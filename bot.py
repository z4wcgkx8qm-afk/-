import os
import asyncio
import asyncpg
import re
import httpx
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
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN", "")

MSK = ZoneInfo("Europe/Moscow")
CRYPTO_API = "https://pay.crypt.bot/api"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
db: asyncpg.Pool = None
http_client: httpx.AsyncClient = None


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
            active_code_request INT,
            active_qr_request INT,
            last_earn_date DATE
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
            qr_await_msg_id BIGINT,
            accepted BOOLEAN DEFAULT FALSE,
            slotted BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    for table, cols in {
        "users": [
            ("last_earn_date", "DATE"),
            ("active_qr_request", "INT"),
        ],
        "requests": [
            ("sms_code", "TEXT"),
            ("sms_requested", "BOOLEAN DEFAULT FALSE"),
            ("paid_out", "BOOLEAN DEFAULT FALSE"),
            ("support_msg_id", "BIGINT"),
            ("support_chat_id", "BIGINT"),
            ("qr_await_msg_id", "BIGINT"),
        ],
    }.items():
        for col, col_type in cols:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {col_type}")
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


# ================= CRYPTO API =================
async def crypto_request(method: str, params: dict = None) -> dict:
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    url = f"{CRYPTO_API}/{method}"
    resp = await http_client.post(url, headers=headers, json=params or {})
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise Exception(data.get("error", "Unknown error"))
    return data["result"]


async def crypto_create_invoice(amount: float, description: str = "") -> dict:
    return await crypto_request("createInvoice", {
        "asset": "USDT",
        "amount": str(amount),
        "description": description
    })


async def crypto_transfer(amount: float, user_id: int, spend_id: str) -> dict:
    return await crypto_request("transfer", {
        "asset": "USDT",
        "amount": str(amount),
        "user_id": user_id,
        "spend_id": spend_id
    })


async def crypto_delete_check(check_id: int) -> dict:
    return await crypto_request("deleteCheck", {"check_id": check_id})


async def crypto_get_balance() -> float:
    balances = await crypto_request("getBalance")
    for b in balances:
        if b["currency_code"] == "USDT":
            return float(b["available"])
    return 0.0


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
        return await bot.send_message(
            chat_id=req["support_chat_id"],
            text=text,
            reply_to_message_id=req["support_msg_id"],
            reply_markup=reply_markup
        )
    except:
        pass


def require_approved_group(func):
    async def wrapper(message: types.Message):
        if message.chat.type == "private":
            return
        if not await is_approved_group(message.chat.id):
            return
        return await func(message)
    return wrapper


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
@dp.message(Command("start"), F.chat.type == "private")
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

        if user["active_code_request"] is not None or user["active_qr_request"] is not None:
            await message.answer("Вы ещё не обработали текущую заявку, завершите её, прежде чем взять новую!")
            return

        req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
        if req is None or req["status"] != "open":
            await message.answer("Упс.. данная заявка уже была принята другим пользователем, попробуйте снова!")
            return

        is_qr = req["format"] == "QR"

        if is_qr:
            await db.execute("UPDATE requests SET status = 'taken', taken_by = $1 WHERE id = $2", message.from_user.id, req_id)
            await db.execute("UPDATE users SET active_qr_request = $1 WHERE user_id = $2", req_id, message.from_user.id)
        else:
            await db.execute("UPDATE requests SET status = 'taken', taken_by = $1 WHERE id = $2", message.from_user.id, req_id)
            await db.execute("UPDATE users SET active_code_request = $1 WHERE user_id = $2", req_id, message.from_user.id)

        try:
            await bot.delete_message(CHANNEL_ID, req["channel_msg_id"])
        except:
            pass

        if is_qr:
            sent_msg = await notify_group(
                req_id,
                f"Заявка #{req_id} успешно принята, пользователь @{message.from_user.username or 'user'}\nОтправьте ниже QR ответом на это сообщение",
                cancel_keyboard(req_id)
            )
            if sent_msg:
                await db.execute("UPDATE requests SET qr_await_msg_id = $1 WHERE id = $2", sent_msg.message_id, req_id)

            await message.answer(
                f"Вы приняли заявку #{req_id}\nОжидайте получения QR (займет не больше 2-х минут)",
                reply_markup=cancel_keyboard(req_id)
            )
        else:
            await notify_group(
                req_id,
                f"Заявка #{req_id} успешно принята, пользователь @{message.from_user.username or 'user'}",
                cancel_keyboard(req_id)
            )
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
@dp.message(F.text == "Меню", F.chat.type == "private")
async def menu_handler(message: types.Message):
    await ensure_user(message.from_user.id)

    msk_now = datetime.now(MSK)

    last_earn_date = await db.fetchval("SELECT last_earn_date FROM users WHERE user_id = $1", message.from_user.id)
    if last_earn_date is None or last_earn_date < msk_now.date():
        await db.execute("UPDATE users SET today_earn = 0, last_earn_date = $1 WHERE user_id = $2", msk_now.date(), message.from_user.id)

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


