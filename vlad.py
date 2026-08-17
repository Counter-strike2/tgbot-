import os
import asyncio
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessConnection, Update
from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, BigInteger, String, Integer, DateTime

# ===== ТОКЕН БОТА =====
BOT_TOKEN = "8959860095:AAFsRWRSFQOQ84ww_HwxQ2IaI_24DYxTN2o"
CHANNEL_LINK = "https://t.me/gotrollholl"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== БАЗА ДАННЫХ (AIVEN) =====
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("❌ DATABASE_URL не найдена! Добавь переменную на Render.")

ASYNC_DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://")

Base = declarative_base()
engine = create_async_engine(ASYNC_DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class ChatSetting(Base):
    __tablename__ = 'chat_settings'
    chat_id = Column(BigInteger, primary_key=True)
    setting_type = Column(String, primary_key=True)

class Substitution(Base):
    __tablename__ = 'substitutions'
    chat_id = Column(BigInteger, primary_key=True)
    text = Column(String)
    mode = Column(Integer)

class UserSpam(Base):
    __tablename__ = 'user_spam'
    user_id = Column(BigInteger, primary_key=True)
    spam_text = Column(String)

class Mute(Base):
    __tablename__ = 'mutes'
    user_id = Column(BigInteger, primary_key=True)
    until = Column(DateTime)
    chat_id = Column(BigInteger)

# ===== ИНИЦИАЛИЗАЦИЯ БАЗЫ =====
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def load_settings():
    global link_chats, reply_guard_chats, typing_disabled_chats, substitutions, user_spam_texts, mutes
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT chat_id FROM chat_settings WHERE setting_type = 'enabled_links'"))
        link_chats = {row[0] for row in result.fetchall()}
        
        result = await session.execute(text("SELECT chat_id FROM chat_settings WHERE setting_type = 'reply_guard'"))
        reply_guard_chats = {row[0] for row in result.fetchall()}
        
        result = await session.execute(text("SELECT chat_id FROM chat_settings WHERE setting_type = 'typing_disabled'"))
        typing_disabled_chats = {row[0] for row in result.fetchall()}
        
        result = await session.execute(text("SELECT chat_id, text, mode FROM substitutions"))
        substitutions = {row[0]: {"text": row[1], "mode": row[2]} for row in result.fetchall()}
        
        result = await session.execute(text("SELECT user_id, spam_text FROM user_spam"))
        user_spam_texts = {row[0]: row[1] for row in result.fetchall()}
        
        result = await session.execute(text("SELECT user_id, until, chat_id FROM mutes"))
        for row in result.fetchall():
            if row[1] > datetime.now():
                mutes[row[0]] = {"until": row[1], "chat_id": row[2], "time": row[1] - datetime.now()}

# ===== ФУНКЦИИ СОХРАНЕНИЯ =====
async def save_setting(chat_id, setting_type, enabled):
    async with AsyncSessionLocal() as conn:
        if enabled:
            await conn.execute(text("INSERT OR IGNORE INTO chat_settings VALUES (:chat_id, :setting_type)"), {"chat_id": chat_id, "setting_type": setting_type})
        else:
            await conn.execute(text("DELETE FROM chat_settings WHERE chat_id = :chat_id AND setting_type = :setting_type"), {"chat_id": chat_id, "setting_type": setting_type})
        await conn.commit()
    
    if setting_type == 'enabled_links':
        if enabled: link_chats.add(chat_id)
        else: link_chats.discard(chat_id)
    elif setting_type == 'reply_guard':
        if enabled: reply_guard_chats.add(chat_id)
        else: reply_guard_chats.discard(chat_id)
    else:
        if enabled: typing_disabled_chats.add(chat_id)
        else: typing_disabled_chats.discard(chat_id)

async def save_substitution(chat_id, text, mode):
    async with AsyncSessionLocal() as conn:
        if text is None:
            await conn.execute(text("DELETE FROM substitutions WHERE chat_id = :chat_id"), {"chat_id": chat_id})
            substitutions.pop(chat_id, None)
        else:
            await conn.execute(text("INSERT OR REPLACE INTO substitutions VALUES (:chat_id, :text, :mode)"), {"chat_id": chat_id, "text": text, "mode": mode})
            substitutions[chat_id] = {"text": text, "mode": mode}
        await conn.commit()

async def save_user_spam(user_id, text):
    async with AsyncSessionLocal() as conn:
        await conn.execute(text("INSERT OR REPLACE INTO user_spam VALUES (:user_id, :spam_text)"), {"user_id": user_id, "spam_text": text})
        await conn.commit()

async def save_mute(user_id, until, chat_id):
    async with AsyncSessionLocal() as conn:
        await conn.execute(text("INSERT OR REPLACE INTO mutes VALUES (:user_id, :until, :chat_id)"), {"user_id": user_id, "until": until, "chat_id": chat_id})
        await conn.commit()

async def delete_mute(user_id):
    async with AsyncSessionLocal() as conn:
        await conn.execute(text("DELETE FROM mutes WHERE user_id = :user_id"), {"user_id": user_id})
        await conn.commit()

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
mutes = {}              
spam_tasks = {}         
typing_tasks = {}       
user_spam_texts = {}    
link_chats = set()      
reply_guard_chats = set() 
typing_disabled_chats = set()
substitutions = {}      
msg_cache = {}          
active_chats = set()    
bot_id = None
owner_id = None         

@dp.business_connection()
async def business_conn_handler(bc: BusinessConnection):
    global owner_id
    if owner_id is None:
        owner_id = bc.user.id

async def delete_msg(chat_id, msg_id, bc_id):
    try:
        await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id])
    except:
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

