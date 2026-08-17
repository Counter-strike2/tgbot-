import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, BigInteger

# 1. ПОДКЛЮЧЕНИЕ БАЗЫ ДАННЫХ AIVEN
DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

class UserMemory(Base):
    __tablename__ = 'user_memory'
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, nullable=False)
    text = Column(String, nullable=False)

# 2. ИНИЦИАЛИЗАЦИЯ БОТА
TOKEN = "8959860095:AAG2K8ng2mpiukjTRbhxEWmsdFmVa3Sm9Q8"
bot = Bot(token=TOKEN)
dp = Dispatcher()

# 3. ЛОГИКА БОТА
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Напиши мне: Запомни [текст] — чтобы сохранить его в базу. Или: Скажи — чтобы вернуть.")

@dp.message()
async def handle_message(message: types.Message):
    text = message.text.strip()
    user_id = message.from_user.id
    words = text.split()  # Разбиваем текст на отдельные слова

    if not words:
        return

    first_word = words[0].lower()  # Берем только первое слово в нижнем регистре

    # Логика: Запомни
    if first_word == "запомни":
        data_to_save = " ".join(words[1:]).strip()
        
        if not data_to_save:
            await message.answer("А что запомнить-то? Напиши: запомни привет")
            return
            
        async with async_session() as session:
            async with session.begin():
                from sqlalchemy import select
                result = await session.execute(select(UserMemory).filter_by(user_id=user_id))
                user_data = result.scalar_one_or_none()
                
                if user_data:
                    user_data.text = data_to_save
                else:
                    session.add(UserMemory(user_id=user_id, text=data_to_save))
        await message.answer(f"Записал в базу данных Aiven: '{data_to_save}'")

    # Логика: Скажи
    elif first_word == "скажи":
        async with async_session() as session:
            from sqlalchemy import select
            result = await session.execute(select(UserMemory).filter_by(user_id=user_id))
            user_data = result.scalar_one_or_none()
            
            if user_data:
                await message.answer(f"Достаю из базы данных: {user_data.text}")
            else:
                await message.answer("Вы еще ничего не просили меня запомнить!")

# 4. ФИКТИВНЫЙ ВЕБ-СЕРВЕР ДЛЯ ОБМАНА RENDER
async def handle(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
