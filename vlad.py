import asyncio
import logging
import sys
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

# Ваши данные
BOT_TOKEN = "8959860095:AAFsRWRSFQOQ84ww_HwxQ2IaI_24DYxTN2o"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://tgbot-1-qiyl.onrender.com" + WEBHOOK_PATH

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Простые команды для проверки работы вебхука
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет! Вебхук на aiogram v3 успешно работает! 🚀")

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("Pong! 🏓 Бот на связи и отвечает через вебхук.")

# Функция, которая выполнится при старте бота
async def on_startup(bot: Bot) -> None:
    await bot.set_webhook(WEBHOOK_URL)
    logging.info(f"Вебхук успешно установлен: {WEBHOOK_URL}")

def main():
    # Настройка логирования в консоль
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    
    # Создаем приложение aiohttp
    app = web.Application()

    # Регистрируем стандартный обработчик вебхуков aiogram
    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_handler.register(app, path=WEBHOOK_PATH)

    # Привязываем жизненный цикл бота к приложению aiohttp
    setup_application(app, dp, bot=bot)
    
    # Регистрируем событие запуска для установки вебхука
    dp.startup.register(on_startup)

    # Получаем порт от Render (либо используем 8080 по умолчанию)
    port = int(os.environ.get("PORT", 8080))
    
    # Запускаем веб-сервер
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
