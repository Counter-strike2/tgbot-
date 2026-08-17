import asyncio
import logging
import sys
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Данные бота
BOT_TOKEN = "8959860095:AAFsRWRSFQOQ84ww_HwxQ2IaI_24DYxTN2o"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://tgbot-1-qiyl.onrender.com" + WEBHOOK_PATH

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Я запущен через вебхук и готов отвечать на любые сообщения! 🚀")

# Обработчик команды /ping
@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("Pong! 🏓 Бот отлично работает.")

# Универсальный обработчик для абсолютно любого текста, приветствий и прочего
@dp.message()
async def echo_handler(message: types.Message):
    await message.answer(f"Ты написал: «{message.text}». Я всё вижу и отвечаю! 😎")

# Функция при запуске (сбрасывает старые зависшие апдейты)
async def on_startup(bot: Bot) -> None:
    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
    logging.info(f"Вебхук успешно установлен: {WEBHOOK_URL}")

def main():
    # Настройка логов
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    # Создаем aiohttp приложение
    app = web.Application()

    # Регистрируем обработчик вебхуков aiogram
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_handler.register(app, path=WEBHOOK_PATH)

    # Привязываем жизненный цикл
    setup_application(app, dp, bot=bot)
    
    # Регистрируем событие старта
    dp.startup.register(on_startup)

    # Получаем порт от Render (или 8080 для локалхоста)
    port = int(os.environ.get("PORT", 8080))
    
    # Запуск сервера
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
