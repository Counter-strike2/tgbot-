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
                conn.commit()
                
                cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='enabled_links'")
                for row in cur.fetchall(): link_chats.add(int(row[0]))
                
                cur.execute("SELECT chat_id, text, mode FROM substitutions")
                for row in cur.fetchall(): substitutions[int(row[0])] = {"text": row[1], "mode": row[2]}

                cur.execute("SELECT value FROM global_config WHERE key='channel_link'")
                row = cur.fetchone()
                if row: CHANNEL_LINK = row[0]
    except Exception as e:
        logging.error(f"❌ Ошибка БД: {e}")

def save_setting(chat_id, setting_type, enabled):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if enabled:
                    cur.execute("INSERT INTO chat_settings (chat_id, setting_type) VALUES (%s, %s) ON CONFLICT DO NOTHING", (chat_id, setting_type))
                    link_chats.add(chat_id)
                else:
                    cur.execute("DELETE FROM chat_settings WHERE chat_id = %s AND setting_type = %s", (chat_id, setting_type))
                    link_chats.discard(chat_id)
                conn.commit()
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

@dp.message(Command("start"))
async def cmd_start(message: Message):
    # Прямая ссылка на добавление бота в бизнес вместо настроек
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Подключить к Telegram", url=f"https://t.me/{BOT_USERNAME}?startgroup=true")]
    ])
    await message.answer("Жми кнопку ниже для подключения:", reply_markup=kb)

@dp.message()
@dp.business_message()
async def handle(message: Message):
    try:
        if not message.from_user or not message.text: return
        
        uid = int(message.from_user.id)
        chat_id = int(message.chat.id)
        bc_id = message.business_connection_id

        # Авто-удаление сообщений замученного пользователя
        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        text_raw = message.text
        low = text_raw.lower().strip()

        # 1. КОМАНДА ПОДМЕНЫ И СТАРТ/СТОП ЛИНКОВ
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

        if low == ".старт":
            save_setting(chat_id, 'enabled_links', True)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low == ".стоп":
            save_setting(chat_id, 'enabled_links', False)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if low.startswith("+линк"):
            parts = text_raw.split(maxsplit=1)
            if len(parts) > 1:
                new_link = parts[1].strip()
                if not new_link.startswith("http"):
                    new_link = "https://t.me/" + new_link.lstrip("@")
                save_channel_link(new_link)
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        # 2. МУТ (Точное определение собеседника)
        if low.startswith(".мут ") or low.startswith("!мут ") or low.startswith(".ут "):
            try:
                minutes = int(re.search(r"\d+", text_raw).group())
                
                # Если ответ на сообщение
                if message.reply_to_message and message.reply_to_message.from_user:
                    target_id = message.reply_to_message.from_user.id
                    target_name = message.reply_to_message.from_user.first_name or "Пользователь"
                else:
                    # В ЛС берутся данные чата собеседника
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

        # 3. АВТО-ПРИМЕНЕНИЕ ПОДМЕНЫ И ЛИНКА К ОБЫЧНЫМ СООБЩЕНИЯМ
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
            parse_m = "HTML" if '<a href=' in final_text else None
            if bc_id:
                await bot.edit_business_message_text(
                    business_connection_id=bc_id,
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=final_text,
                    parse_mode=parse_m,
                    disable_web_page_preview=True
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message.message_id,
                    text=final_text,
                    parse_mode=parse_m,
                    disable_web_page_preview=True
                )

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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
