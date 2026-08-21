import asyncio
import os
import psycopg2
import re
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN = '8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw'
ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')
DEFAULT_LINK = "https://t.me/gotrollholl"
BOT_USERNAME = "norikKodBot"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Хранилища
mutes = {}
spam_tasks = {}
typing_tasks = {}
user_spam_texts = {}
link_chats = set()
reply_guard_chats = set()
typing_disabled_chats = set()
substitutions = {}
msg_cache = {}
active_chats = {}
bc_owners = {}
user_usernames = {}
user_names = {}
banned_users = set()
CHANNEL_LINK = DEFAULT_LINK
bot_id = None

# ============ БАЗА ДАННЫХ ============
def get_db():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    global CHANNEL_LINK
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Таблицы
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
                cur.execute("" TABLE IF NOT EXISTS spam_texts (key_id TEXT PRIMARY KEY, text TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS global_config (key TEXT PRIMARY KEY, value TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_map (
                        user_id BIGINT PRIMARY KEY, 
                        username TEXT, 
                        first_name TEXT
                    )
                """)
                conn.commit()
                
                # Загрузка данных
                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='enabled_links'")
                for row in cur.fetchall(): link_chats.add(int(row[0]))
                
                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='reply_guard'")
                for row in cur.fetchall(): reply_guard_chats.add(int(row[0]))
                
                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='typing_disabled'")
                for row in cur.fetchall(): typing_disabled_chats.add(int(row[0]))
                
                cur.execute("SELECT chat_id, text, mode FROM substitutions")
                for row in cur.fetchall(): 
                    substitutions[int(row[0])] = {"text": row[1], "mode": row[2]}
                
                cur.execute("SELECT key_id, text FROM spam_texts")
                for row in cur.fetchall(): 
                    user_spam_texts[str(row[0])] = row[1]
                
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

init_db()

# ============ ФУНКЦИИ БАЗЫ ============
def save_user(user_id, username, first_name):
    user_id = int(user_id)
    if first_name: user_names[user_id] = first_name
    username = username.lstrip("@").lower() if username else None
    if username: user_usernames[username] = user_id
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_map (user_id, username, first_name) 
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET 
                        username = COALESCE(EXCLUDED.username, user_map.username),
                        first_name = COALESCE(EXCLUDED.first_name, user_map.first_name)
                """, (user_id, username, first_name))
                conn.commit()
    except: pass

def toggle_ban(user_id, ban):
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
    except: pass

def toggle_setting(chat_id, setting_type, enabled):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if enabled:
                    cur.execute("INSERT INTO chat_settings (chat_id, setting_type) VALUES (%s, %s) ON CONFLICT DO NOTHING", (chat_id, setting_type))
                else:
                    cur.execute("DELETE FROM chat_settings WHERE chat_id = %s AND setting_type = %s", (chat_id, setting_type))
                conn.commit()
                
                if setting_type == 'enabled_links':
                    if enabled: link_chats.add(chat_id)
                    else: link_chats.discard(chat_id)
                elif setting_type == 'reply_guard':
                    if enabled: reply_guard_chats.add(chat_id)
                    else: reply_guard_chats.discard(chat_id)
                elif setting_type == 'typing_disabled':
                    if enabled: typing_disabled_chats.add(chat_id)
                    else: typing_disabled_chats.discard(chat_id)
    except: pass

def save_sub(chat_id, text, mode):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if text is None:
                    cur.execute("DELETE FROM substitutions WHERE chat_id = %s", (chat_id,))
                    substitutions.pop(chat_id, None)
                else:
                    cur.execute("""
                        INSERT INTO substitutions (chat_id, text, mode) 
                        VALUES (%s, %s, %s)
                        ON CONFLICT (chat_id) DO UPDATE SET 
                            text = EXCLUDED.text, 
                            mode = EXCLUDED.mode
                    """, (chat_id, text, mode))
                    substitutions[chat_id] = {"text": text, "mode": mode}
                conn.commit()
    except: pass

def save_spam(key_id, text):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO spam_texts (key_id, text) VALUES (%s, %s) ON CONFLICT (key_id) DO UPDATE SET text = EXCLUDED.text", (str(key_id), text))
                conn.commit()
                user_spam_texts[str(key_id)] = text
    except: pass

def save_link(link):
    global CHANNEL_LINK
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO global_config (key, value) VALUES ('channel_link', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (link,))
                conn.commit()
                CHANNEL_LINK = link
    except: pass

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============
async def delete_msg(chat_id, msg_id, bc_id):
    if bc_id:
        try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id])
        except: pass
    else:
        try: await bot.delete_message(chat_id, msg_id)
        except: pass

