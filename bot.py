import os
import asyncio
import asyncpg
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
NEWS_CHANNEL_URL = os.getenv("NEWS_CHANNEL_URL", "")
AGREEMENT_URL = os.getenv("AGREEMENT_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME", "maxuprobot").replace("@", "")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db: asyncpg.Pool = None


# ================= DB =================
async def init_db():
    global db
    db = await asyncpg.create_pool(DATABASE_URL)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            group_id BIGINT PRIMARY KEY,
            approved BOOLEAN DEFAULT FALSE
        );
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            balance NUMERIC DEFAULT 0,
            today_earn NUMERIC DEFAULT 0,
            total_submitted INT DEFAULT 0,
            total_paid INT DEFAULT 0,
            active_code_request INT
        );
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS requests (
            id SERIAL PRIMARY KEY,
            format TEXT DEFAULT 'CODE',
            status TEXT DEFAULT 'open',
            taken_by BIGINT,
            number TEXT,
            channel_msg_id BIGINT,
            group_msg_id BIGINT,
            accepted BOOLEAN DEFAULT FALSE,
            slotted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # Восстановление холдов после перезапуска
    pending = await db.fetch("""
        SELECT id, taken_by, created_at FROM requests
        WHERE status = 'completed' AND accepted = TRUE AND slotted = FALSE
    """)

    now = datetime.now()
    for req in pending:
        payout_time = req["created_at"] + timedelta(minutes=5)
        delay = (payout_time - now).total_seconds()
        if delay > 0:
            asyncio.create_task(hold_payout(req["id"], req["taken_by"], delay))
        else:
            await process_payout(req["id"], req["taken_by"])


# ================= HELPERS =================
async def ensure_user(user_id: int):
    await db.execute("""
        INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING;
    """, user_id)


async def is_approved_group(group_id: int) -> bool:
    row = await db.fetchrow("SELECT approved FROM groups WHERE group_id = $1", group_id)
    return row is not None and row["approved"]


# ================= KEYBOARDS =================
def main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Меню"))
    return builder.as_markup(resize_keyboard=True)


def withdraw_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Вывод", callback_data="withdraw"))
    return builder.as_markup()


def request_keyboard(req_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Сдать номер",
        url=f"https://t.me/{BOT_USERNAME}?start=take_{req_id}"
    ))
    return builder.as_markup()


def cancel_keyboard(req_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Отменить", callback_data=f"cancel_{req_id}"))
    return builder.as_markup()


def service_keyboard(req_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Встал", callback_data=f"accept_{req_id}"))
    builder.add(types.InlineKeyboardButton(text="Ошибка", callback_data=f"error_{req_id}"))
    builder.add(types.InlineKeyboardButton(text="Слет", callback_data=f"slip_{req_id}"))
    return builder.as_markup()


# ================= /start =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await ensure_user(message.from_user.id)

    args = message.text.split()

    # Диплинк с заявкой
    if len(args) > 1 and args[1].startswith("take_"):
        try:
            req_id = int(args[1].split("_")[1])
        except ValueError:
            await message.answer("Неверная ссылка")
            return

        user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)

        if user["active_code_request"] is not None:
            await message.answer("❌ Вы ещё не обработали текущую заявку, завершите её, прежде чем взять новую!")
            return

        req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
        if req is None or req["status"] != "open":
            await message.answer("Упс.. данная заявка уже была принята другим пользователем, попробуйте снова!")
            return

        # Берём заявку
        await db.execute("""
            UPDATE requests SET status = 'taken', taken_by = $1 WHERE id = $2
        """, message.from_user.id, req_id)
        await db.execute("UPDATE users SET active_code_request = $1 WHERE user_id = $2", req_id, message.from_user.id)

        # Удаляем сообщение в канале
        try:
            await bot.delete_message(CHANNEL_ID, req["channel_msg_id"])
        except:
            pass

        # Саппорту в группу
        for group in await db.fetch("SELECT group_id FROM groups WHERE approved = TRUE"):
            try:
                await bot.send_message(
                    group["group_id"],
                    f"Заявка #{req_id} успешно принята, пользователь @{message.from_user.username or 'user'}",
                    reply_markup=cancel_keyboard(req_id)
                )
            except:
                pass

        # Пользователю
        await message.answer(
            f"Укажите номер РФ (+7XXXXXXXXXX), который будет привязан к заявке #{req_id}. Таймер — 3 минуты.",
            reply_markup=cancel_keyboard(req_id)
        )

        # Таймер 3 минуты
        asyncio.create_task(timeout_request(req_id, message.from_user.id))
        return

    # Обычный старт
    welcome_text = (
        f"👋 Добро пожаловать в MaxUP!\n\n"
        f"<a href='{NEWS_CHANNEL_URL}'>Новостной канал</a>\n"
        f"<a href='{AGREEMENT_URL}'>Пользовательское соглашение</a>"
    )
    await message.answer(welcome_text, reply_markup=main_keyboard())


# ================= МЕНЮ =================
@dp.message(F.text == "Меню")
async def menu_handler(message: types.Message):
    await ensure_user(message.from_user.id)
    user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)
    user_id = user["user_id"]

    submitted = user["total_submitted"]
    paid = user["total_paid"]
    conversion = int(paid / submitted * 100) if submitted > 0 else 0

    text = (
        "<tg-emoji emoji-id='6237594537422758462'>🎨</tg-emoji>"
        "<tg-emoji emoji-id='6237595413596087393'>🎨</tg-emoji>"
        "<tg-emoji emoji-id='6237880921547086417'>🎨</tg-emoji>"
        f" | Личный кабинет\n"
        f"\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"\n"
        f"👤 ID: {user_id}\n"
        f"💳 Баланс: {user['balance']:.2f} USDT\n"
        f"\n"
        f"📊 Ваша статистика:\n"
        f"💰 Заработано сегодня: {user['today_earn']:.2f} USDT\n"
        f"📱 Всего сдано номеров: {submitted}\n"
        f"✅ Всего оплачено: {paid}\n"
        f"📈 Конверсия успеха: {conversion}%\n"
        f"\n"
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"\n"
        f"Выберите действие ниже:"
    )
    await message.answer(text, reply_markup=withdraw_keyboard())


