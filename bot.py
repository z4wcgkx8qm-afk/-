import os
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

pending_approvals = {}
approved_users = set()

def menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="MAX-QR", callback_data="maxqr"))
    builder.add(types.InlineKeyboardButton(text="Вывести средства", callback_data="withdraw"))
    builder.add(types.InlineKeyboardButton(text="Архив", callback_data="archive"))
    builder.adjust(2, 1)
    return builder.as_markup()

def admin_keyboard(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Одобрить", callback_data=f"approve_{user_id}"))
    builder.add(types.InlineKeyboardButton(text="Запретить", callback_data=f"reject_{user_id}"))
    return builder.as_markup()

def archive_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Моя статистика", callback_data="my_stats"))
    builder.add(types.InlineKeyboardButton(text="Общая статистика", callback_data="global_stats"))
    builder.add(types.InlineKeyboardButton(text="Назад", callback_data="back_to_menu"))
    builder.adjust(1)
    return builder.as_markup()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    if user_id == ADMIN_ID:
        approved_users.add(user_id)

    if user_id in approved_users:
        text = (
            f'<tg-emoji emoji-id="5276220667182736079">📥</tg-emoji> Добро пожаловать в бота QR scan GOST!\n'
            f'<blockquote>┌ Ваш ID: <code>{user_id}</code>\n'
            f'├ Актуальный прайс: <code>4.4$</code>\n'
            f'├ Общая очередь: <code>0</code>\n'
            f'├ Баланс: <code>0.00$</code>\n'
            f'└ <tg-emoji emoji-id="5215670591905869044">🟢</tg-emoji> Статус бота: В работе</blockquote>'
        )
        await message.answer(text, reply_markup=menu_keyboard())
        return

    if user_id in pending_approvals:
        await message.answer('<tg-emoji emoji-id="5206626000665868017">📚</tg-emoji> Ваша заявка уже отправлена на рассмотрение администрации, ожидайте подтверждения.')
        return

    pending_approvals[user_id] = True

    await message.answer('<tg-emoji emoji-id="5206626000665868017">📚</tg-emoji> Ваша заявка отправлена на рассмотрение администрации, ожидайте подтверждения.')

    try:
        await bot.send_message(
            ADMIN_ID,
            f'<tg-emoji emoji-id="5206626000665868017">📚</tg-emoji> Уведомление о новой заявке, пользователь @{message.from_user.username or "user"}, отправил запрос на подтверждение использования бота.',
            reply_markup=admin_keyboard(user_id)
        )
    except:
        pass

@dp.callback_query(F.data.startswith("approve_"))
async def approve_user(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    approved_users.add(user_id)
    pending_approvals.pop(user_id, None)
    await callback.message.delete()
    await callback.answer("Заявка одобрена", show_alert=True)
    try:
        await bot.send_message(user_id, '<tg-emoji emoji-id="5278602437001767574">🔓</tg-emoji> Ваша заявка была успешно одобрена, для начала работы с ботом, пропишите /start')
    except:
        pass

@dp.callback_query(F.data.startswith("reject_"))
async def reject_user(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    pending_approvals.pop(user_id, None)
    await callback.message.delete()
    await callback.answer("Заявка отклонена", show_alert=True)
    try:
        await bot.send_message(user_id, '<tg-emoji emoji-id="5278578973595427038">🚫</tg-emoji> Ваша заявка, к сожалению, отклонена. Свяжитесь с администратором для уточнения причины.')
    except:
        pass

@dp.callback_query(F.data == "maxqr")
async def maxqr_stub(callback: types.CallbackQuery):
    await callback.answer("В разработке", show_alert=True)

@dp.callback_query(F.data == "withdraw")
async def withdraw_stub(callback: types.CallbackQuery):
    await callback.answer("В разработке", show_alert=True)

@dp.callback_query(F.data == "archive")
async def archive_menu(callback: types.CallbackQuery):
    text = '<tg-emoji emoji-id="5278227821364275264">📁</tg-emoji> Архив данных\nВ данном разделе вы можете увидеть статистику бота за все время, выберите раздел:'
    await callback.message.edit_text(text, reply_markup=archive_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "my_stats")
async def my_stats(callback: types.CallbackQuery):
    text = (
        '<tg-emoji emoji-id="5298668674532538341">👥️</tg-emoji> Моя статистика:\n'
        '<blockquote>┌ Сдано номеров: <code>0</code>\n'
        '├ Выдано сегодня: <code>0</code>\n'
        '├ Успешно засчитано: <code>0</code>\n'
        '├ Всего слетов: <code>0</code>\n'
        '└ Заработано за всё время: <code>0.00$</code></blockquote>'
    )
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Назад", callback_data="archive"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "global_stats")
async def global_stats(callback: types.CallbackQuery):
    text = (
        '<tg-emoji emoji-id="5298668674532538341">👥️</tg-emoji> Общая статистика бота:\n'
        '<blockquote>┌ Зарегистрировано: <code>0</code>\n'
        '├ Всего сдано номеров: <code>0</code>\n'
        '├ Выдано сегодня: <code>0</code>\n'
        '├ Успешно засчитано: <code>0</code>\n'
        '├ Всего слетов: <code>0</code>\n'
        '├ Заработано дропами: <code>0.00$</code>\n'
        '└ Выплачено всего: <code>0.00$</code></blockquote>'
    )
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Назад", callback_data="archive"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    text = (
        f'<tg-emoji emoji-id="5276220667182736079">📥</tg-emoji> Добро пожаловать в бота QR scan GOST!\n'
        f'<blockquote>┌ Ваш ID: <code>{user_id}</code>\n'
        f'├ Актуальный прайс: <code>4.4$</code>\n'
        f'├ Общая очередь: <code>0</code>\n'
        f'├ Баланс: <code>0.00$</code>\n'
        f'└ <tg-emoji emoji-id="5215670591905869044">🟢</tg-emoji> Статус бота: В работе</blockquote>'
    )
    await callback.message.edit_text(text, reply_markup=menu_keyboard())
    await callback.answer()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
