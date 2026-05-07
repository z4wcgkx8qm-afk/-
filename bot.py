import os
import asyncio
import sqlite3

from aiogram import Bot, Dispatcher, types
from groq import Groq

# --- ENV ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = Groq(api_key=GROQ_API_KEY)

# --- MEMORY (SQLite) ---
conn = sqlite3.connect("memory.db")
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS memory (
    user_id INTEGER,
    role TEXT,
    content TEXT
)
""")
conn.commit()


# --- DB FUNCTIONS ---
def save(user_id, role, content):
    cur.execute(
        "INSERT INTO memory VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()


def load(user_id):
    cur.execute(
        "SELECT role, content FROM memory WHERE user_id=? ORDER BY rowid DESC LIMIT 12",
        (user_id,)
    )
    rows = cur.fetchall()
    rows.reverse()
    return [{"role": r, "content": c} for r, c in rows]


# --- SYSTEM ---
SYSTEM = """
Ты — Мыслитель, живой чат-бот.

Правила:
- Понимаешь контекст разговора
- Отвечаешь естественно, как человек
- Без воды и повторов
- Без сложных слов
- Коротко и по делу
"""


# --- AI ---
def ask_ai(user_id, text):
    save(user_id, "user", text)

    history = load(user_id)

    messages = [{"role": "system", "content": SYSTEM}] + history

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages
    )

    answer = response.choices[0].message.content

    save(user_id, "assistant", answer)

    return answer


# --- HANDLER ---
@dp.message()
async def handler(message: types.Message):
    try:
        answer = ask_ai(message.from_user.id, message.text)
        await message.answer(answer)
    except Exception:
        await message.answer("Ошибка, попробуй ещё раз.")


# --- START ---
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
