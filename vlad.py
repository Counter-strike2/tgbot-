import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, Update
from aiohttp import web

BOT_TOKEN = "8959860095:AAFsRWRSFQOQ84ww_HwxQ2IaI_24DYxTN2o"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://tgbot-1-qiyl.onrender.com" + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== ТЕСТОВЫЙ ХЕНДЛЕР — ОТВЕЧАЕТ НА ВСЁ =====
@dp.message()
async def test_webhook(message: Message):
    try:
        await message.answer(f"✅ Webhook работает! Ты написал: {message.text}")
        print(f"✅ Получено: {message.text}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")

# ===== ЗАПУСК WEBHOOK =====
async def health_check(request):
    return web.Response(text="OK", status=200)

async def handle_webhook(request):
    try:
        data = await request.json()
        update = Update(**data)
        await dp.process_update(update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        print(f"❌ Ошибка webhook: {e}")
        return web.Response(text="ERROR", status=500)

async def main():
    await bot.delete_webhook()
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook: {WEBHOOK_URL}")
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("🔥 ТЕСТОВЫЙ БОТ ЗАПУЩЕН! ЖДУ СООБЩЕНИЙ...")
    
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
