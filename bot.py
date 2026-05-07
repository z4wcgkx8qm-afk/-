import os
import asyncio
import sqlite3

from aiogram import Bot, Dispatcher, types
from groq import Groq

# --- ENV ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# --- BOT ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

client = Groq(api_key=GROQ_API_KEY)

# --- DB (SQLite) ---
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


# --- DB FUNCS ---
def save_message(user_id, role, content):
    cur.execute(
        "INSERT INTO memory VALUES (?, ?, ?)",
        (user_id, role, content)
    )
    conn.commit()


def get_history(user_id):
    cur.execute(
        "SELECT role, content FROM memory WHERE user_id=? ORDER BY rowid DESC LIMIT 10",
        (user_id,)
    )
    rows = cur.fetchall()

    # разворачиваем в правильный порядок
    rows.reverse()

    return [{"role": r, "content": c} for r, c in rows]


# --- AI ---
def ask_ai(user_id, text):
    save_message(user_id, "user", text)

    history = get_history(user_id)

    messages = [
        {
            "role": "system",
            "content": "Ты Мыслитель. Отвечай кратко, умно, иногда саркастично. Без воды."
        }
    ] + history

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages
    )

    answer = response.choices[0].message.content

    save_message(user_id, "assistant", answer)

    return answer


# --- HANDLER ---
@dp.message()
async def handler(message: types.Message):
    answer = ask_ai(message.from_user.id, message.text)
    await message.answer(answer)


# --- START ---
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
