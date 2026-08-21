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

TOKEN_FROM_ENV = os.environ.get('BOT_TOKEN', '8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw')
BOT_TOKEN = TOKEN_FROM_ENV.replace(" ", "").strip()

ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')
DEFAULT_CHANNEL_LINK = "https://t.me/gotrollholl"
BOT_USERNAME = "norikKodBot"

BIND_LINK = f"tg://resolve?domain={BOT_USERNAME}&startattach=biz"

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
bot_id = None
CHANNEL_LINK = DEFAULT_CHANNEL_LINK

bc_owners = {}          
user_usernames = {}     
user_names = {}         
banned_users = set()    

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
        logging.error(f"❌ Ошибка подключения к БД: {e}")

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

async def clear_cmd(chat_id, msg_id, bc_id):
    await delete_msg(chat_id, msg_id, bc_id)

async def typing_worker(bc_id):
    try:
        while True:
            chats = active_chats.get(bc_id, set())
            for cid in list(chats)[-50:]:
                if cid not in typing_disabled_chats:
                    try: await bot.send_chat_action(chat_id=cid, action="typing", business_connection_id=bc_id)
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
            await bot.send_message(chat_id, f"🔊 С пользователя {user_link} снят <b>МУТ</b> (время истекло).", parse_mode="HTML", business_connection_id=bc_id)
        except: pass

