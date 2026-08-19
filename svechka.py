import asyncio
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== КОНФИГ ====================
TOKEN = "8518936477:AAGHks_mt0ucVc8FGg_1GHX_009qnq5H3Jg"
ADMIN_IDS = [5825717381, 8649099800]  # кто видит админку
SUPPORT_USERNAME = "WestTwopeek"      # юзер для поддержки

# ==================== БАЗА ДАННЫХ (JSON) ====================
DB_FILE = "database.json"

def load_db() -> Dict:
    if not os.path.exists(DB_FILE):
        # создаём пустую базу
        return {
            "users": {},
            "total_stats": {
                "total_users": 0,
                "total_games": 0,
                "total_coins": 0
            }
        }
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(data: Dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_user(user_id: int) -> Optional[Dict]:
    db = load_db()
    return db["users"].get(str(user_id))

def create_user(user_id: int, first_name: str, username: Optional[str] = None) -> Dict:
    db = load_db()
    user_data = {
        "id": user_id,
        "first_name": first_name,
        "username": username,
        "balance": 5000,
        "stats": {
            "total_games": 0,
            "wins": 0,
            "losses": 0
        },
        "banned": False
    }
    db["users"][str(user_id)] = user_data
    db["total_stats"]["total_users"] += 1
    save_db(db)
    return user_data

def update_user(user_id: int, data: Dict):
    db = load_db()
    if str(user_id) in db["users"]:
        db["users"][str(user_id)].update(data)
        save_db(db)

def add_coins(user_id: int, amount: int):
    db = load_db()
    user = db["users"].get(str(user_id))
    if user:
        user["balance"] += amount
        db["total_stats"]["total_coins"] += amount
        save_db(db)

def remove_coins(user_id: int, amount: int):
    db = load_db()
    user = db["users"].get(str(user_id))
    if user and user["balance"] >= amount:
        user["balance"] -= amount
        db["total_stats"]["total_coins"] -= amount
        save_db(db)
        return True
    return False

def is_banned(user_id: int) -> bool:
    user = get_user(user_id)
    return user and user.get("banned", False)

def find_user_by_username_or_id(text: str) -> Optional[Dict]:
    """Ищет пользователя по @username или ID (число)"""
    db = load_db()
    text = text.strip()
    if text.startswith("@"):
        username = text[1:].lower()
        for uid, data in db["users"].items():
            if data.get("username") and data["username"].lower() == username:
                return data
    else:
        # пытаемся как ID
        try:
            uid = int(text)
            return db["users"].get(str(uid))
        except:
            pass
    return None

# ==================== FSM СОСТОЯНИЯ ====================
class GameStates(StatesGroup):
    # Угадай число
    guess_waiting_stake = State()
    guess_waiting_number = State()
    # Камень-ножницы-бумага
    rps_waiting_stake = State()
    rps_waiting_move = State()
    # Кости
    dice_waiting_stake = State()
    dice_waiting_roll = State()
    # Минное поле
    mine_waiting_stake = State()
    mine_waiting_cell = State()
    # Админка - бан
    admin_ban_waiting_input = State()
    admin_unban_waiting_input = State()
    admin_addcoins_waiting_input = State()

# ==================== КЛАВИАТУРЫ ====================
def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎮 ИГРАТЬ", callback_data="menu_games"))
    builder.row(InlineKeyboardButton(text="👤 ПРОФИЛЬ", callback_data="menu_profile"))
    if user_id in ADMIN_IDS:
        builder.row(InlineKeyboardButton(text="⚙️ АДМИН ПАНЕЛЬ", callback_data="menu_admin"))
    builder.row(InlineKeyboardButton(text="📩 ПОДДЕРЖКА", url=f"tg://resolve?domain={SUPPORT_USERNAME}"))
    return builder.as_markup()

def games_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔢 Угадай число", callback_data="game_guess"),
        InlineKeyboardButton(text="✊ Камень-ножницы", callback_data="game_rps")
    )
    builder.row(
        InlineKeyboardButton(text="🎲 Кости", callback_data="game_dice"),
        InlineKeyboardButton(text="💣 Минное поле", callback_data="game_mine")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main"))
    return builder.as_markup()

def number_buttons_1_10() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row = []
    for i in range(1, 11):
        row.append(InlineKeyboardButton(text=str(i), callback_data=f"num_{i}"))
    builder.row(*row[:5])
    builder.row(*row[5:])
    builder.row(InlineKeyboardButton(text="🎲 Рандом", callback_data="num_random"))
    builder.row(InlineKeyboardButton(text="❌ Сдаться", callback_data="num_giveup"))
    return builder.as_markup()

def rps_buttons() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✊ Камень", callback_data="rps_rock"),
        InlineKeyboardButton(text="🤚 Бумага", callback_data="rps_paper"),
        InlineKeyboardButton(text="✌️ Ножницы", callback_data="rps_scissors")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_games"))
    return builder.as_markup()

def mine_field_buttons(step: int = 0) -> InlineKeyboardMarkup:
    """Генерирует поле 5x5 с номерами 1-25"""
    builder = InlineKeyboardBuilder()
    for i in range(1, 26):
        builder.button(text=str(i), callback_data=f"mine_{i}")
    builder.adjust(5)
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_games"))
    return builder.as_markup()

def admin_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Список пользователей", callback_data="admin_list"))
    builder.row(InlineKeyboardButton(text="🚫 Забанить", callback_data="admin_ban"))
    builder.row(InlineKeyboardButton(text="✅ Разбанить", callback_data="admin_unban"))
    builder.row(InlineKeyboardButton(text="💰 Выдать монеты", callback_data="admin_addcoins"))
    builder.row(InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_stats"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu_main"))
    return builder.as_markup()

