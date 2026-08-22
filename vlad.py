import asyncio
import os
import psycopg2
import re
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN_FROM_ENV = os.environ.get('BOT_TOKEN', '8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw')
BOT_TOKEN = TOKEN_FROM_ENV.replace(" ", "").strip()

ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')
REQUIRED_CHANNEL = "@norikx"
REQUIRED_CHANNEL_URL = "https://t.me/norikx"
OWNER_TG_LINK = "https://t.me/NorikAmiri"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Хранилища состояния
mutes = {}               # user_id -> {"until": datetime}
spam_tasks = {}          # (chat_id, bc_id) -> Task
typing_tasks = {}        # bc_id -> Task
user_spam_texts = {}     # user_id -> str
link_chats = set()       # chat_ids
reply_guard_chats = set()
typing_disabled_chats = set()
substitutions = {}       # chat_id -> {"text": str, "mode": int}
msg_cache = {}           # (chat_id, msg_id) -> dict
active_chats = {}        # bc_id -> set(chat_ids)
promo_messages = {}      # (chat_id, bc_id) -> message_id
recent_business_chats = [] # Список (chat_id, bc_id) для бизнес-рассылок
bot_id = None
CHANNEL_LINK = None      

bc_owners = {}           # bc_id -> user_id
user_usernames = {}      
user_names = {}          
banned_users = set()     

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
    "• <code>!команды</code> — меню команд"
)

TEXT_CONNECT_INSTRUCTION = (
    "🚀 <b>Инструкция по подключению бота:</b>\n\n"
    "1️⃣ Перейдите в <b>Настройки</b> Telegram.\n"
    "2️⃣ Откройте раздел <b>Мой профиль</b>.\n"
    "3️⃣ Выберите пункт <b>Автоматизация чатов</b>.\n"
    "4️⃣ Добавьте бота: <code>@norikKodBot</code>.\n"
    "5️⃣ ⚠️ <b>ОБЯЗАТЕЛЬНО:</b> Предоставьте боту полный доступ к сообщениям <b>5/5</b>!\n\n"
    "📢 <b>Обратите внимание:</b> Бот публикует рекламные материалы в подключенных чатах. "
    "<b>Удалять рекламу строго запрещено!</b> В случае удаления рекламного сообщения вы будете заблокированы."
)

def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    global CHANNEL_LINK
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS chat_settings (chat_id BIGINT, setting_type TEXT, PRIMARY KEY (chat_id, setting_type))")
                cur.execute("CREATE TABLE IF NOT EXISTS substitutions (chat_id BIGINT PRIMARY KEY, text TEXT, mode INTEGER)")
                cur.execute("CREATE TABLE IF NOT EXISTS spam_texts (key_id TEXT PRIMARY KEY, text TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS global_config (key TEXT PRIMARY KEY, value TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)")
                cur.execute("CREATE TABLE IF NOT EXISTS user_map (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT)")
                
                cur.execute("ALTER TABLE user_map ADD COLUMN IF NOT EXISTS first_name TEXT")
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

                cur.execute("SELECT value FROM global_config WHERE key='channel_link'")
                row = cur.fetchone()
                if row: CHANNEL_LINK = row[0]
    except Exception as e:
        logging.error(f"❌ Ошибка БД: {e}")

def save_user_info(user_id: int, username: str, first_name: str):
    user_id = int(user_id)
    if first_name:
        user_names[user_id] = first_name
    username_clean = username.lstrip("@").lower() if username else None
    if username_clean:
        user_usernames[username_clean] = user_id
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

def save_channel_link(link_url):
    global CHANNEL_LINK
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO global_config (key, value) VALUES ('channel_link', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (link_url,))
                conn.commit()
                CHANNEL_LINK = link_url
    except Exception as e:
        logging.error(f"Ошибка сохранения ссылки: {e}")

def get_user_mention(user_id: int, fallback_name: str = None) -> str:
    user_id = int(user_id)
    fname = user_names.get(user_id) or fallback_name or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{fname}</a>'

