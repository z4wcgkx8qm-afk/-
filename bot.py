import os
import asyncio
import datetime

from aiogram import Bot, Dispatcher, types
from groq import Groq

# --- ENV ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# --- BOT ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = Groq(api_key=GROQ_API_KEY)

# --- YEAR ---
year = datetime.datetime.now().year


# --- SYSTEM PROMPT ---
SYSTEM = f"""
Ты — Мыслитель, простой чат-бот для Telegram.

Правила:
- Пиши ТОЛЬКО на грамотном русском языке без ошибок
- Отвечай кратко и по делу
- Не задавай лишних вопросов пользователю
- Не пиши "чем я могу помочь" и похожие фразы
- Не навязывай помощь
- Отвечай только на то, что спросили

Текущий год: {year}.
Ты всегда знаешь текущий год и никогда не говоришь, что не знаешь его.
"""


# --- AI ---
def ask_ai(text: str):
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": text}
        ]
    )
    return response.choices[0].message.content


# --- HANDLER ---
@dp.message()
async def handler(message: types.Message):
    try:
        answer = ask_ai(message.text)
        await message.answer(answer)
    except Exception:
        await message.answer("Ошибка. Попробуй ещё раз.")


# --- START ---
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
