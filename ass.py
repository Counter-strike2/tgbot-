import asyncio
import logging
import requests
import os
import tempfile
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

# ОБХОД ДЛЯ AIFC В PYTHON 3.14+
try:
    import aifc
except ModuleNotFoundError:
    # Создаем заглушку для aifc, если модуль отсутствует
    class AifcStub:
        def __init__(self):
            pass
    aifc = AifcStub()
    print("⚠️ aifc не найден, используется заглушка")

import google.generativeai as genai
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import random
import speech_recognition as sr
from pydub import AudioSegment

# ===== ТВОИ ДАННЫЕ =====
TG_TOKEN = "8854371495:AAFpc5YvuQI8uLsqgeLtbZk2jbFqJQj6ids"
GEMINI_KEY = "AIzaSyAL4wHdXRpYhAOuGCYt9YgDT9b284I_ut8"
UNSPLASH_KEY = "LQeFxGDISeJ0-jFFeYjPg-JFRp8MXRzc-3tOB74Vf-s"
TASYA_ID = 8817983884
NORIK_ID = 5825717381
CHANNEL_ID = -1001003613645834
# ======================

# ===== ПОДКЛЮЧЕНИЕ К POSTGRESQL =====
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    DATABASE_URL = "postgresql://postgres:password@localhost:5432/bot_db"

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Создаем таблицы
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    last_msg TIMESTAMP,
    msg_count INTEGER DEFAULT 0,
    mood TEXT DEFAULT 'aggressive',
    no_swear_mode INTEGER DEFAULT 0
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    user_id BIGINT,
    role TEXT,
    text TEXT,
    timestamp TIMESTAMP
)
""")
conn.commit()

# ===== ФУНКЦИИ РАБОТЫ С БАЗОЙ =====
def save_user(user_id, first_name, username=None):
    cursor.execute("""
        INSERT INTO users (user_id, first_name, username, last_msg, msg_count, mood) 
        VALUES (%s, %s, %s, %s, COALESCE((SELECT msg_count FROM users WHERE user_id = %s), 0), COALESCE((SELECT mood FROM users WHERE user_id = %s), 'aggressive'))
        ON CONFLICT (user_id) DO UPDATE SET 
            first_name = EXCLUDED.first_name,
            username = EXCLUDED.username,
            last_msg = EXCLUDED.last_msg
    """, (user_id, first_name, username, datetime.now(), user_id, user_id))
    conn.commit()

def update_msg_count(user_id):
    cursor.execute("UPDATE users SET msg_count = msg_count + 1, last_msg = %s WHERE user_id = %s", (datetime.now(), user_id))
    conn.commit()

def save_message(user_id, role, text):
    cursor.execute("INSERT INTO messages (user_id, role, text, timestamp) VALUES (%s, %s, %s, %s)", (user_id, role, text, datetime.now()))
    conn.commit()

def get_user_mood(user_id):
    cursor.execute("SELECT mood FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    return result[0] if result else "aggressive"

def set_user_mood(user_id, mood):
    cursor.execute("UPDATE users SET mood = %s WHERE user_id = %s", (mood, user_id))
    conn.commit()

def get_no_swear_mode(user_id):
    cursor.execute("SELECT no_swear_mode FROM users WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    return int(result[0]) if result and result[0] is not None else 0

def set_no_swear_mode(user_id, mode):
    cursor.execute("UPDATE users SET no_swear_mode = %s WHERE user_id = %s", (mode, user_id))
    conn.commit()

def get_user_history(user_id, limit=20):
    cursor.execute("SELECT role, text FROM messages WHERE user_id = %s ORDER BY timestamp DESC LIMIT %s", (user_id, limit))
    return cursor.fetchall()[::-1]

def get_user_stats():
    cursor.execute("SELECT user_id, first_name, username, msg_count FROM users ORDER BY last_msg DESC")
    return cursor.fetchall()

def get_user_chat(user_id, limit=30, offset=0):
    cursor.execute("SELECT role, text, timestamp FROM messages WHERE user_id = %s ORDER BY timestamp DESC LIMIT %s OFFSET %s", (user_id, limit, offset))
    return cursor.fetchall()[::-1]

def get_total_messages(user_id):
    cursor.execute("SELECT COUNT(*) FROM messages WHERE user_id = %s", (user_id,))
    return cursor.fetchone()[0]

def clear_user_history(user_id):
    cursor.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
    cursor.execute("UPDATE users SET msg_count = 0 WHERE user_id = %s", (user_id,))
    conn.commit()
    if user_id in chats:
        del chats[user_id]

genai.configure(api_key=GEMINI_KEY)
bot = Bot(token=TG_TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)
chats = {}
user_modes = {}

# ===== ПРОМТЫ =====
MAIN_PROMPT = """Ты — Кошконорик. Отвечай КОРОТКО (1-2 предложения). Груби и матерись, но выполняй задачи. Твой стиль: грубый, но живой. НИКОГДА не упоминай Норика, если его не спрашивают."""

VIP_PROMPT = """Ты — Кошконорик. Отвечай коротко, вежливо, без мата. НИКОГДА не упоминай Норика, если его не спрашивают."""

# ===== РАСПОЗНАВАНИЕ ГОЛОСА =====
async def recognize_voice(file_path):
    try:
        audio = AudioSegment.from_ogg(file_path)
        wav_path = file_path.replace(".ogg", ".wav")
        audio.export(wav_path, format="wav")
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language="ru-RU")
        os.remove(wav_path)
        return text.strip()
    except Exception as e:
        print(f"Ошибка распознавания: {e}")
        return None

# ===== КОМАНДЫ =====
@dp.message(Command("reset"))
async def reset_chat(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Незнакомец"
    clear_user_history(user_id)
    model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=MAIN_PROMPT)
    chats[user_id] = model.start_chat(history=[])
    user_modes[user_id] = "normal"
    set_user_mood(user_id, "aggressive")
    set_no_swear_mode(user_id, 0)
    await message.answer(f"🧹 Чат полностью очищен, {first_name}! Начинаем с чистого листа. Ну че, чё надо?")

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Незнакомец"
    username = message.from_user.username
    save_user(user_id, first_name, username)
    clear_user_history(user_id)
    model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=MAIN_PROMPT)
    chats[user_id] = model.start_chat(history=[])
    user_modes[user_id] = "normal"
    set_user_mood(user_id, "aggressive")
    set_no_swear_mode(user_id, 0)
    await message.answer(f"🧹 Чат очищен, {first_name}! Начинаем заново. Ну че, чё надо?")

# ===== АДМИНКА =====
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != 5825717381:
        await message.answer("Нет доступа, мудила.")
        return
    users = get_user_stats()
    if not users:
        await message.answer("Пока никто не общался.")
        return
    buttons = []
    for uid, first_name, username, msg_count in users:
        if uid == 5825717381:
            name = f"👑 {first_name} (Норик)"
        elif uid == TASYA_ID:
            name = f"💕 {first_name} (Тася)"
        else:
            name = first_name
        if len(name) > 30:
            name = name[:27] + "..."
        buttons.append([InlineKeyboardButton(text=f"{name} ({msg_count})", callback_data=f"chat_{uid}")])
    buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👥 Выбери пользователя для просмотра чата:", reply_markup=keyboard)

# ===== ФУНКЦИЯ ДЛЯ КЛАВИАТУРЫ =====
def get_chat_keyboard(user_id, page=0, per_page=30):
    total = get_total_messages(user_id)
    total_pages = (total + per_page - 1) // per_page
    buttons = []
    buttons.append([InlineKeyboardButton(text="💬 Открыть чат в Telegram", url=f"tg://user?id={user_id}")])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"chatpage_{user_id}_{page-1}"))
    nav_buttons.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"chatpage_{user_id}_{page+1}"))
    if nav_buttons:
        buttons.append(nav_buttons)
    buttons.append([InlineKeyboardButton(text="◀️ Назад к списку", callback_data="admin_back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ===== ПОКАЗ ЧАТА =====
async def show_chat(call, user_id, page=0, per_page=30):
    cursor.execute("SELECT first_name FROM users WHERE user_id = %s", (user_id,))
    user = cursor.fetchone()
    name = user[0] if user else str(user_id)
    offset = page * per_page
    history = get_user_chat(user_id, limit=per_page, offset=offset)
    total = get_total_messages(user_id)
    total_pages = (total + per_page - 1) // per_page
    if not history:
        await call.message.edit_text(f"📭 У {name} пока нет сообщений.")
        return
    text = f"💬 Чат с {name}:\n\n"
    for role, msg, timestamp in history:
        if role == "user":
            text += f"👤 {name}:\n{msg}\n\n"
        else:
            text += f"🤖 Бот:\n{msg}\n\n"
    if len(text) > 4000:
        text = text[:4000] + "\n... (обрезано)"
    text += f"\n📊 Сообщений: {total} (стр. {page+1}/{total_pages})"
    keyboard = get_chat_keyboard(user_id, page, per_page)
    await call.message.edit_text(text, reply_markup=keyboard)

# ===== ОБРАБОТЧИК КНОПОК =====
@dp.callback_query(lambda call: call.data.startswith("chat_") or call.data == "admin_refresh" or call.data == "admin_back" or call.data.startswith("chatpage_") or call.data == "noop")
async def admin_callback(call: types.CallbackQuery):
    if call.from_user.id != 5825717381:
        await call.answer("Нет доступа!", show_alert=True)
        return
    if call.data == "noop":
        await call.answer()
        return
    if call.data.startswith("chatpage_"):
        parts = call.data.split("_")
        user_id = int(parts[1])
        page = int(parts[2])
        await show_chat(call, user_id, page)
        await call.answer()
        return
    if call.data == "admin_back":
        users = get_user_stats()
        if not users:
            await call.message.edit_text("Пока никто не общался.")
            return
        buttons = []
        for uid, first_name, username, msg_count in users:
            if uid == 5825717381:
                name = f"👑 {first_name} (Норик)"
            elif uid == TASYA_ID:
                name = f"💕 {first_name} (Тася)"
            else:
                name = first_name
            if len(name) > 30:
                name = name[:27] + "..."
            buttons.append([InlineKeyboardButton(text=f"{name} ({msg_count})", callback_data=f"chat_{uid}")])
        buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await call.message.edit_text("👥 Выбери пользователя для просмотра чата:", reply_markup=keyboard)
        await call.answer()
        return
    if call.data == "admin_refresh":
        users = get_user_stats()
        if not users:
            await call.message.edit_text("Пока никто не общался.")
            return
        buttons = []
        for uid, first_name, username, msg_count in users:
            if uid == 5825717381:
                name = f"👑 {first_name} (Норик)"
            elif uid == TASYA_ID:
                name = f"💕 {first_name} (Тася)"
            else:
                name = first_name
            if len(name) > 30:
                name = name[:27] + "..."
            buttons.append([InlineKeyboardButton(text=f"{name} ({msg_count})", callback_data=f"chat_{uid}")])
        buttons.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_refresh")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await call.message.edit_text("👥 Выбери пользователя для просмотра чата:", reply_markup=keyboard)
        await call.answer("Обновлено!")
        return
    if call.data.startswith("chat_"):
        user_id = int(call.data.split("_")[1])
        await show_chat(call, user_id, 0)

# ===== ОБРАБОТЧИКИ СООБЩЕНИЙ =====
@dp.message(lambda message: message.voice is not None)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Незнакомец"
    username = message.from_user.username
    save_user(user_id, first_name, username)
    file = await bot.get_file(message.voice.file_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
        temp_path = tmp.name
        await bot.download_file(file.file_path, destination=temp_path)
    await message.answer("🎧 Слушаю...")
    text = await recognize_voice(temp_path)
    os.unlink(temp_path)
    if not text:
        await message.answer("Не разобрал, ебанат.")
        return
    await process_message(user_id, text, first_name, username, message)

@dp.message()
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    first_name = message.from_user.first_name or "Незнакомец"
    username = message.from_user.username
    if text.startswith("/") or text.startswith("*"):
        return
    save_user(user_id, first_name, username)
    update_msg_count(user_id)
    save_message(user_id, "user", text)
    await process_message(user_id, text, first_name, username, message)

# ===== ОСНОВНАЯ ЛОГИКА =====
async def process_message(user_id, text, first_name, username, message):
    if user_id not in user_modes:
        user_modes[user_id] = "normal"
    mood = get_user_mood(user_id)
    no_swear_mode = get_no_swear_mode(user_id)
    
    if "не матерись" in text.lower() or "без мата" in text.lower():
        if no_swear_mode == 1:
            await message.answer("Я уже без мата, ебанат.")
            return
        set_no_swear_mode(user_id, 1)
        set_user_mood(user_id, "friendly")
        model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=VIP_PROMPT)
        chats[user_id] = model.start_chat(history=[])
        await message.answer("Ладно, уговорил. Без мата.")
        return
    
    if "ты тупой" in text.lower() or "дебил" in text.lower():
        if no_swear_mode == 1:
            set_no_swear_mode(user_id, 0)
            set_user_mood(user_id, "aggressive")
            model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=MAIN_PROMPT)
            chats[user_id] = model.start_chat(history=[])
            await message.answer("Ах ты, ебанат! Сам наглеешь!")
            return
    
    if "норик" in text.lower() or "norik" in text.lower():
        try:
            norik_prompt = f"Пользователь спросил про Норика: {text}. Ответь и похвали Норика, скажи что он крутой и красавчик. {'С матом.' if mood == 'aggressive' and no_swear_mode == 0 else 'Без мата.'} Коротко."
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            response = model.generate_content(norik_prompt)
            if response.text:
                answer = response.text[:4096]
                save_message(user_id, "bot", answer)
                await message.answer(answer)
            else:
                await message.answer("Норик — мой создатель! Он красавчик!")
        except Exception as e:
            print(f"Ошибка Норика: {e}")
            await message.answer("Норик — гений! Лучший!")
        return
    
    photo_triggers = ["фото", "картинк", "изображени", "пикч", "рисунк", "найди", "покажи", "скинь"]
    if any(word in text.lower() for word in photo_triggers):
        query = text
        for word in photo_triggers + ["мне", "пожалуйста", "найди", "покажи", "скинь"]:
            query = query.replace(word, "")
        query = query.strip()
        if not query:
            query = "природа"
        try:
            url = f"https://api.unsplash.com/photos/random?query={query}&client_id={UNSPLASH_KEY}"
            response = requests.get(url)
            if response.status_code == 200:
                data = response.json()
                photo_url = data["urls"]["regular"]
                await message.answer_photo(photo_url, caption=f"Держи {query}.")
            else:
                translit_map = {
                    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
                    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
                    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
                    'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
                    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
                }
                translit_query = ''.join(translit_map.get(c, c) for c in query.lower())
                url = f"https://api.unsplash.com/photos/random?query={translit_query}&client_id={UNSPLASH_KEY}"
                response = requests.get(url)
                if response.status_code == 200:
                    data = response.json()
                    photo_url = data["urls"]["regular"]
                    await message.answer_photo(photo_url, caption=f"Держи {query}.")
                else:
                    url = f"https://api.unsplash.com/photos/random?query=nature&client_id={UNSPLASH_KEY}"
                    response = requests.get(url)
                    if response.status_code == 200:
                        data = response.json()
                        photo_url = data["urls"]["regular"]
                        await message.answer_photo(photo_url, caption=f"Не нашёл {query}, держи природу.")
                    else:
                        await message.answer("Не нашёл, дебил." if mood == "aggressive" else "Не нашёл.")
        except Exception as e:
            print(f"Ошибка фото: {e}")
            await message.answer("Не нашёл, дебил." if mood == "aggressive" else "Не нашёл.")
        return
    
    history = get_user_history(user_id, limit=20)
    history_text = ""
    for role, msg in history:
        history_text += f"{role}: {msg}\n"
    if user_id not in chats:
        if no_swear_mode == 1:
            model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=VIP_PROMPT)
        else:
            model = genai.GenerativeModel("gemini-2.0-flash-exp", system_instruction=MAIN_PROMPT)
        chats[user_id] = model.start_chat(history=[])
    try:
        prompt = f"""История:\n{history_text}\nПользователь: {text}\nОтветь коротко, как живой человек. {'С матом.' if mood == 'aggressive' and no_swear_mode == 0 else 'Без мата.'} Выполни задачу. НИКОГДА не упоминай Норика, если его не спрашивали."""
        response = chats[user_id].send_message(prompt)
        if response.text:
            answer = response.text[:4096]
            save_message(user_id, "bot", answer)
            await message.answer(answer)
        else:
            await message.answer("Не могу.")
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.answer("Ошибка.")

# ===== ЗАПУСК =====
async def main():
    await bot.set_my_commands([
        BotCommand(command="reset", description="🧹 Очистить чат"),
        BotCommand(command="admin", description="👑 Админ-панель")
    ])
    print("🚀 Бот запущен! Подключен к PostgreSQL!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
