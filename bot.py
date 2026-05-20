import os
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

def menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="MAX-QR", callback_data="maxqr"))
    builder.add(types.InlineKeyboardButton(text="Вывести средства", callback_data="withdraw"))
    builder.add(types.InlineKeyboardButton(text="Архив", callback_data="archive"))
    builder.adjust(2, 1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    text = (
        '<tg-emoji emoji-id="5206202791768393003">🧭</tg-emoji> Добро пожаловать в сервис GOST!\n'
        '<blockquote>Ваш ID: <code>{user_id}</code>\n'
        'Сдано номеров за все время: <code>0</code>\n\n'
        'Баланс: <code>0.00$</code>\n'
        'Статус бота: В работе</blockquote>'
    )
    await message.answer(text, reply_markup=menu_keyboard())

@dp.callback_query(F.data == "maxqr")
async def maxqr_stub(callback: types.CallbackQuery):
    await callback.answer("В разработке", show_alert=True)

@dp.callback_query(F.data == "withdraw")
async def withdraw_stub(callback: types.CallbackQuery):
    await callback.answer("В разработке", show_alert=True)

@dp.callback_query(F.data == "archive")
async def archive_stub(callback: types.CallbackQuery):
    await callback.answer("В разработке", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
