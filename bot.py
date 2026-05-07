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

BOT_USERNAME = os.getenv("BOT_USERNAME", "").replace("@", "")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
        format TEXT,
        qr_state BOOLEAN DEFAULT FALSE,
        accepted BOOLEAN DEFAULT FALSE,
        slotted BOOLEAN DEFAULT FALSE,
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


# ================= SETTINGS =================
async def get_settings():
    return await db.fetchrow("SELECT * FROM settings WHERE id = 1")


# ================= PROFILE =================
async def profile_text(user_id: int):
    user = await db.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
    settings = await get_settings()

    user = dict(user or {})
    user.setdefault("balance", 0)
    user.setdefault("today_earn", 0)

    return (
        "<b>👤 Профиль</b>\n\n"
        f"ID: <code>{user_id}</code>\n"
        f"Баланс: <code>{float(user['balance']):.2f}</code>\n"
        f"Сегодня: <code>{float(user['today_earn']):.2f}</code>\n"
        f"Статус: {settings['status']}"
    )


# ================= KEYBOARDS =================
def request_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Сдать номер",
            url=f"https://t.me/{BOT_USERNAME}?start=take_{req_id}"
        )]
    ])


def cancel_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отменить", callback_data=f"cancel_{req_id}")]
    ])


def service_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Номер принят", callback_data=f"accept_{req_id}"),
            InlineKeyboardButton(text="СЛЁТ", callback_data=f"slip_{req_id}")
        ]
    ])


# ================= CREATE =================
async def create_request(message: Message, fmt: str):

    req = await db.fetchrow("""
        INSERT INTO requests (format, channel_msg_id)
        VALUES ($1, 0)
        RETURNING id
    """, fmt)

    sent = await bot.send_message(
        CHANNEL_ID,
        f"<b>💼 Срочно нужен номер!</b>\n"
        f"Кто первый нажмёт — тот заберёт\n"
        f"Формат: {fmt}",
        reply_markup=request_kb(req["id"])
    )

    await db.execute("""
        UPDATE requests SET channel_msg_id=$1 WHERE id=$2
    """, sent.message_id, req["id"])

    await message.answer("Заявка создана")


@dp.message(F.text)
async def handle_text(message: Message):

    if not message.text:
        return

    text = message.text.lower().strip()

    if text == "куар":
        await create_request(message, "QR")

    elif text == "код":
        await create_request(message, "CODE")


# ================= TAKE =================
@dp.message(Command("start"))
async def start(message: Message):

    await ensure_user(message.from_user.id)

    args = message.text.split()

    if len(args) > 1 and args[1].startswith("take_"):

        req_id = int(args[1].split("_")[1])

        req = await db.fetchrow("SELECT * FROM requests WHERE id=$1", req_id)

        if not req:
            return await message.answer("Не найдено")

        await db.execute("""
            UPDATE requests SET status='taken', taken_by=$2 WHERE id=$1
        """, req_id, message.from_user.id)

        # группа
        await bot.send_message(
            GROUP_ID,
            f"🕓 Принята заявка #{req_id}\n• Формат: {req['format']}",
            reply_markup=service_kb(req_id)
        )

        # юзер
        await bot.send_message(
            message.from_user.id,
            f"🕓 Принята заявка #{req_id}\nОжидайте QR"
        )


# ================= ACCEPT =================
@dp.callback_query(F.data.startswith("accept_"))
async def accept(call: CallbackQuery):

    req_id = int(call.data.split("_")[1])

    req = await db.fetchrow("SELECT * FROM requests WHERE id=$1", req_id)

    await db.execute("""
        UPDATE requests SET accepted=TRUE WHERE id=$1
    """, req_id)

    await bot.send_message(
        req["taken_by"],
        f"💼 Номер по заявке #{req_id} принят"
    )

    asyncio.create_task(payout(req_id, req["taken_by"]))

    await call.answer()


async def payout(req_id: int, user_id: int):
    await asyncio.sleep(330)

    req = await db.fetchrow("SELECT * FROM requests WHERE id=$1", req_id)

    if not req or req["slotted"]:
        return

    if not req["accepted"]:
        return

    settings = await get_settings()

    await db.execute("""
        UPDATE users SET balance = balance + $1 WHERE user_id=$2
    """, settings["rate"], user_id)


# ================= SLIP =================
@dp.callback_query(F.data.startswith("slip_"))
async def slip(call: CallbackQuery):

    req_id = int(call.data.split("_")[1])

    req = await db.fetchrow("SELECT * FROM requests WHERE id=$1", req_id)

    await db.execute("""
        UPDATE requests SET slotted=TRUE WHERE id=$1
    """, req_id)

    await bot.send_message(
        req["taken_by"],
        "🗑 Номер слетел"
    )

    await call.answer()


# ================= CANCEL =================
@dp.callback_query(F.data.startswith("cancel_"))
async def cancel(call: CallbackQuery):

    await call.message.delete()
    await call.answer()


# ================= QR FROM GROUP =================
@dp.message(F.photo)
async def qr_handler(message: Message):

    req = await db.fetchrow("""
        SELECT * FROM requests
        WHERE qr_state = TRUE AND status='taken'
        ORDER BY id DESC LIMIT 1
    """)

    if not req:
        return

    await db.execute("""
        UPDATE requests SET qr_state=FALSE WHERE id=$1
    """, req["id"])

    await bot.send_photo(
        req["taken_by"],
        message.photo[-1].file_id,
        caption="⏱ 2 минуты на сканирование",
        reply_markup=cancel_kb(req["id"])
    )


# ================= RUN =================
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