@dp.callback_query(F.data == "withdraw")
async def withdraw_stub(callback: types.CallbackQuery):
    await callback.answer("Вывод в разработке", show_alert=True)


# ================= /setup =================
@dp.message(Command("setup"))
async def cmd_setup(message: types.Message):
    if message.from_user.id != ADMIN_ID or message.chat.type == "private":
        return

    args = message.text.split()
    if len(args) != 2:
        return

    try:
        group_id = int(args[1])
    except ValueError:
        return

    await db.execute("""
        INSERT INTO groups (group_id, approved) VALUES ($1, TRUE)
        ON CONFLICT (group_id) DO UPDATE SET approved = TRUE
    """, group_id)

    await message.answer(f"Группа {group_id} одобрена")


# ================= /code =================
@dp.message(Command("code"))
async def cmd_code(message: types.Message):
    if message.chat.type == "private":
        return

    if not await is_approved_group(message.chat.id):
        return

    req = await db.fetchrow("""
        INSERT INTO requests (format, status) VALUES ('CODE', 'open') RETURNING id
    """)
    req_id = req["id"]

    sent = await bot.send_message(
        CHANNEL_ID,
        f"<b>🔥 Срочно нужен номер!</b>\n"
        f"Формат запроса: CODE\n"
        f"<i>⏳ Кто первый нажмёт, того и заявка.</i>",
        reply_markup=request_keyboard(req_id)
    )

    await db.execute("UPDATE requests SET channel_msg_id = $1 WHERE id = $2", sent.message_id, req_id)

    await message.answer(f"Заявка #{req_id} создана, ожидайте принятия")


# ================= TIMEOUT =================
async def timeout_request(req_id: int, user_id: int):
    await asyncio.sleep(180)

    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
    if req["status"] != "taken":
        return

    await db.execute("UPDATE requests SET status = 'cancelled' WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", user_id)

    try:
        await bot.send_message(user_id, f"⏰ Время вышло, заявка #{req_id} аннулирована. Не успев ввести номер вовремя, вы можете попробовать взять новую заявку.")
    except:
        pass

    for group in await db.fetch("SELECT group_id FROM groups WHERE approved = TRUE"):
        try:
            await bot.send_message(group["group_id"], f"⏰ Заявка #{req_id} отменена по таймауту — пользователь не отправил номер в течение 3 минут.")
        except:
            pass


