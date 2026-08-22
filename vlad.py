import asyncio
import os
import psycopg2
import re
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TOKEN_FROM_ENV = os.environ.get('BOT_TOKEN', '8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw')
BOT_TOKEN = TOKEN_FROM_ENV.replace(" ", "").strip()

ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')
DEFAULT_CHANNEL_LINK = "https://t.me/gotrollholl"
REQUIRED_CHANNEL = "@norikx"
REQUIRED_CHANNEL_URL = "https://t.me/norikx"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

mutes = {}              
spam_tasks = {}         
typing_tasks = {}       
user_spam_texts = {}    
link_chats = set()      
reply_guard_chats = set()
typing_globally_disabled = False  
substitutions = {}      
msg_cache = {}          
active_chats_set = set() 
promo_messages = {}     
recent_chats_list = []  
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
                
                target_set = link_chats if setting_type == 'enabled_links' else reply_guard_chats
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
        return False

init_db()

async def delete_msg(chat_id, msg_id, bc_id):
    if bc_id:
        try:
            await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id])
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
        logging.warning(f"Ошибка редактирования сообщения: {e}")
        return False

async def typing_worker():
    global typing_globally_disabled
    try:
        while True:
            if not typing_globally_disabled:
                chats_to_type = list(active_chats_set)[-50:]
                for cid in chats_to_type:
                    try: 
                        await bot.send_chat_action(chat_id=cid, action="typing")
                    except: 
                        pass
            await asyncio.sleep(0.4)
    except asyncio.CancelledError: 
        pass

# РАБОТАЕТ СТРОГО ОТ ТВОЕГО ЛИЦА (С ЗАДЕРЖКОЙ 0.3)
async def spam_worker(chat_id, bc_id, reply_to, text):
    try:
        words = text.split() if text else ["Ты", "фрик!"]
        while True:
            for word in words:
                kwargs = {"chat_id": chat_id, "text": word, "reply_to_message_id": reply_to}
                if bc_id:
                    kwargs["business_connection_id"] = bc_id
                    await bot.send_message(**kwargs)
                else:
                    await bot.send_message(**kwargs)
                await asyncio.sleep(0.3)
    except asyncio.CancelledError: pass

async def unmute(user_id, chat_id, user_name):
    if user_id in mutes:
        await asyncio.sleep((mutes[user_id]["until"] - datetime.now()).total_seconds())
        if user_id in mutes and datetime.now() >= mutes[user_id]["until"]:
            mutes.pop(user_id, None)
            user_link = get_user_mention(user_id, user_name)
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"🔊 С {user_link} снят <b>МУТ</b>.",
                    parse_mode="HTML"
                )
            except: pass

async def promo_broadcaster():
    promo_text = (
        "📢 <b>Подпишись на наш официальный канал!</b>\n\n"
        "⚠️ <b>ВНИМАНИЕ:</b> Не удаляйте это сообщение! "
        "Если вы его удалите, вы автоматически потеряете доступ к боту."
    )
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=REQUIRED_CHANNEL_URL)]
    ])

    while True:
        await asyncio.sleep(7200)
        target_chats = recent_chats_list[-100:]

        for cid in target_chats:
            if cid in banned_users:
                continue
            try:
                msg = await bot.send_message(
                    chat_id=cid,
                    text=promo_text,
                    parse_mode="HTML",
                    reply_markup=promo_kb
                )
                promo_messages[cid] = msg.message_id
            except Exception as e:
                logging.warning(f"Ошибка отправки рекламы в {cid}: {e}")
            await asyncio.sleep(3)

async def check_promo_deletions():
    unban_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Разбан у владельца", url="https://t.me/NorikAmiri")]
    ])
    promo_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=REQUIRED_CHANNEL_URL)]
    ])

    while True:
        await asyncio.sleep(15)
        for cid, msg_id in list(promo_messages.items()):
            if cid in banned_users:
                continue
            try:
                await bot.edit_message_reply_markup(chat_id=cid, message_id=msg_id, reply_markup=promo_kb)
            except TelegramBadRequest as e:
                err = str(e).lower()
                if "message to edit not found" in err or "message can't be edited" in err:
                    set_user_ban(cid, True)
                    promo_messages.pop(cid, None)
                    user_link = get_user_mention(cid)
                    try:
                        await bot.send_message(
                            chat_id=cid,
                            text=f"🚫 {user_link}, вы заблокированы за удаление рекламного сообщения!\n\n"
                                 f"За разбаном напишите владельцу.",
                            parse_mode="HTML",
                            reply_markup=unban_kb
                        )
                    except: pass
            except: pass

