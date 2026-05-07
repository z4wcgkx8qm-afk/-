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
CHANNEL_LINK = os.getenv("CHANNEL_LINK")
DATABASE_URL = os.getenv("DATABASE_URL")


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

    # users table
    await db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY,
        balance NUMERIC DEFAULT 0
    );
    """)

    # 🔥 авто-миграция (ВАЖНО)
    await db.execute("""
    ALTER TABLE users
    ADD COLUMN IF NOT EXISTS today_earn NUMERIC DEFAULT 0;
    """)

    # settings
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


# ================= USERS =================
async def ensure_user(user_id: int):
    await db.execute("""
        INSERT INTO users (user_id)
        VALUES ($1)
        ON CONFLICT DO NOTHING;
    """, user_id)


async def get_user(user_id: int):
    return await db.fetchrow("""
        SELECT * FROM users WHERE user_id = $1
    """, user_id)


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


# ================= SUB CHECK =================
async def is_subscribed(user_id: int):
    member = await bot.get_chat_member(CHANNEL_ID, user_id)
    return member.status in ["member", "administrator", "creator"]


# ================= PROFILE =================
async def profile_text(user_id: int):

    settings = await get_settings()
    user = await get_user(user_id)

    # 🔥 защита от старых записей
    user = dict(user)
    user.setdefault("balance", 0)
    user.setdefault("today_earn", 0)

    return (
        "<b>👤 Ваш профиль</b>\n\n"
        f"🔓 ID: <code>{user_id}</code>\n"
        f"📊 Заработано за сегодня: <code>{user['today_earn']}</code> USDT\n"
        f"💼 Баланс: <code>{user['balance']}</code> USDT\n"
        f"🕓 Статус: {settings['status']}\n"
        f"💰 Ставка: <code>{settings['rate']}</code> USDT"
    )


# ================= KEYBOARDS =================
def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="Проверить", callback_data="check_sub")]
    ])


def profile_keyboard(user_id: int):
    kb = [
        [InlineKeyboardButton(text="Вывести", callback_data="withdraw")]
    ]

    if user_id == ADMIN_ID:
        kb.append([
            InlineKeyboardButton(text="Админ панель", callback_data="admin")
        ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def admin_keyboard(settings):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статус: {settings['status']}", callback_data="toggle_status")],
        [InlineKeyboardButton(text=f"Ставка: {settings['rate']}", callback_data="change_rate")],
        [InlineKeyboardButton(text="Назад", callback_data="back")]
    ])


# ================= RESET DAILY =================
async def reset_daily():
    await db.execute("""
        UPDATE users
        SET today_earn = 0
    """)
    print("Daily reset done")


scheduler = AsyncIOScheduler(timezone=pytz.timezone("Europe/Moscow"))


def start_scheduler():
    scheduler.add_job(reset_daily, "cron", hour=6, minute=0)
    scheduler.start()


# ================= START =================
@dp.message(Command("start"))
async def start(message: Message):

    await ensure_user(message.from_user.id)

    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "🚫 Доступ запрещен!\n\nПодпишитесь на канал",
            reply_markup=sub_keyboard()
        )
        return

    await message.answer(
        await profile_text(message.from_user.id),
        reply_markup=profile_keyboard(message.from_user.id)
    )


# ================= CHECK SUB =================
@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery):

    await ensure_user(callback.from_user.id)

    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()
        await callback.message.answer(
            await profile_text(callback.from_user.id),
            reply_markup=profile_keyboard(callback.from_user.id)
        )
    else:
        await callback.answer("Вы не подписаны", show_alert=True)


# ================= ADMIN =================
@dp.callback_query(F.data == "admin")
async def admin(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        return

    settings = await get_settings()

    await callback.message.edit_text(
        "🔨 Админ панель",
        reply_markup=admin_keyboard(settings)
    )


@dp.callback_query(F.data == "toggle_status")
async def toggle(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        return

    await toggle_status()

    settings = await get_settings()

    await callback.message.edit_reply_markup(
        reply_markup=admin_keyboard(settings)
    )


@dp.callback_query(F.data == "change_rate")
async def change_rate(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        return

    options = [4.00, 4.25, 4.50, 4.75, 5.00]

    settings = await get_settings()
    current = float(settings["rate"])

    idx = options.index(current)
    new_rate = options[(idx + 1) % len(options)]

    await set_rate(new_rate)

    settings = await get_settings()

    await callback.message.edit_reply_markup(
        reply_markup=admin_keyboard(settings)
    )


@dp.callback_query(F.data == "back")
async def back(callback: CallbackQuery):

    await callback.message.delete()

    await callback.message.answer(
        await profile_text(callback.from_user.id),
        reply_markup=profile_keyboard(callback.from_user.id)
    )


@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery):
    await callback.answer("Функция позже")


# ================= MAIN =================
async def main():
    await init_db()
    start_scheduler()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
