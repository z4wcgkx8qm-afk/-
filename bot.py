import os
import asyncio
import asyncpg
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import BufferedInputFile

# ================= CONFIG =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
NEWS_CHANNEL_URL = os.getenv("NEWS_CHANNEL_URL", "")
AGREEMENT_URL = os.getenv("AGREEMENT_URL", "")
DATABASE_URL = os.getenv("DATABASE_URL")
BOT_USERNAME = os.getenv("BOT_USERNAME", "maxuprobot").replace("@", "")

MSK = ZoneInfo("Europe/Moscow")

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
            support_msg_id BIGINT,
            support_chat_id BIGINT,
            accepted BOOLEAN DEFAULT FALSE,
            slotted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    for col, col_type in [
        ("sms_code", "TEXT"),
        ("sms_requested", "BOOLEAN DEFAULT FALSE"),
        ("paid_out", "BOOLEAN DEFAULT FALSE"),
        ("support_msg_id", "BIGINT"),
        ("support_chat_id", "BIGINT"),
    ]:
        try:
            await db.execute(f"ALTER TABLE requests ADD COLUMN IF NOT EXISTS {col} {col_type}")
        except:
            pass

    pending = await db.fetch("""
        SELECT id, taken_by, created_at FROM requests
        WHERE status = 'completed' AND accepted = TRUE AND slotted = FALSE AND paid_out = FALSE
    """)

    now = datetime.now(MSK)
    for req in pending:
        payout_time = req["created_at"].replace(tzinfo=ZoneInfo("UTC")).astimezone(MSK) + timedelta(minutes=5)
        delay = (payout_time - now).total_seconds()
        if delay > 0:
            asyncio.create_task(hold_payout(req["id"], req["taken_by"], delay))
        else:
            await process_payout(req["id"], req["taken_by"])


# ================= HELPERS =================
async def ensure_user(user_id: int):
    await db.execute("INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING;", user_id)


async def is_approved_group(group_id: int) -> bool:
    row = await db.fetchrow("SELECT approved FROM groups WHERE group_id = $1", group_id)
    return row is not None and row["approved"]


async def notify_group(req_id: int, text: str, reply_markup=None):
    req = await db.fetchrow("SELECT support_chat_id, support_msg_id FROM requests WHERE id = $1", req_id)
    if not req or not req["support_chat_id"] or not req["support_msg_id"]:
        return
    try:
        await bot.send_message(
            chat_id=req["support_chat_id"],
            text=text,
            reply_to_message_id=req["support_msg_id"],
            reply_markup=reply_markup
        )
    except:
        pass


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


