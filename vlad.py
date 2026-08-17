import asyncio
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String

# 1. ПОДКЛЮЧЕНИЕ БАЗЫ ДАННЫХ AIVEN
# Берем ссылку, которую мы настроили в панели Render
DATABASE_URL = os.environ.get("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=True)
async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

# Модель таблицы в бд
class UserMemory(Base):
    __tablename__ = 'user_memory'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, nullable=False)
    text = Column(String, nullable=False)

# 2. ИНИЦИАЛИЗАЦИЯ БОТА (Вставьте ваш токен вместо заглушки)
TOKEN = "8959860095:ВСТАВЬ_СЮДА_НОВЫЙ_ТОКЕН_ИЗ_BOTFATHER"
bot = Bot(token=TOKEN)
dp = Dispatcher()

# 3. ЛОГИКА БОТА (ЗАПОМНИ И СКАЖИ)
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Напиши мне: Запомни [текст] — чтобы сохранить его в базу. Или: Скажи — чтобы вернуть.")

@dp.message()
async def handle_message(message: types.Message):
    text = message.text.strip()
    user_id = message.from_user.id

    # Логика: Запомни
    if text.lower().startswith("запомни"):
        data_to_save = text[7:].strip()
        if not data_to_save:
            await message.answer("А что запомнить-то? Напиши: Запомни привет")
            return
            
        async with async_session() as session:
            async with session.begin():
                # Проверяем, есть ли уже запись
                from sqlalchemy import select
                result = await session.execute(select(UserMemory).filter_by(user_id=user_id))
                user_data = result.scalar_one_or_none()
                
                if user_data:
                    user_data.text = data_to_save
                else:
                    session.add(UserMemory(user_id=user_id, text=data_to_save))
        await message.answer(f"Записал в базу данных Aiven: '{data_to_save}'")

    # Логика: Скажи
    elif text.lower() == "скажи":
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
    # Создаем таблицы в Aiven, если их еще нет
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    await start_web_server()  # Запускаем порт
    await dp.start_polling(bot)  # Включаем бота

if __name__ == '__main__':
    asyncio.run(main())