# ================= WITHDRAW =================
@dp.callback_query(F.data == "withdraw")
async def withdraw_start(callback: types.CallbackQuery):
    user = await db.fetchrow("SELECT balance FROM users WHERE user_id = $1", callback.from_user.id)

    if user["balance"] <= 0:
        await callback.answer("Ваш баланс пуст", show_alert=True)
        return

    await callback.message.answer(
        "Укажите сумму для вывода в USDT.\n"
        "Средства поступят на ваш кошелёк моментально."
    )
    await callback.answer()


@dp.message(F.text, F.chat.type == "private")
async def handle_withdraw_amount(message: types.Message):
    user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", message.from_user.id)

    if user["active_code_request"] is not None or user["active_qr_request"] is not None:
        await handle_message(message)
        return

    try:
        amount = float(message.text.strip().replace(",", "."))
    except ValueError:
        return

    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return

    if amount > user["balance"]:
        await message.answer("Недостаточно средств на балансе.")
        return

    try:
        spend_id = f"wd_{message.from_user.id}_{int(datetime.now().timestamp())}"
        await crypto_transfer(amount, message.from_user.id, spend_id)

        await db.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", amount, message.from_user.id)

        await message.answer(
            f"✅ Вывод на {amount:.2f} USDT успешно выполнен. Средства зачислены на ваш кошелёк."
        )
    except Exception as e:
        await message.answer(f"Ошибка при выводе: {e}")


# ================= /help =================
@dp.message(Command("help"))
@require_approved_group
async def cmd_help(message: types.Message):
    text = (
        "Доступные команды:\n\n"
        "/code — создать заявку CODE (4.20$)\n"
        "/qr — создать заявку QR (4.50$)\n"
        "/set сумма — пополнить баланс бота через инвойс\n"
        "/state — статистика за сегодня + баланс бота\n"
        "/reset user_id — обнулить профиль пользователя\n"
        "/delcheck check_id — удалить чек и вернуть средства\n"
        "/help — список команд"
    )
    await message.reply(text)


