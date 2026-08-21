import asyncio
import os
import psycopg2
import re
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, Update
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Изменили имя переменной, чтобы Render не перехватывал старый BOT_TOKEN
BOT_TOKEN = "8959860095:AAGoL-Ng0r--K4l2K_I0RJusKfQLI8dzwSw".strip()
DATABASE_URL = os.environ.get('DATABASE_URL')
CHANNEL_LINK = "https://t.me/gotrollholl"

bot = Bot(token=MY_TOKEN)
dp = Dispatcher()