async def edit_msg(chat_id, msg_id, text, bc_id, parse_mode=None):
    """Редактирует сообщение, не удаляя его"""
    if bc_id:
        try:
            await bot.edit_business_message_text(
                business_connection_id=bc_id,
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            logging.warning(f"Edit business error: {e}")
    
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode=parse_mode,
            disable_web_page_preview=True
        )
        return True
    except Exception as e:
        logging.warning(f"Edit error: {e}")
        return False

async def typing_worker(bc_id):
    try:
        while True:
            chats = active_chats.get(bc_id, set())
            for cid in list(chats)[-50:]:
                if cid not in typing_disabled_chats:
                    try: await bot.send_chat_action(chat_id=cid, action="typing", business_connection_id=bc_id)
                    except: pass
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

async def spam_worker(chat_id, bc_id, reply_to, text):
    try:
        words = text.split() if text else ["Ты", "фрик!"]
        while True:
            for word in words:
                await bot.send_message(chat_id, word, business_connection_id=bc_id, reply_to_message_id=reply_to)
                await asyncio.sleep(0.3)
    except asyncio.CancelledError:
        pass

async def unmute_task(user_id, chat_id, bc_id, name):
    try:
        await asyncio.sleep(mutes[user_id]["time"].total_seconds())
        if user_id in mutes:
            mutes.pop(user_id, None)
            link = f"<a href='tg://user?id={user_id}'>{name}</a>"
            await bot.send_message(chat_id, f"🔊 С {link} снят <b>МУТ</b> (время истекло).", parse_mode="HTML", business_connection_id=bc_id)
    except: pass

async def resolve_user(target):
    target = target.strip()
    if target.startswith("@"):
        return user_usernames.get(target[1:].lower())
    elif target.isdigit():
        return int(target)
    return None

# ============ ОБРАБОТЧИКИ ============
@dp.business_connection()
async def on_business_connection(bc):
    bc_owners[bc.id] = int(bc.user.id)
    save_user(bc.user.id, bc.user.username, bc.user.first_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать владельцу", url="https://t.me/NorikAmiri")]
    ])
    
    try:
        await bot.send_message(
            bc.user.id,
            f"👋 Привет, {bc.user.first_name or 'Пользователь'}!\n\n"
            f"✅ Бот подключен!\n"
            f"Команды: `!команды`",
            parse_mode="Markdown",
            reply_markup=kb
        )
    except: pass

