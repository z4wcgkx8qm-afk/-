import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN")
NEWS_CHANNEL_URL = os.getenv("NEWS_CHANNEL_URL")
PRIEMKA_CHANNEL_URL = os.getenv("PRIEMKA_CHANNEL_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.add(types.KeyboardButton(text="Меню"))
    return builder.as_markup(resize_keyboard=True)

def get_menu_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Вывод", callback_data="withdraw"))
    builder.add(types.InlineKeyboardButton(text="Меню", callback_data="menu"))
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        f'👋 Добро пожаловать в MaxUP!\n\n'
        f'<a href="{NEWS_CHANNEL_URL}">Новостной канал</a>\n'
        f'<a href="{PRIEMKA_CHANNEL_URL}">Канал приёмка</a>'
    )
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode="HTML")

@dp.message(lambda msg: msg.text == "Меню")
async def menu_handler(message: types.Message):
    user_id = message.from_user.id
    
    text = (
        '<tg-emoji emoji-id="6237594537422758462">🎨</tg-emoji>'
        '<tg-emoji emoji-id="6237595413596087393">🎨</tg-emoji>'
        '<tg-emoji emoji-id="6237880921547086417">🎨</tg-emoji>'
        f' | Личный кабинет\n'
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
    
    await message.answer(text, reply_markup=get_menu_inline_keyboard(), parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "withdraw")
async def withdraw_stub(callback: types.CallbackQuery):
    await callback.answer("Вывод в разработке", show_alert=True)

@dp.callback_query(lambda c: c.data == "menu")
async def menu_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    text = (
        '<tg-emoji emoji-id="6237594537422758462">🎨</tg-emoji>'
        '<tg-emoji emoji-id="6237595413596087393">🎨</tg-emoji>'
        '<tg-emoji emoji-id="6237880921547086417">🎨</tg-emoji>'
        f' | Личный кабинет\n'
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
    
    await callback.message.edit_text(text, reply_markup=get_menu_inline_keyboard(), parse_mode="HTML")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
