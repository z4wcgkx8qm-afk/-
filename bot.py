import os
import asyncio
import asyncpg
import pytz

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler


# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GROUP_ID = int(os.getenv("GROUP_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_LINK = os.getenv("CHANNEL_LINK")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()
db: asyncpg.Pool = None


# ================= DB =================
async def init_db():
    global db

    db = await asyncpg.create_pool(DATABASE_URL)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        balance NUMERIC DEFAULT 0,
        today_earn NUMERIC DEFAULT 0
    );
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        id INT PRIMARY KEY,
        rate NUMERIC DEFAULT 4.00,
        status TEXT DEFAULT 'Стартворк'
    );

    INSERT INTO settings (id)
    VALUES (1)
    ON CONFLICT DO NOTHING;
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id SERIAL PRIMARY KEY,
        channel_msg_id BIGINT,
        status TEXT DEFAULT 'open',
        taken_by BIGINT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)


# ================= USERS =================
async def ensure_user(user_id: int):
    await db.execute("""
        INSERT INTO users (user_id)
        VALUES ($1)
        ON CONFLICT DO NOTHING;
    """, user_id)


async def get_user(user_id: int):
    return await db.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


# ================= SETTINGS =================
async def get_settings():
    return await db.fetchrow("SELECT * FROM settings WHERE id = 1")


async def toggle_status():
    await db.execute("""
        UPDATE settings
        SET status = CASE
            WHEN status = 'Стартворк' THEN 'Стопворк'
            ELSE 'Стартворк'
        END
        WHERE id = 1
    """)


async def set_rate(rate: float):
    await db.execute("""
        UPDATE settings
        SET rate = $1
        WHERE id = 1
    """, rate)


# ================= PROFILE =================
async def profile_text(user_id: int):

    user = await get_user(user_id)
    settings = await get_settings()

    user = dict(user or {})
    user.setdefault("balance", 0)
    user.setdefault("today_earn", 0)

    return (
        "<b>👤 Ваш профиль:</b>\n\n"
        f"🔓 ID Аккаунта: <code>{user_id}</code>\n"
        f"📊 Заработано за сегодня: <code>{float(user['today_earn']):.2f}</code> USDT\n"
        f"💼 Баланс: <code>{float(user['balance']):.2f}</code> USDT\n"
        f"🕓 Статус бота: <code>{settings['status']}</code>"
    )


# ================= KEYBOARDS =================
def profile_kb(user_id: int):
    kb = [
        [InlineKeyboardButton(text="Вывести", callback_data="withdraw")]
    ]

    if user_id == ADMIN_ID:
        kb.append([InlineKeyboardButton(text="Настройки", callback_data="admin")])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def request_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Сдать номер",
            callback_data=f"take_{req_id}"
        )]
    ])


def admin_kb(settings):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статус: {settings['status']}", callback_data="toggle_status")],
        [InlineKeyboardButton(text=f"Ставка: {settings['rate']}", callback_data="change_rate")],
        [InlineKeyboardButton(text="Назад", callback_data="back")]
    ])


# ================= START =================
@dp.message(Command("start"))
async def start(message: Message):

    await ensure_user(message.from_user.id)

    await message.answer(
        await profile_text(message.from_user.id),
        reply_markup=profile_kb(message.from_user.id)
    )


# ================= ADD REQUEST =================
@dp.message(Command("add"))
async def add_request(message: Message):

    if message.chat.id != GROUP_ID:
        return

    sent = await bot.send_message(
        CHANNEL_ID,
        "<b>💼 Срочно нужен номер!</b>\n"
        "Кто первый нажмёт, того и заявка"
    )

    req = await db.fetchrow("""
        INSERT INTO requests (channel_msg_id)
        VALUES ($1)
        RETURNING id
    """, sent.message_id)

    await bot.edit_message_reply_markup(
        CHANNEL_ID,
        sent.message_id,
        reply_markup=request_kb(req["id"])
    )

    await message.answer("Заявка создана")


# ================= TAKE REQUEST =================
@dp.callback_query(F.data.startswith("take_"))
async def take_request(callback: CallbackQuery):

    req_id = int(callback.data.split("_")[1])

    req = await db.fetchrow("""
        SELECT * FROM requests WHERE id = $1
    """, req_id)

    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    updated = await db.execute("""
        UPDATE requests
        SET status = 'taken',
            taken_by = $2
        WHERE id = $1 AND status = 'open'
    """, req_id, callback.from_user.id)

    if updated == "UPDATE 0":
        await callback.answer("Уже занято", show_alert=True)
        return

    await bot.delete_message(CHANNEL_ID, req["channel_msg_id"])

    await bot.send_message(
        GROUP_ID,
        f"✅ Заявка принята от: @{callback.from_user.username}"
    )

    await callback.answer("Принято")


# ================= ADMIN =================
@dp.callback_query(F.data == "admin")
async def admin(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        return

    settings = await get_settings()

    await callback.message.edit_text(
        "🔨 Панель администратора",
        reply_markup=admin_kb(settings)
    )


@dp.callback_query(F.data == "toggle_status")
async def toggle(callback: CallbackQuery):

    await toggle_status()
    settings = await get_settings()

    await callback.message.edit_reply_markup(
        reply_markup=admin_kb(settings)
    )


@dp.callback_query(F.data == "change_rate")
async def change_rate(callback: CallbackQuery):

    options = [4.00, 4.25, 4.50, 4.75, 5.00]

    settings = await get_settings()
    current = float(settings["rate"])

    idx = options.index(current)
    new = options[(idx + 1) % len(options)]

    await set_rate(new)

    settings = await get_settings()

    await callback.message.edit_reply_markup(
        reply_markup=admin_kb(settings)
    )


@dp.callback_query(F.data == "back")
async def back(callback: CallbackQuery):

    await callback.message.delete()

    await callback.message.answer(
        await profile_text(callback.from_user.id),
        reply_markup=profile_kb(callback.from_user.id)
    )


@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery):
    await callback.answer("Позже")


# ================= MAIN =================
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
