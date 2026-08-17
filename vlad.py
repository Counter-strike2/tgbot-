import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message, Update
from aiohttp import web

BOT_TOKEN = "8959860095:AAFsRWRSFQOQ84ww_HwxQ2IaI_24DYxTN2o"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = "https://tgbot-1-qiyl.onrender.com" + WEBHOOK_PATH

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message()
async def echo(message: Message):
    await message.answer(f"✅ Webhook alive! Your text: {message.text}")

async def health_check(request):
    return web.Response(text="OK", status=200)

async def handle_webhook(request):
    data = await request.json()
    update = Update(**data)
    await dp.process_update(update)
    return web.Response(text="OK", status=200)

async def main():
    await bot.delete_webhook()
    await bot.set_webhook(WEBHOOK_URL)
    print(f"✅ Webhook set to {WEBHOOK_URL}")
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("🔥 TEST BOT RUNNING")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
