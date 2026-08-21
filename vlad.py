import asyncio
import os
import psycopg2
import re
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN_FROM_ENV = os.environ.get('BOT_TOKEN', '8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw')
BOT_TOKEN = TOKEN_FROM_ENV.replace(" ", "").strip()

ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')
DEFAULT_CHANNEL_LINK = "https://t.me/gotrollholl"
BOT_USERNAME = "norikKodBot"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

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
CHANNEL_LINK = DEFAULT_CHANNEL_LINK

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
        logging.error(f"Ошибка сохранения пользователя: {e}")

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
        logging.error(f"Ошибка настройки: {e}")

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
        logging.error(f"Ошибка спам-текста: {e}")

def save_channel_link(link_url):
    global CHANNEL_LINK
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO global_config (key, value) VALUES ('channel_link', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (link_url,))
                conn.commit()
                CHANNEL_LINK = link_url
    except Exception as e:
        logging.error(f"Ошибка ссылки: {e}")

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

async def typing_worker(bc_id):
    try:
        while True:
            chats = active_chats.get(bc_id, set())
            for cid in list(chats)[-50:]:
                if cid not in typing_disabled_chats:
                    try: 
                        await bot.send_chat_action(chat_id=cid, action="typing", business_connection_id=bc_id)
                    except: pass
            await asyncio.sleep(4)
    except asyncio.CancelledError: pass

async def spam_worker(chat_id, bc_id, reply_to, text):
    try:
        words = text.split() if text else ["Ты", "фрик!"]
        while True:
            for word in words:
                await bot.send_message(chat_id, word, business_connection_id=bc_id, reply_to_message_id=reply_to)
                await asyncio.sleep(0.3)
    except asyncio.CancelledError: pass

async def unmute(user_id, chat_id, bc_id, user_name):
    await asyncio.sleep(mutes[user_id]["time"].total_seconds())
    if user_id in mutes:
        mutes.pop(user_id, None)
        user_link = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
        try:
            await bot.send_message(chat_id, f"🔊 С пользователя {user_link} снят <b>МУТ</b>.", parse_mode="HTML", business_connection_id=bc_id)
        except: pass

@dp.business_connection()
async def handle_bc(bc):
    bc_owners[bc.id] = int(bc.user.id)
    save_user_info(bc.user.id, bc.user.username, bc.user.first_name)
    if bc.id not in typing_tasks:
        typing_tasks[bc.id] = asyncio.create_task(typing_worker(bc.id))

@dp.message(Command("start"))
async def cmd_start(message: Message):
    save_user_info(message.from_user.id, message.from_user.username, message.from_user.first_name)
    await message.answer(
        "📌 **Как подключить бота к Telegram Business:**\n\n"
        "1. Открой **Настройки Telegram**.\n"
        "2. Перейди в раздел **Telegram Business** -> **Чат-боты**.\n"
        "3. Добавь этого бота в список разрешённых.",
        parse_mode="Markdown"
    )

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 **Панель Администратора**\nБот работает штатно.", parse_mode="Markdown")

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
            fname = user_names.get(target_id, str(target_id))
            user_link = f"<a href='tg://user?id={target_id}'>{fname}</a>"
            await message.answer(f"🚫 {user_link} <b>заблокирован</b>!", parse_mode="HTML")
    except: pass

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        arg = message.text.split(maxsplit=1)[1]
        target_id = await resolve_user_id(arg)
        if target_id:
            set_user_ban(target_id, False)
            fname = user_names.get(target_id, str(target_id))
            user_link = f"<a href='tg://user?id={target_id}'>{fname}</a>"
            await message.answer(f"✅ {user_link} <b>разблокирован</b>!", parse_mode="HTML")
    except: pass