async def check_subscription(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id=REQUIRED_CHANNEL, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logging.warning(f"Ошибка проверки подписки: {e}")
        return True

init_db()

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
        if bc_id:
            kwargs["business_connection_id"] = bc_id

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
            try:
                await bot.send_message(**kwargs)
            except: pass

async def promo_broadcaster():
    promo_text = "Можешь, пожалуйста, на наш канал подписаться? Если не трудно ❤️"
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Подписаться", url=REQUIRED_CHANNEL_URL)]
    ])

    while True:
        await asyncio.sleep(7200)
        target_chats = recent_business_chats[-100:]

        for chat_info in target_chats:
            cid, bc_id = chat_info
            owner_id = bc_owners.get(bc_id)
            if owner_id and owner_id in banned_users:
                continue
            try:
                msg = await bot.send_message(
                    chat_id=cid,
                    text=promo_text,
                    parse_mode="HTML",
                    reply_markup=promo_kb,
                    business_connection_id=bc_id
                )
                promo_messages[(cid, bc_id)] = msg.message_id
            except Exception as e:
                logging.warning(f"Ошибка рассылки рекламы в бизнес-чат {cid}: {e}")
            await asyncio.sleep(3)

async def check_promo_deletions():
    unban_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать владельцу", url=OWNER_TG_LINK)]
    ])
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Подписаться", url=REQUIRED_CHANNEL_URL)]
    ])

    while True:
        await asyncio.sleep(15)
        for (cid, bc_id), msg_id in list(promo_messages.items()):
            owner_id = bc_owners.get(bc_id)
            if owner_id and owner_id in banned_users:
                continue
            try:
                await bot.edit_message_reply_markup(
                    chat_id=cid,
                    message_id=msg_id,
                    reply_markup=promo_kb,
                    business_connection_id=bc_id
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
                                text=f"Вы забанены владельцем за удаление рекламы.",
                                parse_mode="HTML",
                                reply_markup=unban_kb
                            )
                        except: pass
                    promo_messages.pop((cid, bc_id), None)
            except: pass