@dp.business_connection()
async def handle_bc(bc):
    bc_owners[bc.id] = int(bc.user.id)
    save_user_info(bc.user.id, bc.user.username, bc.user.first_name)
    owner_id = int(bc.user.id)
    owner_mention = get_user_mention(owner_id, bc.user.first_name)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Написать владельцу", url="https://t.me/NorikAmiri")]
    ])
    try:
        await bot.send_message(
            owner_id,
            f"👋 Привет, {owner_mention}!\n\n"
            f"✅ Бот успешно подключен к твоему бизнес-аккаунту!\n\n"
            f"📌 Управление командами: `!команды`",
            parse_mode="HTML",
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Не удалось отправить приветствие: {e}")

@dp.callback_query(F.data.startswith("check_sub:"))
async def check_sub_callback(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[1])
    if callback.from_user.id != user_id:
        await callback.answer("Эта кнопка для другого пользователя!", show_alert=True)
        return

    is_subbed = await check_subscription(user_id)
    if is_subbed:
        try:
            await callback.message.delete()
        except: pass
        await callback.answer("✅ Подписка подтверждена! Команда выполнена.", show_alert=True)
    else:
        await callback.answer("❌ Вы всё ещё не подписались на канал!", show_alert=True)

@dp.callback_query(F.data.in_(["menu_admin", "menu_functions", "menu_connect", "menu_back"]))
async def process_start_menu_callbacks(callback: CallbackQuery):
    data = callback.data
    uid = callback.from_user.id

    if data == "menu_admin":
        if uid != ADMIN_ID:
            await callback.answer("❌ Эта кнопка доступна только администратору!", show_alert=True)
            return
        await callback.message.edit_text(
            "👑 **Панель Администратора**\n\nИспользуй кнопки ниже:",
            reply_markup=get_admin_keyboard(),
            parse_mode="Markdown"
        )
    elif data == "menu_functions":
        text = (
            "📋 **ФУНКЦИИ И КОМАНДЫ БОТА:**\n\n"
            "`.мут X` / `.размут` — управление мутом\n"
            "`.стоп` / `.старт` — авто-ссылки\n"
            "`печать +` / `печать -` — вечный статус печати\n"
            "`подмена текст 1/2/выкл` — подмена текста\n"
            "`ss` / `dd` — авто-спам / стоп\n"
            "`set текст` — задать спам-текст\n"
            "`+реплай` / `-реплай` — защита от ответов\n"
            "`+линк ссылка` — сменить ссылку\n"
            "`мой ид` / `твой ид` — кликабельный First Name"
        )
        await callback.message.edit_text(text, parse_mode="Markdown")
    elif data == "menu_connect":
        text = (
            "📌 **Как подключить бота к бизнес-аккаунту:**\n\n"
            "1. Перейдите в Telegram -> <b>Настройки</b>.\n"
            "2. Выберите пункт <b>Telegram Business</b> (или Мой профиль / Автоматизация чатов).\n"
            "3. Найдите раздел <b>Чат-боты</b> и добавьте этого бота.\n"
            "4. Готово! После подключения бот начнет автоматически обрабатывать ваши чаты."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]
        ])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    elif data == "menu_back":
        start_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Админ-панель", callback_data="menu_admin")],
            [InlineKeyboardButton(text="📋 Функции бота", callback_data="menu_functions")],
            [InlineKeyboardButton(text="🔗 Подключить бота", callback_data="menu_connect")]
        ])
        await callback.message.edit_text(
            "👋 **Привет!**\n\nВыбери нужный раздел с помощью кнопок ниже:",
            parse_mode="Markdown",
            reply_markup=start_kb
        )
    await callback.answer()

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Бизнес-клиенты", callback_data="admin_users")],
        [InlineKeyboardButton(text="🚫 Забанить / Разбанить", callback_data="admin_ban_prompt")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="menu_back")]
    ])

@dp.callback_query(F.data.in_(["admin_stats", "admin_users", "admin_ban_prompt"]))
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
                user_link = get_user_mention(owner_id)
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
            user_link = get_user_mention(target_id)
            await message.answer(f"🚫 {user_link} <b>заблокирован</b>!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Пользователь `{arg}` не найден.", parse_mode="Markdown")
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
            user_link = get_user_mention(target_id)
            await message.answer(f"✅ {user_link} <b>разблокирован</b>!", parse_mode="HTML")
        else:
            await message.answer(f"❌ Пользователь `{arg}` не найден.", parse_mode="Markdown")
    except:
        await message.answer("Формат: `/unban 123456789` или `/unban @username`", parse_mode="Markdown")