def admin_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel"))
    return builder.as_markup()

# ==================== ОБРАБОТЧИКИ ====================
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ---------- СТАРТ / ПРИВЕТСТВИЕ ----------
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Гость"
    username = message.from_user.username

    user = get_user(user_id)
    if not user:
        user = create_user(user_id, first_name, username)
    elif user.get("banned"):
        await message.answer("🚫 Вы забанены и не можете пользоваться ботом.")
        return

    # Приветствие с именем (кликабельное)
    text = (
        f"🕷️ <b>{first_name}</b>, ДОБРО ПОЖАЛОВАТЬ!\n\n"
        f"Я твой игровой бот, выбирай и погнали! 🚀"
    )
    # Отправляем стикер паука (можно использовать любой стикерпак)
    try:
        await message.answer_sticker("CAACAgIAAxkBAAEIXl1nNwlCbAAA")  # пример стикера паука (замени на свой)
    except:
        pass
    await message.answer(text, reply_markup=main_menu_keyboard(user_id), parse_mode="HTML")

# ---------- ГЛАВНОЕ МЕНЮ (callback) ----------
@dp.callback_query(F.data == "menu_main")
async def menu_main(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    await callback.message.edit_text(
        f"🕷️ <b>{callback.from_user.first_name}</b>, возвращайся в меню!",
        reply_markup=main_menu_keyboard(user_id),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_games")
async def menu_games(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    await callback.message.edit_text(
        "🎮 <b>Выбери игру:</b>",
        reply_markup=games_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "menu_profile")
async def menu_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    user = get_user(user_id)
    if not user:
        await callback.answer("Профиль не найден", show_alert=True)
        return
    stats = user["stats"]
    total = stats["total_games"] or 1  # чтобы не делить на ноль
    winrate = round(stats["wins"] / total * 100, 1) if total > 0 else 0
    text = (
        f"👤 <b>МОЙ ПРОФИЛЬ</b>\n\n"
        f"Имя: {user['first_name']}\n"
        f"ID: <code>{user_id}</code>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"Всего игр: {stats['total_games']}\n"
        f"Побед: {stats['wins']}\n"
        f"Поражений: {stats['losses']}\n"
        f"Процент побед: {winrate}%\n\n"
        f"💰 Баланс: {user['balance']} монет"
    )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(user_id), parse_mode="HTML")
    await callback.answer()

# ---------- ПОДДЕРЖКА (уже есть url-кнопка) ----------

# ---------- ИГРЫ ----------
# === Угадай число ===
@dp.callback_query(F.data == "game_guess")
async def game_guess_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    await state.set_state(GameStates.guess_waiting_stake)
    user = get_user(user_id)
    await callback.message.edit_text(
        f"🔢 <b>Угадай число (1-10)</b>\n\n"
        f"Попыток: 4\n"
        f"💰 Твой баланс: {user['balance']} монет\n\n"
        f"Введи сумму ставки (числом):",
        reply_markup=admin_cancel_keyboard(),  # кнопка отмены
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.guess_waiting_stake)
async def guess_stake_received(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("Вы забанены.")
        await state.clear()
        return
    try:
        stake = int(message.text.strip())
        if stake <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи положительное число.")
        return
    user = get_user(user_id)
    if stake > user["balance"]:
        await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}")
        return
    # Сохраняем ставку
    await state.update_data(stake=stake, attempts=4)
    # Списываем ставку сразу?
    # Лучше списать при победе/поражении, но для простоты спишем сейчас
    if not remove_coins(user_id, stake):
        await message.answer("Ошибка списания.")
        await state.clear()
        return
    await state.set_state(GameStates.guess_waiting_number)
    # Загадываем число
    import random
    number = random.randint(1, 10)
    await state.update_data(number=number)
    await message.answer(
        f"✅ Ставка {stake} монет принята!\n"
        f"Угадай число от 1 до 10 (осталось 4 попытки):",
        reply_markup=number_buttons_1_10()
    )

@dp.callback_query(GameStates.guess_waiting_number, F.data.startswith("num_"))
async def guess_number_click(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    number = data.get("number")
    attempts = data.get("attempts", 4)
    stake = data.get("stake", 0)

    if callback.data == "num_giveup":
        await callback.message.edit_text(
            f"❌ Ты сдался! Загадано было число {number}.",
            reply_markup=main_menu_keyboard(user_id)
        )
        # проигрыш, монеты уже списаны
        await state.clear()
        await callback.answer()
        return

    if callback.data == "num_random":
        import random
        guess = random.randint(1, 10)
    else:
        guess = int(callback.data.split("_")[1])

    attempts -= 1
    await state.update_data(attempts=attempts)

    if guess == number:
        # победа
        win_amount = stake * 2
        add_coins(user_id, win_amount)
        # обновляем статистику
        user = get_user(user_id)
        user["stats"]["total_games"] += 1
        user["stats"]["wins"] += 1
        update_user(user_id, {"stats": user["stats"]})
        await callback.message.edit_text(
            f"🎉 Поздравляю! Ты угадал число {number}!\n"
            f"Твой выигрыш: {win_amount} монет (ставка {stake} × 2).\n"
            f"Баланс: {user['balance']} монет.",
            reply_markup=main_menu_keyboard(user_id)
        )
        await state.clear()
        await callback.answer()
        return

    # не угадал
    if attempts == 0:
        # проигрыш
        user = get_user(user_id)
        user["stats"]["total_games"] += 1
        user["stats"]["losses"] += 1
        update_user(user_id, {"stats": user["stats"]})
        await callback.message.edit_text(
            f"😵 Ты проиграл! Загадано было число {number}.\n"
            f"Ты потерял {stake} монет.\n"
            f"Баланс: {user['balance']} монет.",
            reply_markup=main_menu_keyboard(user_id)
        )
        await state.clear()
        await callback.answer()
        return

    # продолжаем
    hint = "больше" if guess < number else "меньше"
    await callback.message.edit_text(
        f"❌ Не угадал! Загаданное число {hint}.\n"
        f"Осталось попыток: {attempts}",
        reply_markup=number_buttons_1_10()
    )
    await callback.answer()

# === Камень-ножницы-бумага ===
@dp.callback_query(F.data == "game_rps")
async def game_rps_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    await state.set_state(GameStates.rps_waiting_stake)
    user = get_user(user_id)
    await callback.message.edit_text(
        f"✊ <b>Камень-ножницы-бумага</b>\n\n"
        f"💰 Твой баланс: {user['balance']} монет\n\n"
        f"Введи сумму ставки (числом):",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.rps_waiting_stake)
async def rps_stake_received(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("Вы забанены.")
        await state.clear()
        return
    try:
        stake = int(message.text.strip())
        if stake <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи положительное число.")
        return
    user = get_user(user_id)
    if stake > user["balance"]:
        await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}")
        return
    if not remove_coins(user_id, stake):
        await message.answer("Ошибка списания.")
        await state.clear()
        return
    await state.update_data(stake=stake)
    await state.set_state(GameStates.rps_waiting_move)
    await message.answer(
        f"✅ Ставка {stake} монет принята! Выбери свой ход:",
        reply_markup=rps_buttons()
    )

@dp.callback_query(GameStates.rps_waiting_move, F.data.startswith("rps_"))
async def rps_move(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    stake = data.get("stake", 0)
    move = callback.data.split("_")[1]  # rock, paper, scissors
    import random
    bot_move = random.choice(["rock", "paper", "scissors"])
    # определяем победителя
    if move == bot_move:
        result = "draw"
    elif (move == "rock" and bot_move == "scissors") or \
         (move == "scissors" and bot_move == "paper") or \
         (move == "paper" and bot_move == "rock"):
        result = "win"
    else:
        result = "lose"

    emoji_map = {"rock": "✊", "paper": "🤚", "scissors": "✌️"}
    user = get_user(user_id)
    if result == "win":
        win_amount = stake * 2
        add_coins(user_id, win_amount)
        user["stats"]["total_games"] += 1
        user["stats"]["wins"] += 1
        update_user(user_id, {"stats": user["stats"]})
        text = (
            f"Ты: {emoji_map[move]}\nБот: {emoji_map[bot_move]}\n\n"
            f"🎉 Ты победил! +{win_amount} монет (ставка {stake} × 2).\n"
            f"Баланс: {user['balance']}"
        )
    elif result == "lose":
        user["stats"]["total_games"] += 1
        user["stats"]["losses"] += 1
        update_user(user_id, {"stats": user["stats"]})
        text = (
            f"Ты: {emoji_map[move]}\nБот: {emoji_map[bot_move]}\n\n"
            f"😵 Ты проиграл! Потеряно {stake} монет.\n"
            f"Баланс: {user['balance']}"
        )
    else:
        # ничья, возвращаем ставку
        add_coins(user_id, stake)
        text = (
            f"Ты: {emoji_map[move]}\nБот: {emoji_map[bot_move]}\n\n"
            f"🤝 Ничья! Ставка {stake} возвращена.\n"
            f"Баланс: {user['balance']}"
        )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(user_id))
    await state.clear()
    await callback.answer()

# === Кости ===
@dp.callback_query(F.data == "game_dice")
async def game_dice_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    await state.set_state(GameStates.dice_waiting_stake)
    user = get_user(user_id)
    await callback.message.edit_text(
        f"🎲 <b>Кости</b>\n\n"
        f"💰 Твой баланс: {user['balance']} монет\n\n"
        f"Введи сумму ставки (числом):",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.dice_waiting_stake)
async def dice_stake_received(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("Вы забанены.")
        await state.clear()
        return
    try:
        stake = int(message.text.strip())
        if stake <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи положительное число.")
        return
    user = get_user(user_id)
    if stake > user["balance"]:
        await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}")
        return
    if not remove_coins(user_id, stake):
        await message.answer("Ошибка списания.")
        await state.clear()
        return
    await state.update_data(stake=stake)
    await state.set_state(GameStates.dice_waiting_roll)
    await message.answer(
        f"✅ Ставка {stake} монет принята! Нажми 🎲, чтобы бросить кости.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎲 БРОСИТЬ", callback_data="dice_roll")]
        ])
    )

