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

CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
CHANNEL_LINK = os.getenv("CHANNEL_LINK")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher()


async def is_subscribed(user_id: int):
    member = await bot.get_chat_member(
        chat_id=CHANNEL_ID,
        user_id=user_id
    )

    return member.status in ["member", "administrator", "creator"]


def sub_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подписаться",
                    url=CHANNEL_LINK
                )
            ],
            [
                InlineKeyboardButton(
                    text="Проверить",
                    callback_data="check_sub"
                )
            ]
        ]
    )


@dp.message(Command("start"))
async def start(message: Message):

    if not await is_subscribed(message.from_user.id):

        await message.answer(
            "<tg-emoji emoji-id='5278578973595427038'>🚫</tg-emoji> Доступ запрещен!\n\n"
            "Для того,чтобы пользоваться ботом,необходимо подписаться на информационный ресурс проекта",
            reply_markup=sub_keyboard()
        )

        return

    await message.answer("запущено")


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery):

    if await is_subscribed(callback.from_user.id):

        await callback.message.edit_text("запущено")

    else:

        await callback.answer(
            "Вы не подписаны",
            show_alert=True
        )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