# ================= /set =================
@dp.message(Command("set"))
@require_approved_group
async def cmd_set(message: types.Message):
    args = message.text.split()
    if len(args) != 2:
        await message.reply("Использование: /set сумма")
        return

    try:
        amount = float(args[1].replace(",", "."))
    except ValueError:
        await message.reply("Неверная сумма")
        return

    if amount <= 0:
        await message.reply("Сумма должна быть больше нуля")
        return

    try:
        result = await crypto_create_invoice(amount, "Пополнение баланса бота MAXup")
        await message.reply(
            f"Счёт на {amount} USDT создан.\n"
            f"Оплатите по ссылке:\n"
            f"{result['pay_url']}"
        )
    except Exception as e:
        await message.reply(f"Ошибка при создании счёта: {e}")


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
@require_approved_group
async def cmd_reset(message: types.Message):
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
        UPDATE users SET balance = 0, today_earn = 0, total_submitted = 0, total_paid = 0,
        active_code_request = NULL, active_qr_request = NULL
        WHERE user_id = $1
    """, user_id)

    await message.reply(f"Профиль пользователя {user_id} обнулён.")


# ================= /delcheck =================
@dp.message(Command("delcheck"))
@require_approved_group
async def cmd_delcheck(message: types.Message):
    args = message.text.split()
    if len(args) != 2:
        await message.reply("Использование: /delcheck check_id")
        return

    try:
        check_id = int(args[1])
    except ValueError:
        await message.reply("Неверный check_id")
        return

    try:
        await crypto_delete_check(check_id)
        await message.reply(f"Чек {check_id} удалён. Средства возвращены на баланс.")
    except Exception as e:
        await message.reply(f"Ошибка при удалении чека: {e}")


# ================= /state =================
@dp.message(Command("state"))
@require_approved_group
async def cmd_state(message: types.Message):
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

    crypto_balance = await crypto_get_balance()

    text = (
        f"📊 Статистика за сегодня ({today_str}):\n"
        f"👥 Пользователей: {users_count}\n"
        f"✅ Встало: {stood}\n"
        f"❌ Ошибок: {errors}\n"
        f"⏱ Слетов: {slips}\n"
        f"💰 Выплачено: {total_earn:.2f} USDT\n"
        f"🏦 Баланс бота: {crypto_balance:.2f} USDT"
    )

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="txt. отчет", callback_data="state_report"))

    await message.reply(text, reply_markup=builder.as_markup())


@dp.callback_query(F.data == "state_report")
async def state_report(callback: types.CallbackQuery):
    msk_now = datetime.now(MSK)

    rows = await db.fetch("""
        SELECT u.user_id, r.number, r.format
        FROM requests r
        JOIN users u ON u.user_id = r.taken_by
        WHERE (r.created_at AT TIME ZONE 'Europe/Moscow')::date = $1
        AND r.status = 'completed' AND r.accepted = TRUE AND r.paid_out = TRUE
        ORDER BY u.user_id
    """, msk_now.date())

    if not rows:
        await callback.answer("Нет данных за сегодня", show_alert=True)
        return

    grouped = {}
    for row in rows:
        uid = row["user_id"]
        amount = "4.50$" if row["format"] == "QR" else "4.20$"
        if uid not in grouped:
            try:
                chat = await bot.get_chat(uid)
                name = f"@{chat.username}" if chat.username else f"ID:{uid}"
            except:
                name = f"ID:{uid}"
            grouped[uid] = {"name": name, "numbers": []}
        grouped[uid]["numbers"].append(f"{row['number']} — {amount}")

    lines = []
    for uid, data in grouped.items():
        lines.append(data["name"])
        for num in data["numbers"]:
            lines.append(f"  {num}")
        lines.append("")

    report = "\n".join(lines)

    file = BufferedInputFile(report.encode("utf-8"), filename=f"report_{msk_now.strftime('%d%m')}.txt")

    await callback.message.reply_document(file)
    await callback.answer()


# ================= /code =================
@dp.message(Command("code"))
@require_approved_group
async def cmd_code(message: types.Message):
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


# ================= /qr =================
@dp.message(Command("qr"))
@require_approved_group
async def cmd_qr(message: types.Message):
    req = await db.fetchrow("INSERT INTO requests (format, status) VALUES ('QR', 'open') RETURNING id")
    req_id = req["id"]

    sent = await bot.send_message(
        CHANNEL_ID,
        f"<b>Срочно нужен номер!</b>\n"
        f"Формат запроса: QR\n"
        f"Кто первый нажмёт, того и заявка.",
        reply_markup=request_keyboard(req_id)
    )

    reply_msg = await message.reply(f"Заявка #{req_id} создана, ожидайте принятия")

    await db.execute(
        "UPDATE requests SET channel_msg_id = $1, support_chat_id = $2, support_msg_id = $3 WHERE id = $4",
        sent.message_id, reply_msg.chat.id, reply_msg.message_id, req_id
    )


# ================= QR PHOTO HANDLER =================
@dp.message(F.photo, F.chat.type.in_(["group", "supergroup"]))
async def handle_qr_photo(message: types.Message):
    if message.reply_to_message:
        replied_msg_id = message.reply_to_message.message_id

        req = await db.fetchrow("""
            SELECT * FROM requests
            WHERE format = 'QR' AND status = 'taken' AND sms_requested = FALSE AND qr_await_msg_id = $1
        """, replied_msg_id)

        if req:
            req_id = req["id"]
            file_id = message.photo[-1].file_id

            await db.execute("UPDATE requests SET number = 'QR', sms_requested = TRUE WHERE id = $1", req_id)

            await message.reply(
                f"Заявка #{req_id}\nQR отправлен",
                reply_markup=service_keyboard(req_id)
            )

            try:
                await bot.send_photo(
                    req["taken_by"],
                    file_id,
                    caption=f"Ваш QR для авторизации:\n\nНа сканирование данного QR у вас ровно две минуты, после чего он истечет.",
                    reply_markup=cancel_keyboard(req_id)
                )
            except:
                pass
            return

    req = await db.fetchrow("""
        SELECT id FROM requests
        WHERE format = 'QR' AND status = 'taken' AND sms_requested = FALSE AND support_chat_id = $1
        ORDER BY id DESC LIMIT 1
    """, message.chat.id)

    if req:
        await message.reply("Для прикрепления QR отправьте его ответом на сообщение с просьбой прикрепить QR!")


# ================= TIMEOUT (CODE only) =================
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

    is_qr = req["format"] == "QR"

    await db.execute("UPDATE requests SET status = 'cancelled' WHERE id = $1", req_id)
    if is_qr:
        await db.execute("UPDATE users SET active_qr_request = NULL WHERE user_id = $1", req["taken_by"])
    else:
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


# ================= USER INPUT (CODE/SMS) =================
@dp.message(F.text, F.chat.type == "private")
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user = await db.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

    if user["active_code_request"] is not None:
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

    amount = 4.50 if req["format"] == "QR" else 4.20
    asyncio.create_task(hold_payout(req_id, req["taken_by"], amount))


async def hold_payout(req_id: int, user_id: int, amount: float, delay: float = 300):
    await asyncio.sleep(delay)
    await process_payout(req_id, user_id, amount)


async def process_payout(req_id: int, user_id: int, amount: float):
    req = await db.fetchrow("SELECT * FROM requests WHERE id = $1", req_id)
    if req and req["accepted"] and not req["slotted"] and req["status"] == "completed" and not req["paid_out"]:
        updated = await db.execute("UPDATE requests SET paid_out = TRUE WHERE id = $1 AND paid_out = FALSE", req_id)
        if updated == "UPDATE 1":
            await db.execute(f"""
                UPDATE users SET balance = balance + {amount}, today_earn = today_earn + {amount},
                total_submitted = total_submitted + 1, total_paid = total_paid + 1,
                active_code_request = NULL, active_qr_request = NULL
                WHERE user_id = $1
            """, user_id)
            try:
                await bot.send_message(
                    user_id,
                    f"На ваш баланс успешно зачислено {amount:.2f} USDT, спасибо за работу!"
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

    is_qr = req["format"] == "QR"

    await db.execute("UPDATE requests SET status = 'cancelled', accepted = FALSE WHERE id = $1", req_id)
    if is_qr:
        await db.execute("UPDATE users SET active_qr_request = NULL WHERE user_id = $1", req["taken_by"])
    else:
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

    is_qr = req["format"] == "QR"

    await db.execute("UPDATE requests SET slotted = TRUE, status = 'cancelled', accepted = FALSE WHERE id = $1", req_id)
    if is_qr:
        await db.execute("UPDATE users SET active_qr_request = NULL WHERE user_id = $1", req["taken_by"])
    else:
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
    global http_client
    http_client = httpx.AsyncClient(timeout=30.0)
    try:
        await init_db()
        await dp.start_polling(bot)
    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
