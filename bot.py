import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message

BOT_TOKEN = "8983059538:AAF1XQEkuwmvYreLN2csBfYrRW8NBQ9pwuc"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message()
async def any_text(msg: Message):
    await msg.answer(f"Получил: {msg.text}")

async def main():
    print("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
