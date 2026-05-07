import os
import asyncio

from aiogram import Bot, Dispatcher, types
from groq import Groq

BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = Groq(api_key=GROQ_API_KEY)


def ask_ai(text: str):
    response = client.chat.completions.create(
        model="llama-3.1-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": "Ты Мыслитель. Отвечай кратко, умно и по делу."
            },
            {
                "role": "user",
                "content": text
            }
        ]
    )
    return response.choices[0].message.content


@dp.message()
async def handler(message: types.Message):
    answer = ask_ai(message.text)
    await message.answer(answer)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
