import os
import asyncio
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessConnection, Update
from aiohttp import web
from sqlalchemy import create_engine, text
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

# Конвертируем синхронную URL в асинхронную для asyncpg
ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

Base = declarative_base()
engine = create_async_engine(ASYNC_DATABASE_URL, echo=True)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Модели таблиц
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

        print(f"✅ Загружено из БД: ссылок={len(link_chats)}, подмен={len(substitutions)}")

# ===== ФУНКЦИИ СОХРАНЕНИЯ =====
async def save_setting(chat_id, setting_type, enabled):
    async with AsyncSessionLocal() as session:
        if enabled:
            await session.execute(text("INSERT OR IGNORE INTO chat_settings VALUES (:chat_id, :setting_type)"), {"chat_id": chat_id, "setting_type": setting_type})
        else:
            await session.execute(text("DELETE FROM chat_settings WHERE chat_id = :chat_id AND setting_type = :setting_type"), {"chat_id": chat_id, "setting_type": setting_type})
        await session.commit()
    
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
    async with AsyncSessionLocal() as session:
        if text is None:
            await session.execute(text("DELETE FROM substitutions WHERE chat_id = :chat_id"), {"chat_id": chat_id})
            substitutions.pop(chat_id, None)
        else:
            await session.execute(text("INSERT OR REPLACE INTO substitutions VALUES (:chat_id, :text, :mode)"), {"chat_id": chat_id, "text": text, "mode": mode})
            substitutions[chat_id] = {"text": text, "mode": mode}
        await session.commit()

async def save_user_spam(user_id, text):
    async with AsyncSessionLocal() as session:
        await session.execute(text("INSERT OR REPLACE INTO user_spam VALUES (:user_id, :spam_text)"), {"user_id": user_id, "spam_text": text})
        await session.commit()

async def save_mute(user_id, until, chat_id):
    async with AsyncSessionLocal() as session:
        await session.execute(text("INSERT OR REPLACE INTO mutes VALUES (:user_id, :until, :chat_id)"), {"user_id": user_id, "until": until, "chat_id": chat_id})
        await session.commit()

async def delete_mute(user_id):
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM mutes WHERE user_id = :user_id"), {"user_id": user_id})
        await session.commit()

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

# ===== ВСЕ ХЕНДЛЕРЫ БЕЗ ИЗМЕНЕНИЙ =====
# (они остаются теми же, что были — копируй их из предыдущего кода)

# ===== ЗАПУСК =====
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
