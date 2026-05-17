import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN")
NEWS_CHANNEL_URL = os.getenv("NEWS_CHANNEL_URL")
PRIEMKA_CHANNEL_URL = os.getenv("PRIEMKA_CHANNEL_URL")

EMOJI_1 = "6237594537422758462"
EMOJI_2 = "6237595413596087393"
EMOJI_3 = "6237880921547086417"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def build_premium_emoji(emoji_id, fallback):
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Меню"))
    return builder.as_markup(resize_keyboard=True)

def get_withdraw_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Вывод", callback_data="withdraw"))
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        f'👋 Добро пожаловать в MaxUP!\n\n'
        f'<a href="{NEWS_CHANNEL_URL}">Новостной канал</a>\n'
        f'<a href="{PRIEMKA_CHANNEL_URL}">Канал приёмка</a>'
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

@dp.message(lambda msg: msg.text == "Меню")
async def menu_handler(message: types.Message):
    user_id = message.from_user.id
    
    emoji_line = (
        build_premium_emoji(EMOJI_1, "🎨") +
        build_premium_emoji(EMOJI_2, "🎨") +
        build_premium_emoji(EMOJI_3, "🎨")
    )
    
    text = (
        f'{emoji_line} | Личный кабинет\n'
        f'\n'
        f'⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n'
        f'\n'
        f'👤 ID: {user_id}\n'
        f'💳 Баланс: 0.00 USDT\n'
        f'\n'
        f'📊 Ваша статистика:\n'
        f'💰 Заработано сегодня: 0.00 USDT\n'
        f'📱 Всего сдано номеров: 0\n'
        f'✅ Всего оплачено: 0\n'
        f'📈 Конверсия успеха: 0%\n'
        f'\n'
        f'⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n'
        f'\n'
        f'Выберите действие ниже:'
    )
    
    await message.answer(text, reply_markup=get_withdraw_inline_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_stub(callback: types.CallbackQuery):
    await callback.answer("Вывод в разработке", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
