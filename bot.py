import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN")
NEWS_CHANNEL_URL = os.getenv("NEWS_CHANNEL_URL")
PRIEMKA_CHANNEL_URL = os.getenv("PRIEMKA_CHANNEL_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Меню"))
    return builder.as_markup(resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        f"👋 Добро пожаловать в MaxUP!\n\n"
        f"<a href=\"{NEWS_CHANNEL_URL}\">Новостной канал</a>\n"
        f"<a href=\"{PRIEMKA_CHANNEL_URL}\">Канал приёмка</a>"
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.message(lambda msg: msg.text == "Меню")
async def menu_stub(message: types.Message):
    await message.answer("Меню в разработке")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
