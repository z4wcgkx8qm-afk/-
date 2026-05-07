import os
import asyncio
import asyncpg

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


# ================= CONFIG =================
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
GROUP_ID = int(os.getenv("GROUP_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")
CHANNEL_LINK = os.getenv("CHANNEL_LINK")

raw_username = os.getenv("BOT_USERNAME")
if not raw_username:
    raise ValueError("BOT_USERNAME is not set")

BOT_USERNAME = raw_username.replace("@", "")


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
    return await db.fetchrow(
        "SELECT * FROM users WHERE user_id = $1",
        user_id
    )


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
        "<tg-emoji emoji-id='5275979556308674886'>👤</tg-emoji> Ваш профиль:\n\n"
        f"<tg-emoji emoji-id='5278602437001767574'>🔓</tg-emoji> ID Аккаунта: <code>{user_id}</code>\n"
        f"<tg-emoji emoji-id='5278778882848220741'>📊</tg-emoji> Заработано за сегодня: <code>{float(user['today_earn']):.2f}</code> USDT\n"
        f"<tg-emoji emoji-id='5276037216244624892'>💼</tg-emoji> Баланс: <code>{float(user['balance']):.2f}</code> USDT\n"
        f"<tg-emoji emoji-id='5276412364458059956'>🕓</tg-emoji> Статус бота: {settings['status']}"
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
        [
            InlineKeyboardButton(
                text="Сдать номер",
                url=f"https://t.me/{BOT_USERNAME}?start=take_{req_id}"
            )
        ]
    ])


def admin_kb(settings):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Статус: {settings['status']}", callback_data="toggle_status")],
        [InlineKeyboardButton(text=f"Ставка: {settings['rate']}", callback_data="change_rate")],
        [InlineKeyboardButton(text="Назад", callback_data="back")]
    ])


def cancel_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Отменить заявку",
                callback_data=f"cancel_{req_id}"
            )
        ]
    ])


def to_channel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Перейти в канал",
                url=CHANNEL_LINK
            )
        ]
    ])


# ================= START =================
@dp.message(Command("start"))
async def start(message: Message):

    await ensure_user(message.from_user.id)

    args = message.text.split()

    if len(args) > 1 and args[1].startswith("take_"):

        req_id = int(args[1].split("_")[1])

        req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

        if not req:
            await message.answer("❌ Заявка не найдена")
            return

        if req["status"] != "open":
            await message.answer("❌ Уже занято")
            return

        updated = await db.execute("""
            UPDATE requests
            SET status = 'taken',
                taken_by = $2
            WHERE id = $1 AND status = 'open'
        """, req_id, message.from_user.id)

        if updated == "UPDATE 0":
            await message.answer("❌ Уже забрали")
            return

        await bot.delete_message(CHANNEL_ID, req["channel_msg_id"])

        await bot.send_message(
            GROUP_ID,
            f"Принята заявка под номером <code>#{req_id}</code>\n\n"
            f"• Пользователь: @{message.from_user.username or 'user'}\n"
            f"• Формат: <code>CODE</code>",
            reply_markup=cancel_kb(req_id)
        )

        await message.answer("➕ Вы приняли заявку")
        return

    await message.answer(
        await profile_text(message.from_user.id),
        reply_markup=profile_kb(message.from_user.id)
    )


# ================= ADD REQUEST =================
@dp.message(Command("add"))
async def add_request(message: Message):

    if message.chat.id != GROUP_ID:
        return

    req = await db.fetchrow("""
        INSERT INTO requests (channel_msg_id)
        VALUES (0)
        RETURNING id
    """)

    sent = await bot.send_message(
        CHANNEL_ID,
        "<b><tg-emoji emoji-id='5276037216244624892'>💼</tg-emoji> Срочно нужен номер!</b>\n"
        "Кто первый нажмёт, того и заявка",
        reply_markup=request_kb(req["id"])
    )

    await db.execute("""
        UPDATE requests
        SET channel_msg_id = $1
        WHERE id = $2
    """, sent.message_id, req["id"])

    await message.answer("Заявка создана")


# ================= CANCEL REQUEST =================
@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_request(callback: CallbackQuery):

    req_id = int(callback.data.split("_")[1])

    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if not req:
        await callback.answer("Заявка не найдена")
        return

    if not req["taken_by"]:
        await callback.answer("Нет исполнителя")
        return

    await db.execute("""
        UPDATE requests
        SET status = 'cancelled'
        WHERE id = $1
    """, req_id)

    await bot.send_message(
        req["taken_by"],
        f"<tg-emoji emoji-id='5276384644739129761'>🗑</tg-emoji> "
        f"Заявка <code>#{req_id}</code>\n"
        f"Отменена администратором, дождитесь новой",
        reply_markup=to_channel_kb()
    )

    await callback.message.delete()
    await callback.answer("Заявка отменена")


# ================= MAIN =================
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