def sms_request_keyboard(req_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Запросить СМС", callback_data=f"smsreq_{req_id}"))
    return builder.as_markup()


def service_keyboard(req_id: int, accepted: bool = False):
    builder = InlineKeyboardBuilder()
    if accepted:
        builder.add(types.InlineKeyboardButton(text="Встал ✅", callback_data="already_accepted"))
    else:
        builder.add(types.InlineKeyboardButton(text="Встал", callback_data=f"accept_{req_id}"))
    builder.add(types.InlineKeyboardButton(text="Ошибка", callback_data=f"error_{req_id}"))
    builder.add(types.InlineKeyboardButton(text="Слет", callback_data=f"slip_{req_id}"))
    return builder.as_markup()


# ================= /start =================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await ensure_user(message.from_user.id)

    args = message.text.split()

    if len(args) > 1 and args[1].startswith("take_"):
        try:
            req_id = int(args[1].split("_")[1])
        except ValueError:
            await message.answer("Неверная ссылка")
            return

        user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)

        if user["active_code_request"] is not None:
            await message.answer("Вы ещё не обработали текущую заявку, завершите её, прежде чем взять новую!")
            return

        req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
        if req is None or req["status"] != "open":
            await message.answer("Упс.. данная заявка уже была принята другим пользователем, попробуйте снова!")
            return

        await db.execute("UPDATE requests SET status = 'taken', taken_by = $1 WHERE id = $2", message.from_user.id, req_id)
        await db.execute("UPDATE users SET active_code_request = $1 WHERE user_id = $2", req_id, message.from_user.id)

        try:
            await bot.delete_message(CHANNEL_ID, req["channel_msg_id"])
        except:
            pass

        await notify_group(req_id, f"Заявка #{req_id} успешно принята, пользователь @{message.from_user.username or 'user'}", cancel_keyboard(req_id))

        await message.answer(
            f"Укажите номер РФ (+7XXXXXXXXXX), который будет привязан к заявке #{req_id}. Таймер — 3 минуты.",
            reply_markup=cancel_keyboard(req_id)
        )

        asyncio.create_task(timeout_request(req_id, message.from_user.id))
        return

    welcome_text = (
        f"Добро пожаловать в MaxUP!\n\n"
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

    await db.execute("INSERT INTO groups (group_id, approved) VALUES ($1, TRUE) ON CONFLICT (group_id) DO UPDATE SET approved = TRUE", group_id)
    await message.answer(f"Группа {group_id} одобрена")


# ================= /reset =================
@dp.message(Command("reset"))
async def cmd_reset(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    args = message.text.split()
    if len(args) != 2:
        await message.reply("Использование: /reset user_id")
        return

    try:
        user_id = int(args[1])
    except ValueError:
        await message.reply("Неверный user_id")
        return

    await db.execute("""
        UPDATE users SET balance = 0, today_earn = 0, total_submitted = 0, total_paid = 0, active_code_request = NULL
        WHERE user_id = $1
    """, user_id)

    await message.reply(f"Профиль пользователя {user_id} обнулён.")


# ================= /state =================
@dp.message(Command("state"))
async def cmd_state(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return

    if message.chat.type == "private":
        return

    if not await is_approved_group(message.chat.id):
        return

    msk_now = datetime.now(MSK)
    today_str = msk_now.strftime("%d.%m")

    users_count = await db.fetchval("SELECT COUNT(*) FROM users")

    stood = await db.fetchval("""
        SELECT COUNT(*) FROM requests
        WHERE (created_at AT TIME ZONE 'Europe/Moscow')::date = $1
        AND status = 'completed' AND accepted = TRUE
    """, msk_now.date())
    errors = await db.fetchval("""
        SELECT COUNT(*) FROM requests
        WHERE (created_at AT TIME ZONE 'Europe/Moscow')::date = $1
        AND status = 'cancelled' AND slotted = FALSE AND accepted = FALSE AND taken_by IS NOT NULL
    """, msk_now.date())
    slips = await db.fetchval("""
        SELECT COUNT(*) FROM requests
        WHERE (created_at AT TIME ZONE 'Europe/Moscow')::date = $1
        AND slotted = TRUE
    """, msk_now.date())
    total_earn = await db.fetchval("""
        SELECT COALESCE(SUM(today_earn), 0) FROM users
    """)

    text = (
        f"📊 Статистика за сегодня ({today_str}):\n"
        f"👥 Пользователей: {users_count}\n"
        f"✅ Встало: {stood}\n"
        f"❌ Ошибок: {errors}\n"
        f"⏱ Слетов: {slips}\n"
        f"💰 Выплачено: {total_earn:.2f} USDT"
    )

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="txt. отчет", callback_data="state_report"))

    await message.reply(text, reply_markup=builder.as_markup())


@dp.callback_query(F.data == "state_report")
async def state_report(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    msk_now = datetime.now(MSK)

    rows = await db.fetch("""
        SELECT u.user_id, r.number
        FROM requests r
        JOIN users u ON u.user_id = r.taken_by
        WHERE (r.created_at AT TIME ZONE 'Europe/Moscow')::date = $1
        AND r.status = 'completed' AND r.accepted = TRUE AND r.paid_out = TRUE
        ORDER BY u.user_id
    """, msk_now.date())

    if not rows:
        await callback.answer("Нет данных за сегодня", show_alert=True)
        return

    # Группировка по юзерам
    grouped = {}
    for row in rows:
        uid = row["user_id"]
        if uid not in grouped:
            try:
                chat = await bot.get_chat(uid)
                name = f"@{chat.username}" if chat.username else f"ID:{uid}"
            except:
                name = f"ID:{uid}"
            grouped[uid] = {"name": name, "numbers": []}
        grouped[uid]["numbers"].append(row["number"])

    lines = []
    for uid, data in grouped.items():
        lines.append(data["name"])
        for num in data["numbers"]:
            lines.append(f"  {num} — 4.20$")
        lines.append("")

    report = "\n".join(lines)

    file = BufferedInputFile(report.encode("utf-8"), filename=f"report_{msk_now.strftime('%d%m')}.txt")

    await callback.message.reply_document(file)
    await callback.answer()


# ================= /code =================
@dp.message(Command("code"))
async def cmd_code(message: types.Message):
    if message.chat.type == "private":
        return

    if not await is_approved_group(message.chat.id):
        return

    req = await db.fetchrow("INSERT INTO requests (format, status) VALUES ('CODE', 'open') RETURNING id")
    req_id = req["id"]

    sent = await bot.send_message(
        CHANNEL_ID,
        f"<b>Срочно нужен номер!</b>\n"
        f"Формат запроса: CODE\n"
        f"Кто первый нажмёт, того и заявка.",
        reply_markup=request_keyboard(req_id)
    )

    reply_msg = await message.reply(f"Заявка #{req_id} создана, ожидайте принятия")

    await db.execute(
        "UPDATE requests SET channel_msg_id = $1, support_chat_id = $2, support_msg_id = $3 WHERE id = $4",
        sent.message_id, reply_msg.chat.id, reply_msg.message_id, req_id
    )


# ================= TIMEOUT =================
async def timeout_request(req_id: int, user_id: int):
    await asyncio.sleep(180)

    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
    if req["status"] != "taken":
        return

    await db.execute("UPDATE requests SET status = 'cancelled' WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", user_id)

    try:
        await bot.send_message(user_id, f"Время вышло, заявка #{req_id} аннулирована.")
    except:
        pass

    await notify_group(req_id, f"Заявка #{req_id} отменена по таймауту.")


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

    if callback.message.chat.type in ("group", "supergroup"):
        try:
            await bot.send_message(req["taken_by"], "Ваша заявка была отклонена администрацией.")
        except:
            pass
        await callback.message.edit_text(f"Заявка #{req_id} отменена администратором.", reply_markup=None)
    else:
        await notify_group(req_id, f"Заявка #{req_id} отменена пользователем.")
        await callback.message.delete()

    await callback.answer()


# ================= USER INPUT =================
@dp.message(F.text, F.chat.type == "private")
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

    if user["active_code_request"] is None:
        return

    req_id = user["active_code_request"]
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] == "taken" and not req["sms_requested"]:
        text = message.text.strip()

        if text == "Меню":
            await message.answer("Вы находитесь в процессе обработки заявки. Завершите её или отмените, прежде чем перейти в меню.")
            return

        if not re.fullmatch(r"(\+7|8|9)\d{10}", text):
            await message.answer("Неверный формат номера. Отправьте номер в формате +7XXXXXXXXXX, 8XXXXXXXXXX или 9XXXXXXXXXX.")
            return

        await db.execute("UPDATE requests SET status = 'number_submitted', number = $1 WHERE id = $2", text, req_id)

        await message.answer(
            f"Номер <code>{text}</code> принят в обработку!\n"
            f"Ожидайте поступления смс (не более 2-х минут)."
        )

        await notify_group(req_id, f"Номер — <code>{text}</code>", sms_request_keyboard(req_id))
        return

    if req["status"] == "number_submitted" and req["sms_requested"]:
        sms = message.text.strip()

        if sms == "Меню":
            await message.answer("Вы находитесь в процессе обработки заявки. Завершите её или отмените, прежде чем перейти в меню.")
            return

        if not re.fullmatch(r"\d{6}", sms):
            await message.answer("Неверный формат отправки СМС, повторите в шестизначном цифровом формате!")
            return

        await db.execute("UPDATE requests SET sms_code = $1, status = 'sms_submitted' WHERE id = $2", sms, req_id)

        await message.answer(
            f"Номер {req['number']} принят в обработку, ожидайте подтверждения от бота."
        )

        await notify_group(req_id, f"СМС-код — <code>{sms}</code>", service_keyboard(req_id))


# ================= SMS REQUEST =================
@dp.callback_query(F.data.startswith("smsreq_"))
async def request_sms(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] != "number_submitted":
        await callback.answer("Невозможно запросить СМС на этом этапе", show_alert=True)
        return

    await db.execute("UPDATE requests SET sms_requested = TRUE WHERE id = $1", req_id)

    try:
        await bot.send_message(
            req["taken_by"],
            f"На номер {req['number']} было отослано СМС, отправьте его ниже!"
        )
    except:
        pass

    await callback.answer("СМС запрошено")
    await callback.message.edit_text(
        callback.message.text + "\n\nСМС запрошено у пользователя.",
        reply_markup=None
    )


# ================= ALREADY ACCEPTED =================
@dp.callback_query(F.data == "already_accepted")
async def already_accepted(callback: types.CallbackQuery):
    await callback.answer("Номер уже встал", show_alert=True)


# ================= SERVICE BUTTONS =================
@dp.callback_query(F.data.startswith("accept_"))
async def accept_number(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] not in ("number_submitted", "sms_submitted", "taken", "completed"):
        await callback.answer("Номер уже неактивен", show_alert=True)
        return

    if req["paid_out"]:
        await callback.answer("Выплата уже произведена", show_alert=True)
        return

    if req["accepted"]:
        await callback.answer("Номер уже встал", show_alert=True)
        return

    await db.execute("UPDATE requests SET accepted = TRUE, slotted = FALSE, status = 'completed' WHERE id = $1", req_id)

    await callback.message.edit_reply_markup(reply_markup=service_keyboard(req_id, accepted=True))

    try:
        await bot.send_message(
            req["taken_by"],
            f"Номер {req['number']} принят в работу! По истечению холда (5 минут) средства будут зачислены на ваш баланс."
        )
    except:
        pass

    await callback.answer("Номер встал")
    asyncio.create_task(hold_payout(req_id, req["taken_by"]))


async def hold_payout(req_id: int, user_id: int, delay: float = 300):
    await asyncio.sleep(delay)
    await process_payout(req_id, user_id)


async def process_payout(req_id: int, user_id: int):
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
    if req and req["accepted"] and not req["slotted"] and req["status"] == "completed" and not req["paid_out"]:
        updated = await db.execute("UPDATE requests SET paid_out = TRUE WHERE id = $1 AND paid_out = FALSE", req_id)
        if updated == "UPDATE 1":
            await db.execute("""
                UPDATE users SET balance = balance + 4.20, today_earn = today_earn + 4.20,
                total_submitted = total_submitted + 1, total_paid = total_paid + 1,
                active_code_request = NULL
                WHERE user_id = $1
            """, user_id)
            try:
                await bot.send_message(
                    user_id,
                    f"На ваш баланс успешно зачислено 4.20 USDT, спасибо за работу!"
                )
            except:
                pass


@dp.callback_query(F.data.startswith("error_"))
async def error_number(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] not in ("number_submitted", "sms_submitted", "taken", "completed"):
        await callback.answer("Номер уже неактивен", show_alert=True)
        return

    await db.execute("UPDATE requests SET status = 'cancelled', accepted = FALSE WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", req["taken_by"])

    try:
        await bot.send_message(
            req["taken_by"],
            f"Номер {req['number']} не встал, произошла непредвиденная ошибка. Повторите попытку позже."
        )
    except:
        pass

    await callback.message.reply(f"Номер {req['number']} больше неактивен, подайте новую заявку.")
    await callback.answer("Ошибка")


@dp.callback_query(F.data.startswith("slip_"))
async def slip_number(callback: types.CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)

    if req["status"] not in ("number_submitted", "sms_submitted", "taken", "completed"):
        await callback.answer("Номер уже неактивен", show_alert=True)
        return

    await db.execute("UPDATE requests SET slotted = TRUE, status = 'cancelled', accepted = FALSE WHERE id = $1", req_id)
    await db.execute("UPDATE users SET active_code_request = NULL WHERE user_id = $1", req["taken_by"])

    try:
        await bot.send_message(
            req["taken_by"],
            f"Номер {req['number']} внезапно слетел. Ожидайте новую заявку!"
        )
    except:
        pass

    await callback.message.reply(f"Номер {req['number']} больше неактивен, подайте новую заявку.")
    await callback.answer("Слет")


# ================= RUN =================
async def main():
    await init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