async def clear_cmd(chat_id, msg_id, bc_id):
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=".", business_connection_id=bc_id)
    except:
        pass

async def typing_worker(chat_id, bc_id):
    try:
        while True:
            if chat_id not in typing_disabled_chats:
                await bot.send_chat_action(chat_id=chat_id, action="typing", business_connection_id=bc_id)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

async def spam_worker(chat_id, bc_id, reply_to, text):
    try:
        while True:
            for word in text.split():
                await bot.send_message(chat_id, word, business_connection_id=bc_id, reply_to_message_id=reply_to)
                await asyncio.sleep(0.4)
    except asyncio.CancelledError:
        pass

async def unmute(user_id):
    if user_id not in mutes:
        return
    wait_time = (mutes[user_id]["until"] - datetime.now()).total_seconds()
    if wait_time > 0:
        await asyncio.sleep(wait_time)
    if user_id in mutes:
        del mutes[user_id]
        await delete_mute(user_id)

@dp.business_message()
async def handle(message: Message):
    global bot_id, owner_id, CHANNEL_LINK
    
    try:
        if not message.from_user:
            return
        
        if bot_id is None:
            me = await bot.get_me()
            bot_id = me.id
            
        uid = message.from_user.id
        if uid == bot_id:
            return
            
        chat_id = message.chat.id
        bc_id = message.business_connection_id
        
        if not bc_id:
            return

        if owner_id is None:
            owner_id = uid

        if chat_id not in active_chats:
            if len(active_chats) >= 50:
                old_chat = active_chats.pop()
                if old_chat in typing_tasks:
                    typing_tasks[old_chat].cancel()
                    del typing_tasks[old_chat]
            active_chats.add(chat_id)
            
            if chat_id not in typing_tasks:
                typing_tasks[chat_id] = asyncio.create_task(typing_worker(chat_id, bc_id))

        if not message.text:
            return

        text_raw = message.text
        low = text_raw.lower().strip()

        if owner_id is not None and uid != owner_id:
            msg_cache[message.message_id] = {
                "text": text_raw, 
                "user": message.from_user.first_name,
                "user_id": uid,
                "chat_id": chat_id,
                "bc_id": bc_id
            }
            if len(msg_cache) > 3000:
                msg_cache.pop(next(iter(msg_cache)))
                
            if chat_id in reply_guard_chats and message.reply_to_message:
                await delete_msg(chat_id, message.message_id, bc_id)
                return
                
            if uid in mutes and datetime.now() < mutes[uid]["until"]:
                await delete_msg(chat_id, message.message_id, bc_id)
                return

        is_command = low in ["ss", "dd", ".стоп", ".старт", "печать -", "печать +", "+реплай", "-реплай", ".размут", "!команды", "мой ид", "моид", "твой ид", "твоид"] or \
                     low.startswith(("set ", ".мут ", "подмена ", "+линк"))

        if not is_command:
            if chat_id in link_chats:
                try:
                    if not message.entities and CHANNEL_LINK not in text_raw:
                        new_text = f'<a href="{CHANNEL_LINK}"><u>{text_raw}</u></a>'
                        await bot.edit_message_text(chat_id, message.message_id, new_text, parse_mode="HTML", disable_web_page_preview=True, business_connection_id=bc_id)
                        return
                except:
                    pass
            
            if chat_id in substitutions:
                try:
                    sub = substitutions[chat_id]
                    new_text = f"{sub['text']} {text_raw}" if sub["mode"] == 1 else f"{text_raw} {sub['text']}"
                    await bot.edit_message_text(chat_id, message.message_id, new_text, business_connection_id=bc_id)
                    return
                except:
                    pass

        if uid == owner_id:
            if low == "!команды":
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id,
                    "📋 **КОМАНДЫ БОТА:**\n\n"
                    "`.мут X` — мут\n"
                    "`.размут` — снять мут\n"
                    "`.стоп` / `.старт` — ссылки\n"
                    "`печать +` / `печать -` — вечная печать\n"
                    "`подмена текст 1/2` — подмена\n"
                    "`подмена выкл` — выкл подмену\n"
                    "`ss` — спам\n"
                    "`dd` — стоп спам\n"
                    "`set текст` — текст спама\n"
                    "`+реплай` / `-реплай` — защита\n"
                    "`+линк` — сменить ссылку\n"
                    "`мой ид` / `твой ид` — ID",
                    parse_mode="Markdown", business_connection_id=bc_id
                )
                return

            if low in ["мой ид", "моид"]:
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, f"🆔 Твой ID: <code>{uid}</code>", parse_mode="html", business_connection_id=bc_id)
                return

            if low in ["твой ид", "твоид"]:
                await clear_cmd(chat_id, message.message_id, bc_id)
                if message.reply_to_message:
                    await bot.send_message(chat_id, f"🆔 ID пользователя: <code>{message.reply_to_message.from_user.id}</code>", parse_mode="html", business_connection_id=bc_id)
                else:
                    await bot.send_message(chat_id, "❌ Ответьте на сообщение пользователя!", business_connection_id=bc_id)
                return

            if low == ".стоп":
                await save_setting(chat_id, 'enabled_links', False)
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "🛑 Авто-ссылки выключены.", business_connection_id=bc_id)
                return

            if low == ".старт":
                await save_setting(chat_id, 'enabled_links', True)
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "✅ Авто-ссылки включены!", business_connection_id=bc_id)
                return

            if low == "печать -":
                await save_setting(chat_id, 'typing_disabled', True)
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "⌨️ Печать выключена.", business_connection_id=bc_id)
                return

            if low == "печать +":
                await save_setting(chat_id, 'typing_disabled', False)
                await clear_cmd(chat_id, message.message_id, bc_id)
                if chat_id not in typing_tasks:
                    typing_tasks[chat_id] = asyncio.create_task(typing_worker(chat_id, bc_id))
                await bot.send_message(chat_id, "⌨️ Печать включена!", business_connection_id=bc_id)
                return

            if low.startswith("подмена "):
                parts = text_raw.split(maxsplit=2)
                if len(parts) >= 2:
                    if parts[1].lower() == "выкл":
                        await save_substitution(chat_id, None, None)
                        await bot.send_message(chat_id, "❌ Подмена выключена.", business_connection_id=bc_id)
                    else:
                        mode = int(parts[2]) if len(parts) == 3 and parts[2] in ["1", "2"] else 1
                        await save_substitution(chat_id, parts[1], mode)
                        await bot.send_message(chat_id, f"🔄 Подмена сохранена! (Режим {mode})", business_connection_id=bc_id)
                    await clear_cmd(chat_id, message.message_id, bc_id)
                return

            if low.startswith("set "):
                new_text = text_raw[4:].strip()
                user_spam_texts[uid] = new_text
                await save_user_spam(uid, new_text)
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, f"📝 Текст спама изменён.", business_connection_id=bc_id)
                return

            if low == "ss":
                await clear_cmd(chat_id, message.message_id, bc_id)
                reply_to = message.reply_to_message.message_id if message.reply_to_message else None
                text = user_spam_texts.get(uid, "Ты фрик!")
                if chat_id in spam_tasks:
                    spam_tasks[chat_id].cancel()
                task = asyncio.create_task(spam_worker(chat_id, bc_id, reply_to, text))
                spam_tasks[chat_id] = task
                await bot.send_message(chat_id, "🚀 Спам запущен!", business_connection_id=bc_id)
                return

            if low == "dd":
                await clear_cmd(chat_id, message.message_id, bc_id)
                if chat_id in spam_tasks:
                    spam_tasks[chat_id].cancel()
                    del spam_tasks[chat_id]
                    await bot.send_message(chat_id, "🛑 Спам остановлен!", business_connection_id=bc_id)
                return

            if low == "+реплай":
                await save_setting(chat_id, 'reply_guard', True)
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "🛡 Защита включена!", business_connection_id=bc_id)
                return

            if low == "-реплай":
                await save_setting(chat_id, 'reply_guard', False)
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "🛡 Защита выключена.", business_connection_id=bc_id)
                return

            if low.startswith(".мут "):
                try:
                    minutes = int(re.search(r"\d+", text_raw).group())
                    target = message.reply_to_message.from_user.id if message.reply_to_message else chat_id
                    until = datetime.now() + timedelta(minutes=minutes)
                    mutes[target] = {"until": until, "chat_id": chat_id, "time": timedelta(minutes=minutes)}
                    await save_mute(target, until, chat_id)
                    asyncio.create_task(unmute(target))
                    await clear_cmd(chat_id, message.message_id, bc_id)
                    await bot.send_message(chat_id, f"🔇 Замучен на {minutes} мин.", business_connection_id=bc_id)
                except:
                    pass
                return

            if low == ".размут":
                target = message.reply_to_message.from_user.id if message.reply_to_message else chat_id
                if target in mutes:
                    del mutes[target]
                    await delete_mute(target)
                    await clear_cmd(chat_id, message.message_id, bc_id)
                    await bot.send_message(chat_id, "🔊 Мут снят!", business_connection_id=bc_id)
                return

            if low.startswith("+линк"):
                parts = text_raw.split(maxsplit=1)
                if len(parts) == 1:
                    await bot.send_message(chat_id, f"🔗 {CHANNEL_LINK}", business_connection_id=bc_id)
                else:
                    new_link = parts[1].strip()
                    if not new_link.startswith("http"):
                        new_link = "https://t.me/" + new_link.lstrip("@")
                    CHANNEL_LINK = new_link
                    await clear_cmd(chat_id, message.message_id, bc_id)
                    await bot.send_message(chat_id, f"🔗 Ссылка изменена!", business_connection_id=bc_id)
                return
                
    except Exception as e:
        print(f"❌ Ошибка: {e}")