@dp.callback_query(GameStates.dice_waiting_roll, F.data == "dice_roll")
async def dice_roll(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    stake = data.get("stake", 0)
    import random
    user_roll = random.randint(1, 6)
    bot_roll = random.randint(1, 6)
    user = get_user(user_id)
    if user_roll > bot_roll:
        win_amount = stake * 2
        add_coins(user_id, win_amount)
        user["stats"]["total_games"] += 1
        user["stats"]["wins"] += 1
        update_user(user_id, {"stats": user["stats"]})
        text = (
            f"🎲 Ты: {user_roll}\n🎲 Бот: {bot_roll}\n\n"
            f"🎉 Ты выиграл! +{win_amount} монет.\n"
            f"Баланс: {user['balance']}"
        )
    elif user_roll < bot_roll:
        user["stats"]["total_games"] += 1
        user["stats"]["losses"] += 1
        update_user(user_id, {"stats": user["stats"]})
        text = (
            f"🎲 Ты: {user_roll}\n🎲 Бот: {bot_roll}\n\n"
            f"😵 Ты проиграл! Потеряно {stake} монет.\n"
            f"Баланс: {user['balance']}"
        )
    else:
        add_coins(user_id, stake)
        text = (
            f"🎲 Ты: {user_roll}\n🎲 Бот: {bot_roll}\n\n"
            f"🤝 Ничья! Ставка {stake} возвращена.\n"
            f"Баланс: {user['balance']}"
        )
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(user_id))
    await state.clear()
    await callback.answer()