@dp.message()
@dp.business_message()
async def handle(message: Message):
    global CHANNEL_LINK
    
    try:
        if not message.from_user: return
        
        uid = int(message.from_user.id)
        chat_id = int(message.chat.id)
        bc_id = message.business_connection_id

        save_user_info(uid, message.from_user.username, message.from_user.first_name)

        if bc_id:
            if bc_id not in active_chats: active_chats[bc_id] = set()
            active_chats[bc_id].add(chat_id)
            if bc_id not in typing_tasks and chat_id not in typing_disabled_chats:
                typing_tasks[bc_id] = asyncio.create_task(typing_worker(bc_id))

        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if not message.text: return

        text_raw = message.text
        low = text_raw.lower().strip()
        task_key = (chat_id, bc_id)
        owner_id = bc_owners.get(bc_id, uid)

        if low == ".стоп":
            save_setting(chat_id, 'enabled_links', False)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low == ".старт":
            save_setting(chat_id, 'enabled_links', True)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low.startswith("+линк"):
            parts = text_raw.split(maxsplit=1)
            if len(parts) > 1:
                new_link = parts[1].strip()
                if not new_link.startswith("http"):
                    new_link = "https://t.me/" + new_link.lstrip("@")
                save_channel_link(new_link)
                save_setting(chat_id, 'enabled_links', True)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low.startswith("подмена "):
            parts = text_raw.split(maxsplit=2)
            if len(parts) >= 2:
                if parts[1].lower() == "выкл":
                    save_substitution(chat_id, None, None)
                else:
                    mode = int(parts[2]) if len(parts) == 3 and parts[2] in ["1", "2"] else 1
                    save_substitution(chat_id, parts[1], mode)
                await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low == "ss":
            await delete_msg(chat_id, message.message_id, bc_id)
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            text = user_spam_texts.get(str(owner_id), "Ты фрик!")
            if task_key in spam_tasks: spam_tasks[task_key].cancel()
            spam_tasks[task_key] = asyncio.create_task(spam_worker(chat_id, bc_id, reply_to, text))
            return

        if low == "dd":
            await delete_msg(chat_id, message.message_id, bc_id)
            if task_key in spam_tasks:
                spam_tasks[task_key].cancel()
                del spam_tasks[task_key]
            return

        if low.startswith("set "):
            save_spam_text(str(owner_id), text_raw[4:].strip())
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low.startswith(".ут ") or low.startswith(".ут") or low.startswith(".мут ") or low.startswith("!мут "):
            try:
                minutes = int(re.search(r"\d+", text_raw).group())
                
                if message.reply_to_message and message.reply_to_message.from_user:
                    target_id = message.reply_to_message.from_user.id
                    target_name = message.reply_to_message.from_user.first_name or "Пользователь"
                else:
                    target_id = chat_id
                    target_name = message.chat.first_name or message.chat.title or "Собеседник"

                mutes[target_id] = {"time": timedelta(minutes=minutes), "until": datetime.now() + timedelta(minutes=minutes)}
                asyncio.create_task(unmute(target_id, chat_id, bc_id, target_name))
                
                await delete_msg(chat_id, message.message_id, bc_id)
                user_link = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
                await bot.send_message(chat_id, f"🔇 Пользователю {user_link} выдан <b>МУТ</b> на {minutes} мин.", parse_mode="HTML", business_connection_id=bc_id)
            except Exception as e:
                logging.error(f"Ошибка мута: {e}")
            return

        if low in [".размут", "!размут", "размут"]:
            if message.reply_to_message and message.reply_to_message.from_user:
                target_id = message.reply_to_message.from_user.id
                target_name = message.reply_to_message.from_user.first_name or "Пользователь"
            else:
                target_id = chat_id
                target_name = message.chat.first_name or message.chat.title or "Собеседник"
            
            mutes.pop(target_id, None)
            await delete_msg(chat_id, message.message_id, bc_id)
            user_link = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
            await bot.send_message(chat_id, f"🔊 С пользователя {user_link} снят <b>МУТ</b>.", parse_mode="HTML", business_connection_id=bc_id)
            return

        if low == "печать -":
            save_setting(chat_id, 'typing_disabled', True)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low == "печать +":
            save_setting(chat_id, 'typing_disabled', False)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        final_text = text_raw
        modified = False

        if chat_id in substitutions:
            sub = substitutions[chat_id]
            final_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
            modified = True

        if chat_id in link_chats and CHANNEL_LINK not in final_text:
            final_text = f'<a href="{CHANNEL_LINK}">{final_text}</a>'
            modified = True

        if modified:
            if bc_id:
                try:
                    await bot.edit_business_message_text(
                        business_connection_id=bc_id,
                        chat_id=chat_id,
                        message_id=message.message_id,
                        text=final_text,
                        parse_mode="HTML" if '<a href=' in final_text else None
                    )
                except Exception as e:
                    logging.error(f"Ошибка редактирования бизнес-сообщения: {e}")
            else:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message.message_id,
                        text=final_text,
                        parse_mode="HTML" if '<a href=' in final_text else None
                    )
                except Exception as e:
                    logging.error(f"Ошибка редактирования обычного сообщения: {e}")

    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")

async def handle_ping(request):
    return web.Response(text="Bot OK")

async def main():
    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("🚀 Бот успешно запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