@dp.message()
@dp.business_message()
async def handle(message: Message):
    global bot_id, CHANNEL_LINK, typing_globally_disabled
    
    try:
        if not message.from_user: return
        
        uid = int(message.from_user.id)
        chat_id = int(message.chat.id)
        bc_id = message.business_connection_id

        save_user_info(uid, message.from_user.username, message.from_user.first_name)
        owner_id = bc_owners.get(bc_id) if bc_id else None

        active_chats_set.add(chat_id)

        if chat_id > 0 and not bc_id:
            if chat_id in recent_chats_list:
                recent_chats_list.remove(chat_id)
            recent_chats_list.append(chat_id)
            if len(recent_chats_list) > 100:
                recent_chats_list.pop(0)

        if uid in banned_users or (owner_id and owner_id in banned_users):
            if message.chat.type == "private" and not bc_id:
                unban_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💬 Разбан у владельца", url="https://t.me/NorikAmiri")]
                ])
                await message.answer("❌ Вы заблокированы. За разбаном напишите владельцу.", reply_markup=unban_kb)
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
            is_from_me = (message.chat.type == "private") or (message.chat.type in ["group", "supergroup"] and not message.from_user.is_bot)

        if bot_id is None:
            me = await bot.get_me()
            bot_id = me.id

        if message.text and not message.from_user.is_bot and bc_id:
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

        if chat_id in reply_guard_chats and message.reply_to_message and not is_from_me:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        if not message.text: return

        text_raw = message.text
        low = text_raw.lower().strip()
        task_key = (chat_id, bc_id)
        current_owner = owner_id if owner_id else uid

        bot_commands_list = [
            ".стоп", ".старт", "+линк", "подмена", "печать -", "печать +",
            "+реплай", "-реплай", "ss", "dd", "set", ".мут", "!мут", ".ут",
            ".размут", "!размут", "мой ид", "моид", "твой ид", "твоид", "!команды", "/start"
        ]
        
        is_bot_command = any(low.startswith(cmd) for cmd in bot_commands_list)

        if is_bot_command and uid != ADMIN_ID:
            is_subbed = await check_subscription(uid)
            if not is_subbed:
                await delete_msg(chat_id, message.message_id, bc_id)
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📢 Подписаться на канал", url=REQUIRED_CHANNEL_URL)],
                    [InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_sub:{uid}")]
                ])
                await bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ <b>Для использования команд бота необходима подписка на канал!</b>\n\nПожалуйста, подпишитесь, затем нажмите кнопку ниже.",
                    parse_mode="HTML",
                    reply_markup=kb
                )
                return

        if message.chat.type == "private" and not bc_id and low == "/start":
            start_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="👑 Админ-панель", callback_data="menu_admin")],
                [InlineKeyboardButton(text="📋 Функции бота", callback_data="menu_functions")],
                [InlineKeyboardButton(text="🔗 Подключить бота", callback_data="menu_connect")]
            ])
            await message.answer(
                "👋 **Привет!**\n\nВыбери нужный раздел с помощью кнопок ниже:",
                parse_mode="Markdown",
                reply_markup=start_kb
            )
            return

        if low == ".стоп":
            await delete_msg(chat_id, message.message_id, bc_id)
            save_setting(chat_id, 'enabled_links', False)
            return

        if low == ".старт":
            await delete_msg(chat_id, message.message_id, bc_id)
            save_setting(chat_id, 'enabled_links', True)
            return

        if low.startswith("+линк"):
            await delete_msg(chat_id, message.message_id, bc_id)
            parts = text_raw.split(maxsplit=1)
            if len(parts) > 1:
                new_link = parts[1].strip()
                if not new_link.startswith("http"):
                    new_link = "https://t.me/" + new_link.lstrip("@")
                save_channel_link(new_link)
            return

        if low.startswith("подмена "):
            await delete_msg(chat_id, message.message_id, bc_id)
            parts = text_raw.split(maxsplit=2)
            if len(parts) >= 2:
                if parts[1].lower() == "выкл":
                    save_substitution(chat_id, None, None)
                else:
                    mode = int(parts[2]) if len(parts) == 3 and parts[2] in ["1", "2"] else 1
                    save_substitution(chat_id, parts[1], mode)
            return

        if low == "печать -":
            await delete_msg(chat_id, message.message_id, bc_id)
            typing_globally_disabled = True
            return

        if low == "печать +":
            await delete_msg(chat_id, message.message_id, bc_id)
            typing_globally_disabled = False
            return

        if low == "+реплай":
            await delete_msg(chat_id, message.message_id, bc_id)
            save_setting(chat_id, 'reply_guard', True)
            return

        if low == "-реплай":
            await delete_msg(chat_id, message.message_id, bc_id)
            save_setting(chat_id, 'reply_guard', False)
            return

        if low == "ss":
            await delete_msg(chat_id, message.message_id, bc_id)
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            text = user_spam_texts.get(str(current_owner), "Ты фрик!")
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
            await delete_msg(chat_id, message.message_id, bc_id)
            save_spam_text(str(current_owner), text_raw[4:].strip())
            return

        if low.startswith(".мут") or low.startswith("!мут") or low.startswith(".ут"):
            await delete_msg(chat_id, message.message_id, bc_id)
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
                asyncio.create_task(unmute(target_id, chat_id, target_name))
                
                user_link = get_user_mention(target_id, target_name)
                kwargs = {
                    "chat_id": chat_id,
                    "text": f"🔇 {user_link} выдан <b>МУТ</b> на {minutes} мин.",
                    "parse_mode": "HTML"
                }
                if bc_id:
                    kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
            except: pass
            return

        if low in [".размут", "!размут"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            if message.reply_to_message and message.reply_to_message.from_user:
                target_user = message.reply_to_message.from_user
                target_id = target_user.id
                target_name = target_user.first_name
            else:
                target_id = chat_id
                target_name = message.chat.first_name or "Пользователь"
            
            mutes.pop(target_id, None)
            user_link = get_user_mention(target_id, target_name)
            
            kwargs = {
                "chat_id": chat_id,
                "text": f"🔊 С {user_link} снят <b>МУТ</b>.",
                "parse_mode": "HTML"
            }
            if bc_id:
                kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return

        if low in ["мой ид", "моид"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            my_link = get_user_mention(uid, message.from_user.first_name)
            kwargs = {
                "chat_id": chat_id,
                "text": f"🆔 {my_link}",
                "parse_mode": "HTML"
            }
            if bc_id:
                kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return

        if low in ["твой ид", "твоид"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            target_user = message.reply_to_message.from_user if message.reply_to_message else None
            target_id = target_user.id if target_user else (chat_id if chat_id > 0 else None)
            if target_id:
                t_fname = target_user.first_name if target_user else None
                t_link = get_user_mention(target_id, t_fname)
                kwargs = {
                    "chat_id": chat_id,
                    "text": f"🆔 {t_link}",
                    "parse_mode": "HTML"
                }
                if bc_id:
                    kwargs["business_connection_id"] = bc_id
                await bot.send_message(**kwargs)
            return

        if low == "!команды":
            await delete_msg(chat_id, message.message_id, bc_id)
            kwargs = {
                "chat_id": chat_id,
                "text": (
                    "📋 **КОМАНДЫ:**\n\n"
                    "`.мут X` / `.размут` — управление мутом\n"
                    "`.стоп` / `.старт` — авто-ссылки\n"
                    "`печать +` / `печать -` — вечный статус печати\n"
                    "`подмена текст 1/2/выкл` — подмена текста\n"
                    "`ss` / `dd` — авто-спам / стоп\n"
                    "`set текст` — задать спам-текст\n"
                    "`+реплай` / `-реплай` — защита от ответов\n"
                    "`+линк ссылка` — сменить ссылку\n"
                    "`мой ид` / `твой ид` — кликабельный First Name"
                ),
                "parse_mode": "Markdown"
            }
            if bc_id:
                kwargs["business_connection_id"] = bc_id
            await bot.send_message(**kwargs)
            return

        if bc_id:
            final_text = text_raw
            need_modify = False
            parse_mode = None
            
            if chat_id in substitutions:
                sub = substitutions[chat_id]
                final_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
                need_modify = True
                parse_mode = "HTML"
            
            if chat_id in link_chats:
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
                        "parse_mode": "html"
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

    asyncio.create_task(promo_broadcaster())
    asyncio.create_task(check_promo_deletions())
    asyncio.create_task(typing_worker())

    logging.info("🚀 БОТ ЗАПУЩЕН!")
    await dp.start_polling(
        bot, 
        allowed_updates=["message", "business_connection", "business_message", "edited_business_message", "deleted_business_messages", "callback_query"]
    )

if __name__ == "__main__":
    asyncio.run(main())