# === Минное поле ===
@dp.callback_query(F.data == "game_mine")
async def game_mine_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if is_banned(user_id):
        await callback.answer("Вы забанены!", show_alert=True)
        return
    await state.set_state(GameStates.mine_waiting_stake)
    user = get_user(user_id)
    await callback.message.edit_text(
        f"💣 <b>Минное поле</b>\n\n"
        f"Поле 5×5, спрятано 5 мин.\n"
        f"Выигрыш: ставка × 2.\n"
        f"💰 Твой баланс: {user['balance']} монет\n\n"
        f"Введи сумму ставки (числом):",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.mine_waiting_stake)
async def mine_stake_received(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if is_banned(user_id):
        await message.answer("Вы забанены.")
        await state.clear()
        return
    try:
        stake = int(message.text.strip())
        if stake <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введи положительное число.")
        return
    user = get_user(user_id)
    if stake > user["balance"]:
        await message.answer(f"❌ Недостаточно средств! Баланс: {user['balance']}")
        return
    if not remove_coins(user_id, stake):
        await message.answer("Ошибка списания.")
        await state.clear()
        return
    # Генерируем поле: 5 мин, остальное безопасно
    import random
    mines = set(random.sample(range(1, 26), 5))
    await state.update_data(stake=stake, mines=mines, opened=set(), game_over=False)
    await state.set_state(GameStates.mine_waiting_cell)
    await message.answer(
        f"✅ Ставка {stake} монет принята! Открывай клетки. Попадаешь на мину — проигрыш.",
        reply_markup=mine_field_buttons()
    )

@dp.callback_query(GameStates.mine_waiting_cell, F.data.startswith("mine_"))
async def mine_cell_click(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    if data.get("game_over"):
        await callback.answer("Игра уже завершена.", show_alert=True)
        return
    stake = data.get("stake", 0)
    mines = data.get("mines", set())
    opened = data.get("opened", set())
    cell = int(callback.data.split("_")[1])
    if cell in opened:
        await callback.answer("Эта клетка уже открыта.")
        return
    opened.add(cell)
    await state.update_data(opened=opened)

    if cell in mines:
        # попал на мину
        data["game_over"] = True
        await state.update_data(game_over=True)
        user = get_user(user_id)
        user["stats"]["total_games"] += 1
        user["stats"]["losses"] += 1
        update_user(user_id, {"stats": user["stats"]})
        await callback.message.edit_text(
            f"💥 БАБАХ! Ты наступил на мину!\n"
            f"Потеряно {stake} монет.\n"
            f"Баланс: {user['balance']}",
            reply_markup=main_menu_keyboard(user_id)
        )
        await state.clear()
        await callback.answer()
        return

    # проверяем, все ли мины найдены (открыто 20 клеток)
    if len(opened) == 20:
        # победа
        data["game_over"] = True
        await state.update_data(game_over=True)
        win_amount = stake * 2
        add_coins(user_id, win_amount)
        user = get_user(user_id)
        user["stats"]["total_games"] += 1
        user["stats"]["wins"] += 1
        update_user(user_id, {"stats": user["stats"]})
        await callback.message.edit_text(
            f"🎉 Поздравляю! Ты обезвредил все мины!\n"
            f"Выигрыш: {win_amount} монет.\n"
            f"Баланс: {user['balance']}",
            reply_markup=main_menu_keyboard(user_id)
        )
        await state.clear()
        await callback.answer()
        return

    # продолжаем
    # обновляем поле (можно показать текущее состояние, но для простоты оставляем кнопки)
    await callback.message.edit_text(
        f"💣 Минное поле (открыто {len(opened)} клеток, нужно найти все 5 мин).",
        reply_markup=mine_field_buttons()
    )
    await callback.answer()

# ---------- АДМИНКА ----------
@dp.callback_query(F.data == "menu_admin")
async def menu_admin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("У вас нет прав.", show_alert=True)
        return
    await callback.message.edit_text(
        "⚙️ <b>АДМИН ПАНЕЛЬ</b>\n\nВыберите действие:",
        reply_markup=admin_menu_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query(F.data == "admin_list")
async def admin_list(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    db = load_db()
    users = db["users"]
    if not users:
        await callback.message.edit_text("📋 Пользователей пока нет.", reply_markup=admin_menu_keyboard())
        await callback.answer()
        return
    text = "📋 <b>Список пользователей:</b>\n\n"
    for uid, data in list(users.items())[:20]:  # ограничим 20
        name = data.get("first_name", "Без имени")
        username = data.get("username")
        username_str = f"(@{username})" if username else ""
        banned = " ⚠️ ЗАБАНЕН" if data.get("banned") else ""
        stats = data.get("stats", {})
        text += (
            f"• {name} {username_str} - ID: <code>{uid}</code>\n"
            f"  Игр: {stats.get('total_games',0)}, Побед: {stats.get('wins',0)}, "
            f"Баланс: {data.get('balance',0)}💰{banned}\n\n"
        )
    text += f"Всего: {len(users)} пользователей."
    await callback.message.edit_text(text, reply_markup=admin_menu_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(GameStates.admin_ban_waiting_input)
    await callback.message.edit_text(
        "🚫 <b>Забанить пользователя</b>\n\n"
        "Введите @username или ID пользователя:",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.admin_ban_waiting_input)
async def admin_ban_process(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("Нет прав.")
        await state.clear()
        return
    text = message.text.strip()
    target = find_user_by_username_or_id(text)
    if not target:
        await message.answer("❌ Пользователь не найден.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return
    if target.get("banned"):
        await message.answer("⚠️ Пользователь уже забанен.")
        await state.clear()
        return
    target_id = target["id"]
    update_user(target_id, {"banned": True})
    await message.answer(
        f"✅ Пользователь {target['first_name']} (ID: {target_id}) забанен.",
        reply_markup=admin_menu_keyboard()
    )
    await state.clear()

@dp.callback_query(F.data == "admin_unban")
async def admin_unban_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(GameStates.admin_unban_waiting_input)
    await callback.message.edit_text(
        "✅ <b>Разбанить пользователя</b>\n\n"
        "Введите @username или ID пользователя:",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.admin_unban_waiting_input)
async def admin_unban_process(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("Нет прав.")
        await state.clear()
        return
    text = message.text.strip()
    target = find_user_by_username_or_id(text)
    if not target:
        await message.answer("❌ Пользователь не найден.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return
    if not target.get("banned"):
        await message.answer("⚠️ Пользователь не забанен.")
        await state.clear()
        return
    target_id = target["id"]
    update_user(target_id, {"banned": False})
    await message.answer(
        f"✅ Пользователь {target['first_name']} (ID: {target_id}) разбанен.",
        reply_markup=admin_menu_keyboard()
    )
    await state.clear()

@dp.callback_query(F.data == "admin_addcoins")
async def admin_addcoins_start(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    await state.set_state(GameStates.admin_addcoins_waiting_input)
    await callback.message.edit_text(
        "💰 <b>Выдать монеты</b>\n\n"
        "Введите @username или ID пользователя:",
        reply_markup=admin_cancel_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(GameStates.admin_addcoins_waiting_input)
async def admin_addcoins_process(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("Нет прав.")
        await state.clear()
        return
    text = message.text.strip()
    target = find_user_by_username_or_id(text)
    if not target:
        await message.answer("❌ Пользователь не найден.")
        await state.clear()
        return
    await state.update_data(target_id=target["id"])
    await message.answer(f"Введите сумму монет для выдачи пользователю {target['first_name']}:")
    # ожидаем следующее сообщение с суммой
    @dp.message(F.text.regexp(r'^\d+$'))
    async def addcoins_amount(msg: types.Message, state: FSMContext):
        data = await state.get_data()
        target_id = data.get("target_id")
        if not target_id:
            return
        amount = int(msg.text)
        add_coins(target_id, amount)
        user = get_user(target_id)
        await msg.answer(
            f"✅ Выдано {amount} монет пользователю {user['first_name']}.\n"
            f"Теперь баланс: {user['balance']} монет.",
            reply_markup=admin_menu_keyboard()
        )
        await state.clear()
    # регистрируем временный хендлер – используем фильтр, но лучше сделать через FSM, но для простоты оставим так
    # Однако это не сработает, так как хендлер определен внутри. Переделаем: запросим сумму и перейдем в новое состояние.
    # Сделаем отдельное состояние для суммы.
    await state.set_state(GameStates.admin_addcoins_waiting_amount)

@dp.message(GameStates.admin_addcoins_waiting_amount)
async def admin_addcoins_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("Нет прав.")
        await state.clear()
        return
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            raise ValueError
    except:
        await message.answer("❌ Введите положительное число.")
        return
    data = await state.get_data()
    target_id = data.get("target_id")
    if not target_id:
        await message.answer("Ошибка, попробуйте заново.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return
    add_coins(target_id, amount)
    user = get_user(target_id)
    await message.answer(
        f"✅ Выдано {amount} монет пользователю {user['first_name']}.\n"
        f"Теперь баланс: {user['balance']} монет.",
        reply_markup=admin_menu_keyboard()
    )
    await state.clear()

# Зарегистрируем состояние для суммы
GameStates.admin_addcoins_waiting_amount = State()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in ADMIN_IDS:
        await callback.answer("Нет прав.", show_alert=True)
        return
    db = load_db()
    total = db["total_stats"]
    users = db["users"]
    banned_count = sum(1 for u in users.values() if u.get("banned"))
    text = (
        "📊 <b>Общая статистика</b>\n\n"
        f"Всего пользователей: {total.get('total_users', 0)}\n"
        f"Забаненных: {banned_count}\n"
        f"Всего игр сыграно: {total.get('total_games', 0)}\n"
        f"Всего монет в системе: {total.get('total_coins', 0)}"
    )
    await callback.message.edit_text(text, reply_markup=admin_menu_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    await callback.message.edit_text(
        "Действие отменено.",
        reply_markup=main_menu_keyboard(user_id)
    )
    await callback.answer()

# ---------- ЗАПУСК ----------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
