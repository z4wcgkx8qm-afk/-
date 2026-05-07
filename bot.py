import os
import asyncio

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("запущено")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
