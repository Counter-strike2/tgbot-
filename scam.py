import asyncio
import asyncpg
import aiohttp
import os
from aiohttp import web
from PIL import Image, ImageDraw
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    LabeledPrice, PreCheckoutQuery, BusinessConnection,
    KeyboardButton, ReplyKeyboardMarkup, KeyboardButtonRequestUsers,
    UsersShared, FSInputFile
)
from aiogram.types import (
    InputRichMessage,
    InputRichBlockButtons,
    InputRichBlockParagraph,
    RichMessageButton,
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8617033510:AAGC53sl9WVYFlF6kS_qK8QnJ-DqPSbiWyQ"
OWNER_USERNAME = "NorikAmiri"
SECRET_CODE = "norik228TOP"
AVATAR_BG = "#17212B"
PORT = int(os.environ.get("PORT", 10000))

DATABASE_URL = os.environ.get("DATABASE_URL")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

BUSINESS_CONNECTION_ID = None
DB_POOL = None


def hex_to_rgb(hex_color: str):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


# ================= БАЗА =================
async def init_db():
    global DB_POOL
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не задан. Добавь в Render → Environment")

    dsn = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    DB_POOL = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=10)

    async with DB_POOL.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS deals (
                id SERIAL PRIMARY KEY,
                owner_id BIGINT,
                nft_name TEXT,
                nft_link TEXT,
                seller TEXT,
                price INTEGER,
                status TEXT DEFAULT 'pending'
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                display_name TEXT
            )
        """)
        await conn.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS owner_id BIGINT")

    print("🐘 PostgreSQL подключён")


async def save_setting(key: str, value: str):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value
        )


async def get_setting(key: str):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key=$1", key)
        return row["value"] if row else None


async def is_admin(user_id: int, username: str) -> bool:
    if username == OWNER_USERNAME:
        return True
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM admins WHERE user_id=$1", user_id)
        return row is not None


async def add_admin(user_id: int, username: str, display_name: str):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO admins (user_id, username, display_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, display_name = EXCLUDED.display_name",
            user_id, username, display_name
        )


async def get_user_display_name(user_id: int, user_obj) -> str:
    name = f"@{user_obj.username}" if user_obj.username else (user_obj.first_name or "Пользователь")
    try:
        async with DB_POOL.acquire() as conn:
            await conn.execute(
                "UPDATE admins SET username=$1, display_name=$2 WHERE user_id=$3",
                user_obj.username, name, user_id
            )
    except Exception:
        pass
    return name


# ================= ЗАГРУЗКА ФОТО =================
async def upload_to_telegraph(file_path: str):
    url = "https://telegra.ph/upload"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            with open(file_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("file", f, filename="img.jpg", content_type="image/jpeg")
                async with session.post(url, data=data) as resp:
                    if resp.status != 200:
                        return None
                    result = await resp.json()
                    if isinstance(result, list) and result and "src" in result[0]:
                        return "https://telegra.ph" + result[0]["src"]
    except Exception as e:
        print(f"[telegra.ph] Ошибка: {e}")
    return None


async def upload_to_catbox(file_path: str):
    url = "https://catbox.moe/user/api.php"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            with open(file_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("reqtype", "fileupload")
                data.add_field("fileToUpload", f, filename="img.jpg", content_type="image/jpeg")
                async with session.post(url, data=data) as resp:
                    text = (await resp.text()).strip()
                    if text.startswith("http"):
                        return text
    except Exception as e:
        print(f"[catbox] Ошибка: {e}")
    return None


async def upload_photo(file_path: str):
    link = await upload_to_telegraph(file_path)
    if link:
        return link
    return await upload_to_catbox(file_path)


# ================= АВАТАРКА БОТА =================
def make_circle_avatar(input_path: str, output_path: str, size: int = 1024, bg_hex: str = "#17212B"):
    try:
        img = Image.open(input_path).convert("RGBA")
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top = (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        img = img.resize((size, size), Image.LANCZOS)

        mask = Image.new("L", (size, size), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, size, size), fill=255)

        bg = Image.new("RGBA", (size, size), hex_to_rgb(bg_hex) + (255,))
        bg.paste(img, (0, 0), mask)

        bg.convert("RGB").save(output_path, "JPEG", quality=95)
        return True
    except Exception as e:
        print(f"[circle] Ошибка: {e}")
        return False


async def get_current_bot_avatar_url():
    try:
        me = await bot.get_me()
        photos = await bot.get_user_profile_photos(user_id=me.id, limit=1)
        if not photos.total_count or not photos.photos:
            return None

        sizes = photos.photos[0]
        file_id = sizes[-1].file_id
        file = await bot.get_file(file_id)

        raw_path = f"bot_avatar_raw_{me.id}.jpg"
        round_path = f"bot_avatar_round_{me.id}.jpg"
        await bot.download_file(file.file_path, destination=raw_path)

        ok = make_circle_avatar(raw_path, round_path, size=1024, bg_hex=AVATAR_BG)
        upload_path = round_path if ok else raw_path
        return await upload_photo(upload_path)
    except Exception as e:
        print(f"[avatar] Ошибка: {e}")
        return None


# ================= FSM =================
class DealForm(StatesGroup):
    nft_name = State()
    nft_link = State()
    seller = State()
    price = State()
    select_chat = State()


# ================= BUSINESS =================
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    global BUSINESS_CONNECTION_ID
    BUSINESS_CONNECTION_ID = connection.id
    await save_setting("business_connection_id", connection.id)
    print(f"✅ Business Connection сохранён: {connection.id}")


@dp.business_message()
async def on_business_message(message: Message):
    global BUSINESS_CONNECTION_ID
    if message.business_connection_id and not BUSINESS_CONNECTION_ID:
        BUSINESS_CONNECTION_ID = message.business_connection_id
        await save_setting("business_connection_id", message.business_connection_id)
        print(f"✅ Business Connection перехвачен: {message.business_connection_id}")


# ================= АКТИВАЦИЯ ПРАВ =================
@dp.message(F.text == SECRET_CODE)
async def activate_admin(message: Message):
    name = f"@{message.from_user.username}" if message.from_user.username else (message.from_user.first_name or "Пользователь")
    await add_admin(message.from_user.id, message.from_user.username, name)
    await message.answer(f"🔑 <b>Права администратора активированы!</b>\nИмя: {name}", parse_mode="HTML")


# ================= ХЕЛПЕР: ПОКАЗАТЬ ЛОТЫ =================
async def show_lots(target_message: Message, owner_id: int):
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, nft_name, price FROM deals WHERE status='pending' AND owner_id=$1 ORDER BY id DESC",
            owner_id
        )

    if not rows:
        await target_message.answer("У вас нет лотов.")
        return

    for r in rows:
        deal_id = r["id"]
        nft_name = r["nft_name"]
        price = r["price"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Выбрать получателя", callback_data=f"pick_{deal_id}", style="primary")],
            [InlineKeyboardButton(text="Удалить", callback_data=f"del_{deal_id}", style="danger")]
        ])
        await target_message.answer(
            f"🕯️ <b>{nft_name}</b> — {price}⭐",
            reply_markup=kb,
            parse_mode="HTML"
        )


# ================= СТАРТ =================
@dp.message(CommandStart(deep_link=False))
async def start(message: Message):
    global BUSINESS_CONNECTION_ID

    if not await is_admin(message.from_user.id, message.from_user.username):
        return  # МОЛЧИМ

    if not BUSINESS_CONNECTION_ID:
        saved = await get_setting("business_connection_id")
        if saved:
            BUSINESS_CONNECTION_ID = saved

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать запрос", callback_data="new_deal", style="success")],
        [InlineKeyboardButton(text="📋 Мои лоты", callback_data="my_lots", style="primary")]
    ])
    status = "🟢 Подключён" if BUSINESS_CONNECTION_ID else "🔴 Не подключён"
    await message.answer(f"👑 Админ-панель\nBusiness: {status}", reply_markup=kb)


# ================= DEEP-LINK =================
@dp.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, command: CommandObject):
    payload = command.args
    if not payload:
        return
    if payload.startswith("deal_"):
        deal_id = int(payload.split("_")[1])
        await open_payment(message.from_user.id, deal_id)


async def open_payment(user_id: int, deal_id: int):
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT nft_name, seller, price FROM deals WHERE id=$1 AND status='pending'",
            deal_id
        )
    if not row:
        return

    photo_url = await get_current_bot_avatar_url()
    kwargs = {}
    if photo_url:
        kwargs["photo_url"] = photo_url

    await bot.send_invoice(
        chat_id=user_id,
        title=row["nft_name"] or "NFT Подарок",
        description=f"Покупка у {row['seller'] or 'продавца'}",
        payload=f"deal_{deal_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=row["nft_name"] or "NFT", amount=row["price"])],
        **kwargs
    )


# ================= СОЗДАНИЕ ЛОТА =================
@dp.callback_query(F.data == "new_deal")
async def new_deal(callback: CallbackQuery, state: FSMContext):
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return
    await callback.message.answer("1️⃣ Введи название NFT:")
    await state.set_state(DealForm.nft_name)
    await callback.answer()


@dp.message(DealForm.nft_name)
async def set_name(message: Message, state: FSMContext):
    await state.update_data(nft_name=message.text)
    await message.answer("2️⃣ Введи ссылку на NFT:")
    await state.set_state(DealForm.nft_link)


@dp.message(DealForm.nft_link)
async def set_link(message: Message, state: FSMContext):
    await state.update_data(nft_link=message.text)
    await message.answer("3️⃣ Введи имя продавца:")
    await state.set_state(DealForm.seller)


@dp.message(DealForm.seller)
async def set_seller(message: Message, state: FSMContext):
    await state.update_data(seller=message.text)
    await message.answer("4️⃣ Введи цену в звёздах:")
    await state.set_state(DealForm.price)


@dp.message(DealForm.price)
async def set_price(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ Только число.")
        return

    await state.update_data(price=int(message.text))
    data = await state.get_data()

    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO deals (owner_id, nft_name, nft_link, seller, price) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            message.from_user.id, data["nft_name"], data["nft_link"],
            data["seller"], data["price"]
        )
        deal_id = row["id"]

    await message.answer(f"✅ Лот #{deal_id} создан! Выбери получателя:", parse_mode="HTML")
    await show_lots(message, message.from_user.id)
    await state.clear()


# ================= МОИ ЛОТЫ =================
@dp.callback_query(F.data == "my_lots")
async def my_lots(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return
    await show_lots(callback.message, callback.from_user.id)
    await callback.answer()


# ================= УДАЛЕНИЕ ЛОТА =================
@dp.callback_query(F.data.startswith("del_"))
async def delete_lot(callback: CallbackQuery):
    deal_id = int(callback.data.split("_")[1])
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT owner_id FROM deals WHERE id=$1", deal_id)
        if not row:
            await callback.answer("Лот не найден", show_alert=True)
            return
        if row["owner_id"] != callback.from_user.id and callback.from_user.username != OWNER_USERNAME:
            await callback.answer("❌ Это не ваш лот", show_alert=True)
            return
        await conn.execute("DELETE FROM deals WHERE id=$1", deal_id)
    await callback.answer("Лот удалён", show_alert=True)
    try:
        await callback.message.delete()
    except:
        pass


# ================= ВЫБОР ЛОТА =================
@dp.callback_query(F.data.startswith("pick_"))
async def pick_lot(callback: CallbackQuery, state: FSMContext):
    deal_id = int(callback.data.split("_")[1])

    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT owner_id FROM deals WHERE id=$1", deal_id)

    if not row:
        await callback.answer("Лот не найден", show_alert=True)
        return
    if row["owner_id"] != callback.from_user.id and callback.from_user.username != OWNER_USERNAME:
        await callback.answer("❌ Это не ваш лот", show_alert=True)
        return

    await state.update_data(deal_id=deal_id)

    kb = ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="👤 Выбрать получателя",
                request_users=KeyboardButtonRequestUsers(
                    request_id=deal_id,
                    user_is_bot=False,
                    max_quantity=1,
                    request_name=True,
                    request_username=True
                )
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await callback.message.answer("👤 Нажми кнопку ниже:", reply_markup=kb)
    await state.set_state(DealForm.select_chat)
    await callback.answer()


# ================= ВЫБОР ПОЛУЧАТЕЛЯ → ОТПРАВКА =================
@dp.message(DealForm.select_chat, F.users_shared)
async def on_user_selected(message: Message, state: FSMContext):
    global BUSINESS_CONNECTION_ID

    users_shared: UsersShared = message.users_shared
    deal_id = users_shared.request_id
    user_id = users_shared.users[0].user_id

    if not BUSINESS_CONNECTION_ID:
        saved = await get_setting("business_connection_id")
        if saved:
            BUSINESS_CONNECTION_ID = saved

    sender_name = message.from_user.first_name or "Пользователь"

    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT owner_id, nft_name, nft_link, seller, price FROM deals WHERE id=$1",
            deal_id
        )

    if not row:
        await message.answer("❌ Лот не найден.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        await state.clear()
        return

    owner_id = row["owner_id"]
    nft_name = row["nft_name"]
    nft_link = row["nft_link"]
    seller = row["seller"]
    price = row["price"]

    if owner_id != message.from_user.id and message.from_user.username != OWNER_USERNAME:
        await message.answer("❌ Это не ваш лот.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        await state.clear()
        return

    photo_url = await get_current_bot_avatar_url()

    invoice_kwargs = {}
    if photo_url:
        invoice_kwargs["photo_url"] = photo_url

    try:
        invoice_link = await bot.create_invoice_link(
            title=nft_name or "NFT Подарок",
            description=f"Покупка у {seller or 'продавца'}",
            payload=f"deal_{deal_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=nft_name or "NFT", amount=price)],
            **invoice_kwargs
        )
        print(f"[invoice] создан: {invoice_link}")
    except Exception as e:
        print(f"[invoice] Ошибка: {e}")
        invoice_link = None

    if not invoice_link:
        await message.answer("❌ Не удалось создать инвойс.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        await state.clear()
        return

    rich_message = InputRichMessage(
        blocks=[
            InputRichBlockParagraph(text=f"{sender_name} предлагает {nft_link} За {price} звезд."),
            InputRichBlockParagraph(text="\n\nПредложение действует 24 часа"),
            InputRichBlockButtons(buttons=[RichMessageButton(text="ПРИНЯТЬ", url=invoice_link, style="success")]),
            InputRichBlockButtons(buttons=[RichMessageButton(text="ИГНОРИРОВАТЬ", url="https://t.me/NorikAmiri", style="danger")])
        ]
    )

    sent_via = None
    last_error = None

    if BUSINESS_CONNECTION_ID:
        try:
            await bot.send_rich_message(
                chat_id=user_id,
                rich_message=rich_message,
                business_connection_id=BUSINESS_CONNECTION_ID
            )
            sent_via = "business"
            print(f"[send] OK через business → {user_id}")
        except Exception as e:
            last_error = str(e)
            print(f"[send business] Ошибка: {e}")

    if not sent_via:
        try:
            await bot.send_rich_message(chat_id=user_id, rich_message=rich_message)
            sent_via = "direct"
            print(f"[send] OK напрямую → {user_id}")
        except Exception as e:
            last_error = str(e)
            print(f"[send direct] Ошибка: {e}")

    if not sent_via:
        try:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⭐ ОПЛАТИТЬ", url=invoice_link)]
            ])
            await bot.send_message(
                user_id,
                f"👤 <b>{sender_name}</b> предлагает вам <b>{nft_name}</b>\n"
                f"💰 Цена: <b>{price}⭐</b>",
                parse_mode="HTML", reply_markup=kb
            )
            sent_via = "fallback"
            print(f"[send] OK fallback → {user_id}")
        except Exception as e:
            last_error = str(e)
            print(f"[send fallback] Ошибка: {e}")

    if sent_via:
        await message.answer("✅ Отправлено!", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
    else:
        err = last_error or "unknown"
        await message.answer(f"❌ Не удалось отправить.\n<code>{err}</code>", parse_mode="HTML",
                            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))

    await state.clear()


# ================= ОПЛАТА =================
@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    print(f"[pre_checkout] От {query.from_user.id}, payload={query.invoice_payload}")
    await bot.answer_pre_checkout_query(query.id, ok=True)


@dp.message(F.successful_payment)
async def payment_success(message: Message):
    print(f"[payment] Ловлю successful_payment от {message.from_user.id}")
    payload = message.successful_payment.invoice_payload
    deal_id = int(payload.split("_")[1])

    async with DB_POOL.acquire() as conn:
        await conn.execute("UPDATE deals SET status='sold' WHERE id=$1", deal_id)
        row = await conn.fetchrow(
            "SELECT owner_id, nft_name, seller, price FROM deals WHERE id=$1", deal_id
        )

    nft_name = row["nft_name"] if row else "NFT"
    seller = row["seller"] if row else "продавец"
    price = row["price"] if row else message.successful_payment.total_amount
    deal_owner_id = row["owner_id"] if row else None

    buyer = message.from_user
    buyer_id = buyer.id
    buyer_username = f"@{buyer.username}" if buyer.username else "—"
    buyer_first_name = buyer.first_name or "Покупатель"
    buyer_link = f'<a href="tg://user?id={buyer_id}">{buyer_first_name}</a>'

    try:
        await message.answer(
            'тебя заскамили как лоха <tg-emoji emoji-id="5391011124231556271">😂</tg-emoji>',
            parse_mode="HTML"
        )
        print(f"[payment] Сообщение отправлено покупателю {buyer_id}")
    except Exception as e:
        print(f"[payment] Ошибка: {e}")

    text = (
        f"💰 <b>НОВАЯ ОПЛАТА!</b>\n\n"
        f"👤 Покупатель: {buyer_link}\n"
        f"🔗 Юзернейм: {buyer_username}\n"
        f"📱 ID: <code>{buyer_id}</code>\n\n"
        f"🕯️ Лот: <b>{nft_name}</b>\n"
        f"👑 Продавец: {seller}\n"
        f"⭐ Сумма: <b>{price} звёзд</b>"
    )

    recipients = set()
    if deal_owner_id:
        recipients.add(deal_owner_id)
    try:
        owner_chat = await bot.get_chat(f"@{OWNER_USERNAME}")
        recipients.add(owner_chat.id)
    except Exception as e:
        print(f"Не удалось получить ID владельца: {e}")

    for admin_id in recipients:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            print(f"Не удалось отправить уведомление {admin_id}: {e}")


# ================= ВЕБ-СЕРВЕР =================
async def health(request):
    return web.Response(text="OK")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()ЛОХ


# ================= ЗАГЛУШКА (молчит для всех) =================
@dp.message()
async def catch_all(message: Message):
    """Ловит всё, что не попало в другие хендлеры. Молчим."""
    return


# ================= ЗАПУСК =================
async def main():
    global BUSINESS_CONNECTION_ID
    await init_db()

    saved = await get_setting("business_connection_id")
    if saved:
        BUSINESS_CONNECTION_ID = saved

    await start_web_server()
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