@dp.message(Command("start"))
async def cmd_start(message: Message):
    save_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Владелец", url="https://t.me/NorikAmiri")]
    ])
    
    await message.answer(
        "👋 **Как подключить бота:**\n\n"
        "1️⃣ Настройки → Мой профиль\n"
        "2️⃣ Автоматизация чатов\n"
        "3️⃣ Добавить → `@norikKodBot`\n"
        "4️⃣ Права: Сообщения 5/5\n\n"
        "✅ Готово!\n"
        "📋 Команды: `!команды`",
        parse_mode="Markdown",
        reply_markup=kb
    )

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="👥 Клиенты", callback_data="users")],
        [InlineKeyboardButton(text="🚫 Бан/Разбан", callback_data="ban")]
    ])
    await message.answer("👑 **Админ-панель**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query()
async def admin_callback(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Нет доступа!", show_alert=True)
        return
    
    data = callback.data
    if data == "stats":
        await callback.message.edit_text(
            f"📊 **Статистика:**\n\n"
            f"Аккаунтов: {len(bc_owners)}\n"
            f"В бане: {len(banned_users)}",
            reply_markup=callback.message.reply_markup
        )
    elif data == "users":
        text = "👥 **Клиенты:**\n\n"
        if not bc_owners:
            text += "Нет подключений"
        else:
            for bc_id, uid in bc_owners.items():
                name = user_names.get(uid, "Пользователь")
                status = "🔴" if uid in banned_users else "🟢"
                text += f"{status} <a href='tg://user?id={uid}'>{name}</a>\n"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=callback.message.reply_markup)
    elif data == "ban":
        await callback.message.edit_text(
            "Команды:\n"
            "`/ban 123` или `/ban @username`\n"
            "`/unban 123` или `/unban @username`",
            parse_mode="Markdown",
            reply_markup=callback.message.reply_markup
        )
    await callback.answer()

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        uid = await resolve_user(arg)
        if uid:
            toggle_ban(uid, True)
            name = user_names.get(uid, str(uid))
            await message.answer(f"🚫 <a href='tg://user?id={uid}'>{name}</a> забанен!", parse_mode="HTML")
        else:
            await message.answer("❌ Не найден")
    except:
        await message.answer("❌ /ban 123 или /ban @username")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        uid = await resolve_user(arg)
        if uid:
            toggle_ban(uid, False)
            name = user_names.get(uid, str(uid))
            await message.answer(f"✅ <a href='tg://user?id={uid}'>{name}</a> разбанен!", parse_mode="HTML")
        else:
            await message.answer("❌ Не найден")
    except:
        await message.answer("❌ /unban 123 или /unban @username")

# ============ ГЛАВНЫЙ ОБРАБОТЧИК ============
@dp.message()
@dp.business_message()
async def handle_message(message: Message):
    global bot_id
    
    try:
        if not message.from_user: return
        
        uid = message.from_user.id
        chat_id = message.chat.id
        bc_id = message.business_connection_id
        
        save_user(uid, message.from_user.username, message.from_user.first_name)
        
        owner_id = bc_owners.get(bc_id) if bc_id else None
        
        # БАН
        if uid in banned_users or (owner_id and owner_id in banned_users):
            await delete_msg(chat_id, message.message_id, bc_id)
            return
        
        if bc_id:
            if bc_id not in bc_owners:
                try:
                    conn = await bot.get_business_connection(bc_id)
                    bc_owners[bc_id] = conn.user.id
                    save_user(conn.user.id, conn.user.username, conn.user.first_name)
                except: pass
            is_owner = (uid == owner_id) if owner_id else False
        else:
            is_owner = (uid == chat_id)
        
        if bot_id is None:
            me = await bot.get_me()
            bot_id = me.id
        
        if bc_id:
            if bc_id not in active_chats:
                active_chats[bc_id] = set()
            active_chats[bc_id].add(chat_id)
        
        # КЭШ
        if message.text and not message.from_user.is_bot:
            msg_cache[(chat_id, message.message_id)] = {
                "text": message.text,
                "user": message.from_user.first_name or "Пользователь",
                "uid": uid,
                "bc_id": bc_id
            }
            if len(msg_cache) > 5000:
                msg_cache.pop(next(iter(msg_cache)))
        
        # МУТ
        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return
        
        # РЕПЛАЙ-ГАРД
        if chat_id in reply_guard_chats and message.reply_to_message and not is_owner:
            await delete_msg(chat_id, message.message_id, bc_id)
            return
        
        # Только от владельца
        if not is_owner or not message.text:
            return
        
        text = message.text
        low = text.lower().strip()
        
        # ========================================
        # КОМАНДЫ (удаляются после выполнения)
        # ========================================
        
        # .стоп
        if low == ".стоп":
            toggle_setting(chat_id, 'enabled_links', False)
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "❌ Ссылки выключены", business_connection_id=bc_id)
            return
        
        # .старт
        if low == ".старт":
            toggle_setting(chat_id, 'enabled_links', True)
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "✅ Ссылки включены", business_connection_id=bc_id)
            return
        
        # +линк
        if low.startswith("+линк"):
            parts = text.split(maxsplit=1)
            if len(parts) > 1:
                link = parts[1].strip()
                if not link.startswith("http"):
                    link = "https://t.me/" + link.lstrip("@")
                save_link(link)
                await bot.send_message(chat_id, f"✅ Ссылка: {link}", business_connection_id=bc_id)
            await delete_msg(chat_id, message.message_id, bc_id)
            return
        
        # подмена
        if low.startswith("подмена "):
            parts = text.split(maxsplit=2)
            if len(parts) >= 2:
                if parts[1].lower() == "выкл":
                    save_sub(chat_id, None, None)
                    await bot.send_message(chat_id, "✅ Подмена выключена", business_connection_id=bc_id)
                else:
                    mode = 1
                    if len(parts) == 3 and parts[2] in ["1", "2"]:
                        mode = int(parts[2])
                    save_sub(chat_id, parts[1], mode)
                    mode_text = "перед" if mode == 1 else "после"
                    await bot.send_message(chat_id, f"✅ Подмена: '{parts[1]}' ({mode_text})", business_connection_id=bc_id)
            await delete_msg(chat_id, message.message_id, bc_id)
            return
        
        # печать
        if low == "печать -":
            toggle_setting(chat_id, 'typing_disabled', True)
            if bc_id in typing_tasks:
                typing_tasks[bc_id].cancel()
                del typing_tasks[bc_id]
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "❌ Печать выключена", business_connection_id=bc_id)
            return
        
        if low == "печать +":
            toggle_setting(chat_id, 'typing_disabled', False)
            if bc_id and bc_id not in typing_tasks:
                typing_tasks[bc_id] = asyncio.create_task(typing_worker(bc_id))
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "✅ Печать включена", business_connection_id=bc_id)
            return
        
        # реплай
        if low == "+реплай":
            toggle_setting(chat_id, 'reply_guard', True)
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "✅ Реплай-гард включен", business_connection_id=bc_id)
            return
        
        if low == "-реплай":
            toggle_setting(chat_id, 'reply_guard', False)
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "❌ Реплай-гард выключен", business_connection_id=bc_id)
            return
        
        # ss - спам
        if low == "ss":
            await delete_msg(chat_id, message.message_id, bc_id)
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            spam_text = user_spam_texts.get(str(owner_id or uid), "Ты фрик!")
            key = (chat_id, bc_id)
            if key in spam_tasks:
                spam_tasks[key].cancel()
            spam_tasks[key] = asyncio.create_task(spam_worker(chat_id, bc_id, reply_to, spam_text))
            await bot.send_message(chat_id, "♻️ Спам запущен", business_connection_id=bc_id)
            return
        
        # dd - стоп спам
        if low == "dd":
            await delete_msg(chat_id, message.message_id, bc_id)
            key = (chat_id, bc_id)
            if key in spam_tasks:
                spam_tasks[key].cancel()
                del spam_tasks[key]
                await bot.send_message(chat_id, "🛑 Спам остановлен", business_connection_id=bc_id)
            return
        
        # set - установить спам-текст
        if low.startswith("set "):
            spam_text = text[4:].strip()
            save_spam(str(owner_id or uid), spam_text)
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, f"✅ Спам-текст: {spam_text}", business_connection_id=bc_id)
            return
        
        # МУТ
        if low.startswith(".ут") or low.startswith(".мут") or low.startswith("!мут"):
            try:
                # Ищем цифры
                match = re.search(r"\d+", text)
                if not match:
                    await delete_msg(chat_id, message.message_id, bc_id)
                    return
                
                minutes = int(match.group())
                target = message.reply_to_message.from_user if message.reply_to_message else None
                
                if not target:
                    await delete_msg(chat_id, message.message_id, bc_id)
                    await bot.send_message(chat_id, "❌ Ответь на сообщение!", business_connection_id=bc_id)
                    return
                
                target_id = target.id
                target_name = target.first_name or "Пользователь"
                
                mutes[target_id] = {
                    "time": timedelta(minutes=minutes),
                    "until": datetime.now() + timedelta(minutes=minutes)
                }
                asyncio.create_task(unmute_task(target_id, chat_id, bc_id, target_name))
                await delete_msg(chat_id, message.message_id, bc_id)
                
                link = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
                await bot.send_message(chat_id, f"🔇 {link} в муте на {minutes} мин.", parse_mode="HTML", business_connection_id=bc_id)
            except Exception as e:
                logging.error(f"Mute error: {e}")
            return
        
        # РАЗМУТ
        if low == ".размут" or low == "!размут":
            target = message.reply_to_message.from_user if message.reply_to_message else None
            
            if not target:
                await delete_msg(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "❌ Ответь на сообщение!", business_connection_id=bc_id)
                return
            
            target_id = target.id
            target_name = target.first_name or "Пользователь"
            
            mutes.pop(target_id, None)
            await delete_msg(chat_id, message.message_id, bc_id)
            
            link = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
            await bot.send_message(chat_id, f"🔊 С {link} снят мут.", parse_mode="HTML", business_connection_id=bc_id)
            return
        
        # ID
        if low in ["мой ид", "моид"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, f"🆔 Твой ID: <code>{uid}</code>", parse_mode="HTML", business_connection_id=bc_id)
            return
        
        if low in ["твой ид", "твоид"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            target = message.reply_to_message.from_user if message.reply_to_message else None
            if target:
                await bot.send_message(chat_id, f"🆔 ID: <code>{target.id}</code>", parse_mode="HTML", business_connection_id=bc_id)
            return
        
        # КОМАНДЫ
        if low == "!команды":
            await delete_msg(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id,
                "📋 **КОМАНДЫ:**\n\n"
                "`.мут X` — мут (ответь на сообщение)\n"
                "`.размут` — снять мут (ответь)\n"
                "`.стоп` / `.старт` — ссылки\n"
                "`печать +` / `печать -` — печать\n"
                "`подмена текст 1/2/выкл` — подмена\n"
                "`ss` / `dd` — спам / стоп\n"
                "`set текст` — спам-текст\n"
                "`+реплай` / `-реплай` — реплай-гард\n"
                "`+линк ссылка` — сменить ссылку\n"
                "`мой ид` / `твой ид` — ID",
                parse_mode="Markdown",
                business_connection_id=bc_id
            )
            return
        
        # ========================================
        # ПОДМЕНА И ССЫЛКА (РЕДАКТИРОВАНИЕ)
        # ========================================
        final_text = text
        need_edit = False
        parse_mode = None
        
        # Подмена
        if chat_id in substitutions:
            sub = substitutions[chat_id]
            if sub["mode"] == 1:
                final_text = f"{sub['text']} {text}"
            else:
                final_text = f"{text} {sub['text']}"
            need_edit = True
            parse_mode = "HTML"
        
        # Ссылка
        if chat_id in link_chats:
            has_link = False
            if message.entities:
                for entity in message.entities:
                    if entity.type in ["url", "text_link"]:
                        has_link = True
                        break
            
            if not has_link and CHANNEL_LINK not in final_text:
                final_text = f'<a href="{CHANNEL_LINK}"><u>{final_text}</u></a>'
                need_edit = True
                parse_mode = "HTML"
        
        # Редактируем
        if need_edit:
            await edit_msg(chat_id, message.message_id, final_text, bc_id, parse_mode)
            
    except Exception as e:
        logging.error(f"❌ Ошибка: {e}")

# ============ УДАЛЕНИЯ ============
@dp.update()
async def on_update(update: Update):
    try:
        if update.deleted_business_messages:
            data = update.deleted_business_messages
            bc_id = data.business_connection_id
            msg_ids = set(data.message_ids)
            
            for (chat_id, msg_id), cached in list(msg_cache.items()):
                if msg_id in msg_ids:
                    name = cached['user']
                    uid = cached['uid']
                    link = f"<a href='tg://user?id={uid}'>{name}</a>"
                    
                    await bot.send_message(
                        chat_id,
                        f"👤 {link} удалил(а):\n\n💬 {cached['text']}",
                        parse_mode="HTML",
                        business_connection_id=bc_id or cached.get('bc_id')
                    )
                    msg_cache.pop((chat_id, msg_id), None)
    except Exception as e:
        logging.error(f"❌ Ошибка удаления: {e}")

# ============ ВЕБ-СЕРВЕР ============
async def health(request):
    return web.Response(text="OK")

async def web_server():
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/health', health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ============ ЗАПУСК ============
async def main():
    await web_server()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except: pass
    logging.info("🚀 Бот запущен!")
    await dp.start_polling(bot, allowed_updates=[
        "message", "business_connection", "business_message",
        "deleted_business_messages", "callback_query"
    ])

if __name__ == "__main__":
    asyncio.run(main())