@dp.business_connection()
async def handle_bc(bc):
    bc_owners[bc.id] = int(bc.user.id)
    save_user_info(bc.user.id, bc.user.username, bc.user.first_name)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    save_user_info(message.from_user.id, message.from_user.username, message.from_user.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡️ Привязать бота к аккаунту", url=BIND_LINK)]
    ])
    await message.answer(
        "👋 **Привет!**\n\n"
        "Нажми кнопку ниже, чтобы привязать бота к своему аккаунту в настройках Telegram для бизнеса.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Бизнес-клиенты", callback_data="admin_users")],
        [InlineKeyboardButton(text="⚡️ Ссылка подключения", url=BIND_LINK)],
        [InlineKeyboardButton(text="🚫 Забанить / Разбанить", callback_data="admin_ban_prompt")]
    ])

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 **Панель Администратора**", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@dp.callback_query()
async def process_admin_callbacks(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("У вас нет доступа!", show_alert=True)
        return

    data = callback.data
    if data == "admin_stats":
        await callback.message.edit_text(
            f"📊 **СТАТИСТИКА:**\n\n"
            f"• Подключено аккаунтов: `{len(bc_owners)}`\n"
            f"• В бане: `{len(banned_users)}`",
            reply_markup=get_admin_keyboard(), parse_mode="Markdown"
        )
    elif data == "admin_users":
        text = "👥 **БИЗНЕС-КЛИЕНТЫ:**\n\n"
        if not bc_owners: 
            text += "Нет активных подключений."
        else:
            for bc_id, owner_id in bc_owners.items():
                fname = user_names.get(owner_id, "Пользователь")
                user_link = f"<a href='tg://user?id={owner_id}'>{fname}</a>"
                status = "🔴 (Забанен)" if owner_id in banned_users else "🟢 (Активен)"
                text += f"• {user_link} {status}\n"
        await callback.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode="HTML")

    elif data == "admin_ban_prompt":
        await callback.message.edit_text(
            "Для бана или разбана используй команды:\n\n"
            "• `/ban 123456789` или `/ban @username`\n"
            "• `/unban 123456789` или `/unban @username`",
            reply_markup=get_admin_keyboard(), parse_mode="Markdown"
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
            fname = user_names.get(target_id, str(target_id))
            user_link = f"<a href='tg://user?id={target_id}'>{fname}</a>"
            await message.answer(f"🚫 {user_link} <b>заблокирован</b>!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Не найден ID для `{arg}`.", parse_mode="Markdown")
    except:
        await message.answer("Формат: `/ban 123456789` или `/ban @username`", parse_mode="Markdown")

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
        else:
            await message.answer(f"❌ Не найден ID для `{arg}`.", parse_mode="Markdown")
    except:
        await message.answer("Формат: `/unban 123456789` или `/unban @username`", parse_mode="Markdown")

# ================= ОБРАБОТКА СООБЩЕНИЙ =================
@dp.message()
@dp.business_message()
async def handle(message: Message):
    global bot_id, CHANNEL_LINK
    
    try:
        if not message.from_user: return
        
        uid = int(message.from_user.id)
        chat_id = int(message.chat.id)
        bc_id = message.business_connection_id

        save_user_info(uid, message.from_user.username, message.from_user.first_name)

        owner_id = bc_owners.get(bc_id) if bc_id else None

        # 🛑 1. ПРОВЕРКА НА БАН
        if uid in banned_users or (owner_id and owner_id in banned_users):
            owner_name = user_names.get(ADMIN_ID, "Владельцем")
            owner_link = f"<a href='tg://user?id={ADMIN_ID}'>{owner_name}</a>"
            try:
                await bot.send_message(
                    chat_id, 
                    f"⛔ Вы были забанены владельцем {owner_link}.", 
                    parse_mode="HTML", 
                    business_connection_id=bc_id
                )
            except: pass
            return

        if bc_id:
            if bc_id not in bc_owners:
                try:
                    conn_info = await bot.get_business_connection(bc_id)
                    bc_owners[bc_id] = int(conn_info.user.id)
                    save_user_info(conn_info.user.id, conn_info.user.username, conn_info.user.first_name)
                except: pass
            
            is_from_me = (uid == owner_id) if owner_id else False
        else:
            is_from_me = (uid == chat_id)

        if bot_id is None:
            me = await bot.get_me()
            bot_id = me.id

        if bc_id:
            if bc_id not in active_chats: active_chats[bc_id] = set()
            active_chats[bc_id].add(chat_id)

        # 💾 2. ТОЧНОЕ КЭШИРОВАНИЕ ДЛЯ УДАЛЁННЫХ СООБЩЕНИЙ
        if message.text and not message.from_user.is_bot:
            # Используем составной ключ (chat_id, message_id), чтобы ловко находить удалёнки
            cache_key = (chat_id, message.message_id)
            msg_cache[cache_key] = {
                "text": message.text, 
                "user": message.from_user.first_name or "Пользователь",
                "user_id": uid,
                "chat_id": chat_id,
                "bc_id": bc_id
            }
            if len(msg_cache) > 4000:
                msg_cache.pop(next(iter(msg_cache)))

        # 🔇 3. ПРОВЕРКА НА МУТ
        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if chat_id in reply_guard_chats and message.reply_to_message and not is_from_me:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if not is_from_me or not message.text: return

        text_raw = message.text
        low = text_raw.lower().strip()
        task_key = (chat_id, bc_id)
        current_owner = bc_owners.get(bc_id, uid)

        # Команды управления
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

        if low == "ss":
            await clear_cmd(chat_id, message.message_id, bc_id)
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            text = user_spam_texts.get(str(current_owner), "Ты фрик!")
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

        # 🔇 ВЫДАЧА И СНЯТИЕ МУТА С ОПОВЕЩЕНИЕМ
        if low.startswith(".ут ") or low.startswith(".ут") or low.startswith(".мут ") or low.startswith("!мут "):
            try:
                minutes = int(re.search(r"\d+", text_raw).group())
                target_user = message.reply_to_message.from_user if message.reply_to_message else None
                target_id = target_user.id if target_user else chat_id
                target_name = target_user.first_name if target_user else "Пользователь"
                
                mutes[target_id] = {"time": timedelta(minutes=minutes), "until": datetime.now() + timedelta(minutes=minutes)}
                asyncio.create_task(unmute(target_id, chat_id, bc_id, target_name))
                await clear_cmd(chat_id, message.message_id, bc_id)
                
                user_link = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
                await bot.send_message(chat_id, f"🔇 Пользователю {user_link} выдан <b>МУТ</b> на {minutes} мин.", parse_mode="HTML", business_connection_id=bc_id)
            except: pass
            return

        if low == ".размут" or low == "!размут":
            target_user = message.reply_to_message.from_user if message.reply_to_message else None
            target_id = target_user.id if target_user else chat_id
            target_name = target_user.first_name if target_user else "Пользователь"
            
            mutes.pop(target_id, None)
            await clear_cmd(chat_id, message.message_id, bc_id)
            user_link = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
            await bot.send_message(chat_id, f"🔊 С пользователя {user_link} снят <b>МУТ</b>.", parse_mode="HTML", business_connection_id=bc_id)
            return

        if low == "печать -":
            save_setting(chat_id, 'typing_disabled', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            if bc_id in typing_tasks:
                typing_tasks[bc_id].cancel()
                del typing_tasks[bc_id]
            return

        if low == "печать +":
            save_setting(chat_id, 'typing_disabled', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            if bc_id and bc_id not in typing_tasks:
                typing_tasks[bc_id] = asyncio.create_task(typing_worker(bc_id))
            return

        if low == "+реплай":
            save_setting(chat_id, 'reply_guard', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return

        if low == "-реплай":
            save_setting(chat_id, 'reply_guard', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            return

        if low in ["мой ид", "моид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, f"🆔 Твой ID: <code>{uid}</code>", parse_mode="html", business_connection_id=bc_id)
            return

        if low in ["твой ид", "твоид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            target_id = message.reply_to_message.from_user.id if message.reply_to_message else (chat_id if chat_id > 0 else None)
            if target_id:
                await bot.send_message(chat_id, f"🆔 ID: <code>{target_id}</code>", parse_mode="html", business_connection_id=bc_id)
            return

        if low == "!команды":
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id,
                "📋 **КОМАНДЫ:**\n\n"
                "`.мут X` / `.размут` — управление мутом\n"
                "`.стоп` / `.старт` — авто-ссылки\n"
                "`печать +` / `печать -` — вечный статус печати\n"
                "`подмена текст 1/2/выкл` — подмена текста\n"
                "`ss` / `dd` — авто-спам / стоп\n"
                "`set текст` — задать спам-текст\n"
                "`+реплай` / `-реплай` — защита от ответов\n"
                "`+линк ссылка` — сменить ссылку\n"
                "`мой ид` / `твой ид` — узнать ID",
                parse_mode="Markdown", business_connection_id=bc_id
            )
            return

        # Подмена текста и ссылка
        final_text = text_raw
        need_modify = False
        
        if chat_id in substitutions:
            sub = substitutions[chat_id]
            final_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
            need_modify = True
        
        if chat_id in link_chats:
            has_link = any(e.type in ["url", "text_link"] for e in (message.entities or []))
            if not has_link and CHANNEL_LINK not in text_raw:
                final_text = f'<a href="{CHANNEL_LINK}"><u>{final_text}</u></a>'
                need_modify = True
        
        if need_modify:
            await delete_msg(chat_id, message.message_id, bc_id)
            parse_m = "HTML" if (chat_id in link_chats and '<a href=' in final_text) else None
            await bot.send_message(chat_id, final_text, parse_mode=parse_m, disable_web_page_preview=True, business_connection_id=bc_id)
                
    except Exception as e:
        logging.error(f"❌ Ошибка: {e}")

# ================= 🗑 УДАЛЁННЫЕ СООБЩЕНИЯ =================
@dp.update()
async def global_update_handler(update: Update, bot: Bot):
    try:
        if update.deleted_business_messages:
            data = update.deleted_business_messages
            bc_id = data.business_connection_id
            msg_ids = data.message_ids

            # Находим закешированное сообщение
            for (cached_chat_id, cached_msg_id), cached in list(msg_cache.items()):
                if cached_msg_id in msg_ids:
                    user_link = f"<a href='tg://user?id={cached['user_id']}'>{cached['user']}</a>"
                    text_to_send = (
                        f"👤 {user_link}\n"
                        f"🗑 <b>Удалил сообщение ↓</b>\n"
                        f"{cached['text']}"
                    )

                    await bot.send_message(
                        cached_chat_id,
                        text_to_send,
                        parse_mode="html",
                        business_connection_id=bc_id or cached["bc_id"]
                    )
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
    logging.info("🚀 БОТ ЗАПУЩЕН!")
    await dp.start_polling(
        bot, 
        allowed_updates=["message", "business_connection", "business_message", "edited_business_message", "deleted_business_messages", "callback_query"]
    )

if __name__ == "__main__":
    asyncio.run(main())
