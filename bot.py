import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from pymax import Client

BOT_TOKEN = "ТВОЙ_ТОКЕН_ОТ_BOTFATHER"
ADMIN_ID = 123456789

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

pending = {}

# Шаг 1: ты присылаешь номер (любой текст без команды)
@dp.message(F.text, ~F.text.startswith("/"))
async def phone_handler(msg: Message):
    if msg.from_user.id != ADMIN_ID:
        return

    phone = msg.text.strip()

    if not phone.startswith("+") or len(phone) != 12:
        return await msg.answer("❌ Неверный формат. Пришли: +79161234567")

    status_msg = await msg.answer(f"⏳ Запрашиваю SMS на {phone}...")

    client = Client(phone=phone, work_dir="cache", session_name=f"{phone}.db")

    try:
        await client.request_code()
        pending[msg.from_user.id] = {"client": client, "phone": phone}
        await status_msg.edit_text(f"📩 Код отправлен на {phone}\nПришли его: /code 12345")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

# Шаг 2: ты присылаешь код
@dp.message(Command("code"))
async def code_handler(msg: Message):
    if msg.from_user.id not in pending:
        return await msg.answer("❌ Сначала пришли номер")

    code = msg.text.split()[1] if len(msg.text.split()) > 1 else None
    if not code or not code.isdigit():
        return await msg.answer("❌ Используй: /code 12345")

    data = pending.pop(msg.from_user.id)
    client = data["client"]
    phone = data["phone"]

    status_msg = await msg.answer("⏳ Авторизую...")

    try:
        await client.sign_in(code)
        await status_msg.edit_text(f"✅ Номер {phone} авторизован!\nСессия: cache/{phone}.db")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

async def main():
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