@dp.business_message()
async def cache_incoming(message: Message):
    if not message.from_user or not message.text:
        return
    if owner_id is not None and message.from_user.id != owner_id:
        msg_cache[message.message_id] = {
            "text": message.text,
            "user": message.from_user.first_name,
            "user_id": message.from_user.id,
            "chat_id": message.chat.id,
            "bc_id": message.business_connection_id
        }
        if len(msg_cache) > 3000:
            msg_cache.pop(next(iter(msg_cache)))

@dp.update()
async def global_update_handler(update: Update, bot: Bot):
    try:
        if update.deleted_business_messages:
            data = update.deleted_business_messages
            bc_id = data.business_connection_id
            msg_ids = data.message_ids
            
            for msg_id in msg_ids:
                if msg_id in msg_cache:
                    cached = msg_cache[msg_id]
                    user_link = f"<a href='tg://user?id={cached['user_id']}'>{cached['user']}</a>"
                    
                    await bot.send_message(
                        cached["chat_id"],
                        f"👤 {user_link}\n🗑 Удалил ↓\n{cached['text']}",
                        parse_mode="html",
                        business_connection_id=bc_id or cached["bc_id"]
                    )
                    msg_cache.pop(msg_id, None)
    except Exception as e:
        print(f"❌ Ошибка обработчика удалений: {e}")

# ===== HEALTH-CHECK =====
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("✅ Health check server started on port 10000")

async def main():
    await init_db()
    await load_settings()
    await start_health_server()
    print("🔥 БОТ ЗАПУЩЕН НА POLLING!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