# ================= CANCEL =================
@dp.callback_query(F.data.startswith("cancel_"))
async def cancel_request(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])

    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
    if req["status"] in ("cancelled", "completed"):
        await callback.answer("Заявка уже неактивна", show_alert=True)
        return

    await db.execute("UPDATE requests SET status = 'cancelled' WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", req["taken_by"])

    # Саппорт отменил (сообщение из группы)
    if callback.message.chat.type in ("group", "supergroup"):
        try:
            await bot.send_message(req["taken_by"], "Ваша заявка была отклонена администрацией. Пожалуйста, ожидайте новую заявку.")
        except:
            pass
        await callback.message.edit_text(f"Заявка #{req_id} отменена администратором.", reply_markup=None)
    else:
        # Пользователь отменил — удаляем сообщение
        for group in await db.fetch("SELECT group_id FROM groups WHERE approved = TRUE"):
            try:
                await bot.send_message(group["group_id"], f"Заявка #{req_id} отменена пользователем.")
            except:
                pass
        await callback.message.delete()

    await callback.answer()


# ================= USER INPUT NUMBER =================
@dp.message(F.text, F.chat.type == "private")
async def handle_number(message: types.Message):
    user_id = message.from_user.id
    user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

    if user["active_code_request"] is None:
        return

    req_id = user["active_code_request"]
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] != "taken":
        return

    number = message.text.strip()

    await db.execute("UPDATE requests SET status = 'number_submitted', number = $1 WHERE id = $2", number, req_id)

    # Пользователю
    await message.answer(
        f"⏳ Номер <code>{number}</code> принят в обработку!\n"
        f"Ожидайте поступления смс (не более 2-х минут).\n"
        f"Если код не придёт, заявка будет отменена автоматически."
    )

    # Саппорту в группу
    for group in await db.fetch("SELECT group_id FROM groups WHERE approved = TRUE"):
        try:
            await bot.send_message(
                group["group_id"],
                f"Номер — <code>{number}</code>",
                reply_markup=service_keyboard(req_id)
            )
        except:
            pass


# ================= SERVICE BUTTONS =================
@dp.callback_query(F.data.startswith("accept_"))
async def accept_number(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] not in ("number_submitted", "taken", "completed"):
        await callback.answer("Номер уже неактивен", show_alert=True)
        return

    await db.execute("UPDATE requests SET accepted = TRUE, slotted = FALSE, status = 'completed' WHERE id = $1", req_id)

    try:
        await bot.send_message(
            req["taken_by"],
            f"⚖️ Номер {req['number']} принят в работу! По истечению холда (5 минут) средства будут автоматически зачислены на ваш баланс."
        )
    except:
        pass

    await callback.answer("Номер встал")

    # Холд 5 минут
    asyncio.create_task(hold_payout(req_id, req["taken_by"]))


async def hold_payout(req_id: int, user_id: int, delay: float = 300):
    await asyncio.sleep(delay)
    await process_payout(req_id, user_id)


async def process_payout(req_id: int, user_id: int):
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
    if req and req["accepted"] and not req["slotted"] and req["status"] == "completed":
        await db.execute("""
            UPDATE users SET balance = balance + 4.20, today_earn = today_earn + 4.20,
            total_submitted = total_submitted + 1, total_paid = total_paid + 1,
            active_code_request = NULL
            WHERE user_id = $1
        """, user_id)


@dp.callback_query(F.data.startswith("error_"))
async def error_number(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] not in ("number_submitted", "taken", "completed"):
        await callback.answer("Номер уже неактивен", show_alert=True)
        return

    await db.execute("UPDATE requests SET status = 'cancelled', accepted = FALSE WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", req["taken_by"])

    try:
        await bot.send_message(
            req["taken_by"],
            f"❌ Номер {req['number']} не встал, произошла непредвиденная ошибка. Повторите попытку позже или дождитесь новой заявки."
        )
    except:
        pass

    await callback.message.reply(f"❌ Номер {req['number']} больше неактивен, подайте новую заявку.")
    await callback.answer("Ошибка")


@dp.callback_query(F.data.startswith("slip_"))
async def slip_number(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] not in ("number_submitted", "taken", "completed"):
        await callback.answer("Номер уже неактивен", show_alert=True)
        return

    await db.execute("UPDATE requests SET slotted = TRUE, status = 'cancelled', accepted = FALSE WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", req["taken_by"])

    try:
        await bot.send_message(
            req["taken_by"],
            f"⏱️ Номер {req['number']} внезапно слетел. Ожидайте новую заявку, мы сообщим!"
        )
    except:
        pass

    await callback.message.reply(f"❌ Номер {req['number']} больше неактивен, подайте новую заявку.")
    await callback.answer("Слет")


# ================= RUN =================
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
