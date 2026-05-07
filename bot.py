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

BOT_USERNAME = os.getenv("BOT_USERNAME").replace("@", "")

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

    INSERT INTO settings (id) VALUES (1) ON CONFLICT DO NOTHING;
    """)

    await db.execute("""
    CREATE TABLE IF NOT EXISTS requests (
        id SERIAL PRIMARY KEY,
        channel_msg_id BIGINT,
        status TEXT DEFAULT 'open',
        taken_by BIGINT,
        format TEXT,
        qr_state BOOLEAN DEFAULT FALSE,
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
def request_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сдать номер", url=f"https://t.me/{BOT_USERNAME}?start=take_{req_id}")]
    ])


def cancel_kb(req_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отменить заявку", callback_data=f"cancel_{req_id}")]
    ])


def to_channel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перейти в канал", url=CHANNEL_LINK)]
    ])


# ================= CREATE REQUESTS =================
@dp.message(F.text.lower() == "куар")
async def create_qr(message: Message):
    await create_request(message, "QR")


@dp.message(F.text.lower() == "код")
async def create_code(message: Message):
    await create_request(message, "CODE")


async def create_request(message: Message, fmt: str):

    req = await db.fetchrow("""
        INSERT INTO requests (channel_msg_id, format)
        VALUES (0, $1)
        RETURNING id
    """, fmt)

    sent = await bot.send_message(
        CHANNEL_ID,
        f"<b><tg-emoji emoji-id='5276037216244624892'>💼</tg-emoji> Срочно нужен номер!</b>\n"
        f"Кто первый нажмёт, того и заявка\n"
        f"Формат: {fmt}",
        reply_markup=request_kb(req["id"])
    )

    await db.execute("""
        UPDATE requests
        SET channel_msg_id = $1
        WHERE id = $2
    """, sent.message_id, req["id"])

    await message.answer("Заявка создана")


# ================= TAKE REQUEST =================
@dp.message(Command("start"))
async def start(message: Message):

    await ensure_user(message.from_user.id)

    args = message.text.split()

    if len(args) > 1 and args[1].startswith("take_"):

        req_id = int(args[1].split("_")[1])

        req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

        if not req:
            await message.answer("❌ Не найдена")
            return

        if req["status"] != "open":
            await message.answer("❌ Уже занято")
            return

        await db.execute("""
            UPDATE requests
            SET status = 'taken',
                taken_by = $2
            WHERE id = $1
        """, req_id, message.from_user.id)

        await bot.delete_message(CHANNEL_ID, req["channel_msg_id"])

        text = (
            f"Принята заявка #{req_id}\n"
            f"• Формат: {req['format']}\n"
        )

        if req["format"] == "QR":
            text += "• Ожидайте получения qr со стороны сервиса."

        await bot.send_message(
            GROUP_ID,
            text,
            reply_markup=cancel_kb(req_id)
        )

        # QR FLOW
        if req["format"] == "QR":
            await bot.send_message(
                ADMIN_ID,
                f"Заявка QR #{req_id}\n• Прикрепите QR"
            )

            await db.execute("""
                UPDATE requests SET qr_state = TRUE WHERE id = $1
            """, req_id)

        await message.answer("Принято")
        return


# ================= CANCEL =================
@dp.callback_query(F.data.startswith("cancel_"))
async def cancel(callback: CallbackQuery):

    req_id = int(callback.data.split("_")[1])

    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if not req:
        await callback.answer("Не найдено")
        return

    await db.execute("""
        UPDATE requests SET status = 'cancelled' WHERE id = $1
    """, req_id)

    if req["taken_by"]:
        await bot.send_message(
            req["taken_by"],
            f"<tg-emoji emoji-id='5276384644739129761'>🗑</tg-emoji> "
            f"Заявка #{req_id} отменена администратором",
            reply_markup=to_channel_kb()
        )

    await callback.message.delete()
    await callback.answer("Отменено")


# ================= QR IMAGE HANDLER =================
@dp.message(F.photo)
async def handle_qr(message: Message):

    req = await db.fetchrow("""
        SELECT * FROM requests
        WHERE qr_state = TRUE AND status = 'taken'
        ORDER BY id DESC LIMIT 1
    """)

    if not req:
        return

    await db.execute("""
        UPDATE requests SET qr_state = FALSE WHERE id = $1
    """, req["id"])

    await bot.send_photo(
        req["taken_by"],
        message.photo[-1].file_id,
        caption="⏱ Время на сканирование: 2 минуты",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отменить", callback_data=f"cancel_{req['id']}")]
        ])
    )


# ================= MAIN =================
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