@dp.business_connection()
async def handle_bc(bc):
    bc_owners[bc.id] = int(bc.user.id)
    save_user_info(bc.user.id, bc.user.username, bc.user.first_name)
    owner_id = int(bc.user.id)
    owner_mention = get_user_mention(owner_id, bc.user.first_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать владельцу", url=OWNER_TG_LINK)]
    ])
    try:
        await bot.send_message(
            owner_id,
            f"👋 Привет, {owner_mention}!\n\n"
            f"✅ Бот успешно подключен!\n\n"
            f"{TEXT_COMMANDS_HELP}",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Не удалось отправить приветствие: {e}")

def get_start_keyboard(user_id: int):
    buttons = []
    if user_id == ADMIN_ID:
        buttons.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="btn_admin_panel")])
        
    buttons.append([InlineKeyboardButton(text="📖 Функционал", callback_data="btn_features")])
    buttons.append([InlineKeyboardButton(text="⚡ Как подключить бота", callback_data="btn_how_to_connect")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != "private":
        return
    
    uid = message.from_user.id
    save_user_info(uid, message.from_user.username, message.from_user.first_name)
    
    user_mention = get_user_mention(uid, message.from_user.first_name)
    keyboard = get_start_keyboard(uid)
    
    await message.answer(
        f"👋 Добро пожаловать, {user_mention}!\n\n"
        f"💬 <b>Обратите внимание:</b> Бот работает только в личных сообщениях!\n\n"
        f"Выберите раздел:",
        parse_mode="HTML",
        reply_markup=keyboard
    )

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="💼 Бизнес-аккаунты", callback_data="admin_users")],
        [InlineKeyboardButton(text="🚫 Забанить / Разбанить", callback_data="admin_ban_prompt")]
    ])

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("check_sub:"))
async def check_sub_callback(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        await callback.answer("Кнопка предназначена для владельца!", show_alert=True)
        return

    is_subbed = await check_subscription(user_id)
    if is_subbed:
        try:
            await callback.message.delete()
        except: pass
        await callback.answer("✅ Подписка подтверждена!", show_alert=True)
    else:
        await callback.answer("❌ Подписка не обнаружена!", show_alert=True)

@dp.callback_query()
async def process_callbacks(callback: CallbackQuery):
    data = callback.data
    uid = callback.from_user.id

    if data == "btn_features":
        await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML")
        await callback.answer()
        return

    if data == "btn_how_to_connect":
        await callback.message.answer(TEXT_CONNECT_INSTRUCTION, parse_mode="HTML")
        await callback.answer()
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

    if data == "admin_stats":
        await callback.message.edit_text(
            f"📊 <b>СТАТИСТИКА:</b>\n\n"
            f"• Бизнес-аккаунтов: <code>{len(set(bc_owners.values()))}</code>\n"
            f"• Юзеров в базе: <code>{len(user_names)}</code>\n"
            f"• Забанено: <code>{len(banned_users)}</code>",
            reply_markup=get_admin_keyboard(), parse_mode="HTML"
        )
    elif data == "admin_users":
        text = "💼 <b>ПОДКЛЮЧЕННЫЕ БИЗНЕС-АККАУНТЫ:</b>\n\n"
        bc_users = list(set(bc_owners.values()))
        if not bc_users: 
            text += "Нет подключенных бизнес-аккаунтов."
        else:
            for u_id in bc_users[:35]:
                fname = user_names.get(u_id, "Пользователь")
                user_link = f'<a href="tg://user?id={u_id}">{fname}</a>'
                status = "🔴 (Бан)" if u_id in banned_users else "🟢"
                text += f"• {user_link} (<code>{u_id}</code>) {status}\n"
        await callback.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

    elif data == "admin_ban_prompt":
        await callback.message.edit_text(
            "Команды бана/разбана:\n\n"
            "• <code>/ban 123456789</code> или <code>/ban @username</code>\n"
            "• <code>/unban 123456789</code> или <code>/unban @username</code>",
            reply_markup=get_admin_keyboard(), parse_mode="HTML"
        )
    await callback.answer()

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

        if uid in banned_users or (owner_id and owner_id in banned_users):
            return

        if bc_id:
            if bc_id not in bc_owners:
                try:
                    conn_info = await bot.get_business_connection(bc_id)
                    bc_owners[bc_id] = int(conn_info.user.id)
                    save_user_info(conn_info.user.id, conn_info.user.username, conn_info.user.first_name)
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

        if not is_from_me and uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if chat_id in reply_guard_chats and message.reply_to_message and not is_from_me:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if not is_from_me or not message.text:
            return

        is_subbed = await check_subscription(uid)
        if not is_subbed:
            await clear_cmd(chat_id, message.message_id, bc_id)
            user_link = get_user_mention(uid, message.from_user.first_name)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📢 Подписаться на канал", url=REQUIRED_CHANNEL_URL)],
                [InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_sub:{uid}")]
            ])
            kwargs = {
                "chat_id": chat_id,
                "text": f"⚠️ {user_link}, подпишитесь на канал для использования бота!",
                "parse_mode": "HTML",
                "reply_markup": kb
            }
            if bc_id: kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return

        text_raw = message.text
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
                if not new_link.startswith("http"):
                    new_link = "https://t.me/" + new_link.lstrip("@")
                save_channel_link(new_link)
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

async def main():
    await start_web_server()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except: pass

    asyncio.create_task(global_typing_loop())
    asyncio.create_task(promo_broadcaster())
    asyncio.create_task(check_promo_deletions())

    logging.info("🚀 БОТ ЗАПУЩЕН!")
    await dp.start_polling(
        bot, 
        allowed_updates=["message", "business_connection", "business_message", "edited_business_message", "deleted_business_messages", "callback_query"]
    )

if __name__ == "__main__":
    asyncio.run(main())
