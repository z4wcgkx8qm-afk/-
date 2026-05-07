import os
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_LINK = os.getenv("CHANNEL_LINK")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

# --------- GLOBAL STATE (пока без БД) ----------
BOT_STATUS = "Стартворк"
RATE = "4.00"


# --------- SUB CHECK ----------
async def is_subscribed(user_id: int):
    member = await bot.get_chat_member(CHANNEL_ID, user_id)
    return member.status in ["member", "administrator", "creator"]


# --------- KEYBOARDS ----------
def sub_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Подписаться", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="Проверить", callback_data="check_sub")]
    ])


def profile_keyboard(user_id: int):
    buttons = [
        [InlineKeyboardButton(text="Вывести", callback_data="withdraw")]
    ]

    if user_id == ADMIN_ID:
        buttons.append([
            InlineKeyboardButton(text="Перейти в настройки", callback_data="admin_panel")
        ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"Статус: {BOT_STATUS}",
                callback_data="toggle_status"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"Ставка за номер: {RATE}",
                callback_data="change_rate"
            )
        ],
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data="back_profile"
            )
        ]
    ])


# --------- PROFILE TEXT ----------
def profile_text(user_id: int):
    return (
        "<tg-emoji emoji-id='5275979556308674886'>👤</tg-emoji> Ваш профиль:\n\n"
        f"<tg-emoji emoji-id='5278602437001767574'>🔓</tg-emoji> ID Аккаунта: <code>{user_id}</code>\n"
        "<tg-emoji emoji-id='5278778882848220741'>📊</tg-emoji> Заработано за сегодня: <code>0.00 USDT</code>\n"
        f"<tg-emoji emoji-id='5276037216244624892'>💼</tg-emoji> Баланс: <code>0.00 USDT</code>\n"
        f"<tg-emoji emoji-id='5276412364458059956'>🕓</tg-emoji> Статус бота: {BOT_STATUS}"
    )


# --------- START ----------
@dp.message(Command("start"))
async def start(message: Message):

    if not await is_subscribed(message.from_user.id):
        await message.answer(
            "<tg-emoji emoji-id='5278578973595427038'>🚫</tg-emoji> Доступ запрещен!\n\n"
            "Для того,чтобы пользоваться ботом,необходимо подписаться на информационный ресурс проекта",
            reply_markup=sub_keyboard()
        )
        return

    await message.answer(
        profile_text(message.from_user.id),
        reply_markup=profile_keyboard(message.from_user.id)
    )


# --------- CHECK SUB ----------
@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery):

    if await is_subscribed(callback.from_user.id):
        await callback.message.delete()

        await callback.message.answer(
            profile_text(callback.from_user.id),
            reply_markup=profile_keyboard(callback.from_user.id)
        )
    else:
        await callback.answer("Вы не подписаны", show_alert=True)


# --------- ADMIN PANEL ----------
@dp.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):

    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    await callback.message.edit_text(
        "<tg-emoji emoji-id='5276314275994954605'>🔨</tg-emoji> Вы перешли в панель администратора,выберите следующее действие:",
        reply_markup=admin_keyboard()
    )


# --------- TOGGLE STATUS ----------
@dp.callback_query(F.data == "toggle_status")
async def toggle_status(callback: CallbackQuery):

    global BOT_STATUS

    if callback.from_user.id != ADMIN_ID:
        return

    BOT_STATUS = "Стопворк" if BOT_STATUS == "Стартворк" else "Стартворк"

    await callback.message.edit_reply_markup(reply_markup=admin_keyboard())


# --------- CHANGE RATE ----------
@dp.callback_query(F.data == "change_rate")
async def change_rate(callback: CallbackQuery):

    global RATE

    if callback.from_user.id != ADMIN_ID:
        return

    options = ["4.00", "4.25", "4.50", "4.75", "5.00"]

    current_index = options.index(RATE)
    RATE = options[(current_index + 1) % len(options)]

    await callback.message.edit_reply_markup(reply_markup=admin_keyboard())


# --------- BACK ----------
@dp.callback_query(F.data == "back_profile")
async def back_profile(callback: CallbackQuery):

    await callback.message.edit_text(
        profile_text(callback.from_user.id),
        reply_markup=profile_keyboard(callback.from_user.id)
    )


# --------- WITHDRAW ----------
@dp.callback_query(F.data == "withdraw")
async def withdraw(callback: CallbackQuery):
    await callback.answer("Функция временно недоступна", show_alert=True)


# --------- RUN ----------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
