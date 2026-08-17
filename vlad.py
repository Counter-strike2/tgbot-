import asyncio
import sqlite3
import re
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, BusinessConnection, Update
from aiohttp import web

BOT_TOKEN = "8959860095:AAG2K8ng2mpiukjTRbhxEWmsdFmVa3Sm9Q8"
DB_NAME = "sessions.db"
CHANNEL_LINK = "https://t.me/gotrollholl"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
mutes = {}              
spam_tasks = {}         
typing_tasks = {}       
user_spam_texts = {}    
link_chats = set()      
reply_guard_chats = set() 
typing_disabled_chats = set()
substitutions = {}      
msg_cache = {}          
active_chats = set()    
bot_id = None

def get_db():
    return sqlite3.connect(DB_NAME, timeout=20)

def init_db():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS chat_settings (chat_id INTEGER, setting_type TEXT, PRIMARY KEY (chat_id, setting_type))")
        conn.execute("CREATE TABLE IF NOT EXISTS substitutions (chat_id INTEGER PRIMARY KEY, text TEXT, mode INTEGER)")
        conn.commit()
        
        cur = conn.execute("SELECT chat_id FROM chat_settings WHERE setting_type='enabled_links'")
        for row in cur:
            link_chats.add(row[0])
            
        cur = conn.execute("SELECT chat_id FROM chat_settings WHERE setting_type='reply_guard'")
        for row in cur:
            reply_guard_chats.add(row[0])

        cur = conn.execute("SELECT chat_id FROM chat_settings WHERE setting_type='typing_disabled'")
        for row in cur:
            typing_disabled_chats.add(row[0])
        
        cur = conn.execute("SELECT chat_id, text, mode FROM substitutions")
        for row in cur:
            substitutions[row[0]] = {"text": row[1], "mode": row[2]}

def save_setting(chat_id, setting_type, enabled):
    with get_db() as conn:
        if enabled:
            conn.execute("INSERT OR IGNORE INTO chat_settings VALUES (?, ?)", (chat_id, setting_type))
        else:
            conn.execute("DELETE FROM chat_settings WHERE chat_id = ? AND setting_type = ?", (chat_id, setting_type))
        conn.commit()
        
        if setting_type == 'enabled_links':
            target_set = link_chats
        elif setting_type == 'reply_guard':
            target_set = reply_guard_chats
        else:
            target_set = typing_disabled_chats

        if enabled:
            target_set.add(chat_id)
        else:
            target_set.discard(chat_id)

def save_substitution(chat_id, text, mode):
    with get_db() as conn:
        if text is None:
            conn.execute("DELETE FROM substitutions WHERE chat_id = ?", (chat_id,))
            substitutions.pop(chat_id, None)
        else:
            conn.execute("INSERT OR REPLACE INTO substitutions VALUES (?, ?, ?)", (chat_id, text, mode))
            substitutions[chat_id] = {"text": text, "mode": mode}
        conn.commit()

init_db()

async def delete_msg(chat_id, msg_id, bc_id):
    try:
        await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id])
    except:
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

async def clear_cmd(chat_id, msg_id, bc_id):
    try:
        await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[msg_id])
    except:
        try:
            await bot.delete_message(chat_id, msg_id)
        except:
            pass

async def typing_worker(chat_id, bc_id):
    try:
        while True:
            if chat_id not in typing_disabled_chats:
                await bot.send_chat_action(chat_id=chat_id, action="typing", business_connection_id=bc_id)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

async def spam_worker(chat_id, bc_id, reply_to, text):
    try:
        while True:
            for word in text.split():
                await bot.send_message(chat_id, word, business_connection_id=bc_id, reply_to_message_id=reply_to)
                await asyncio.sleep(0.4)
    except asyncio.CancelledError:
        pass

async def unmute(user_id):
    await asyncio.sleep(mutes[user_id]["time"].total_seconds())
    mutes.pop(user_id, None)

@dp.business_message()
async def handle(message: Message):
    global bot_id, CHANNEL_LINK
    
    try:
        if not message.from_user:
            return
        
        if bot_id is None:
            me = await bot.get_me()
            bot_id = me.id
            
        uid = message.from_user.id
        if uid == bot_id:
            return
            
        chat_id = message.chat.id
        bc_id = message.business_connection_id
        
        if not bc_id:
            return

        if chat_id not in active_chats:
            if len(active_chats) >= 50:
                old_chat = active_chats.pop()
                if old_chat in typing_tasks:
                    typing_tasks[old_chat].cancel()
                    del typing_tasks[old_chat]
            active_chats.add(chat_id)
            
            if chat_id not in typing_tasks:
                typing_tasks[chat_id] = asyncio.create_task(typing_worker(chat_id, bc_id))

        if not message.text:
            return

        text_raw = message.text
        low = text_raw.lower().strip()

        # Кеш для лога удалений
        msg_cache[message.message_id] = {
            "text": text_raw, 
            "user": message.from_user.first_name,
            "user_id": uid,
            "chat_id": chat_id,
            "bc_id": bc_id
        }
        if len(msg_cache) > 3000:
            msg_cache.pop(next(iter(msg_cache)))
            
        # Проверка на реплай-защиту и мут
        if chat_id in reply_guard_chats and message.reply_to_message:
            await delete_msg(chat_id, message.message_id, bc_id)
            return
            
        if uid in mutes and datetime.now() < mutes[uid]["until"]:
            await delete_msg(chat_id, message.message_id, bc_id)
            return

        # ===== ОБРАБОТКА КОМАНД =====
        if low == ".стоп":
            save_setting(chat_id, 'enabled_links', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "🛑 Авто-ссылки в чате выключены.", business_connection_id=bc_id)
            return

        if low == ".старт":
            save_setting(chat_id, 'enabled_links', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "✅ Авто-ссылки в чате включены!", business_connection_id=bc_id)
            return

        if low.startswith("+линк"):
            parts = text_raw.split(maxsplit=1)
            if len(parts) == 1:
                await bot.send_message(chat_id, f"🔗 Текущая ссылка: {CHANNEL_LINK}", business_connection_id=bc_id)
            else:
                new_link = parts[1].strip()
                if not new_link.startswith("http"):
                    new_link = "https://t.me/" + new_link.lstrip("@")
                CHANNEL_LINK = new_link
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, f"🔗 Ссылка изменена на: {CHANNEL_LINK}", business_connection_id=bc_id)
            return

        if low.startswith("подмена "):
            parts = text_raw.split(maxsplit=2)
            if len(parts) >= 2:
                if parts[1].lower() == "выкл":
                    save_substitution(chat_id, None, None)
                    await bot.send_message(chat_id, "❌ Подмена текста выключена.", business_connection_id=bc_id)
                else:
                    mode = int(parts[2]) if len(parts) == 3 and parts[2] in ["1", "2"] else 1
                    sub_text = parts[1]
                    save_substitution(chat_id, sub_text, mode)
                    await bot.send_message(chat_id, f"🔄 Подмена сохранена!\nШаблон: <b>{sub_text}</b> (Режим {mode})", parse_mode="html", business_connection_id=bc_id)
                await clear_cmd(chat_id, message.message_id, bc_id)
            return

        if low == "ss":
            await clear_cmd(chat_id, message.message_id, bc_id)
            reply_to = message.reply_to_message.message_id if message.reply_to_message else None
            text = user_spam_texts.get(chat_id, "Ты фрик!")
            if chat_id in spam_tasks:
                spam_tasks[chat_id].cancel()
            task = asyncio.create_task(spam_worker(chat_id, bc_id, reply_to, text))
            spam_tasks[chat_id] = task
            await bot.send_message(chat_id, "🚀 Спам запущен!", business_connection_id=bc_id)
            return

        if low == "dd":
            await clear_cmd(chat_id, message.message_id, bc_id)
            if chat_id in spam_tasks:
                spam_tasks[chat_id].cancel()
                del spam_tasks[chat_id]
                await bot.send_message(chat_id, "🛑 Спам остановлен!", business_connection_id=bc_id)
            return

        if low.startswith("set "):
            new_spam_text = text_raw[4:].strip()
            user_spam_texts[chat_id] = new_spam_text
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, f"📝 Текст спама изменен на: <i>{new_spam_text}</i>", parse_mode="html", business_connection_id=bc_id)
            return

        if low.startswith(".мут "):
            try:
                minutes = int(re.search(r"\d+", text_raw).group())
                target = message.reply_to_message.from_user.id if message.reply_to_message else chat_id
                mutes[target] = {"time": timedelta(minutes=minutes), "until": datetime.now() + timedelta(minutes=minutes)}
                asyncio.create_task(unmute(target))
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, f"🔇 Пользователь замучен на {minutes} мин.", business_connection_id=bc_id)
            except:
                pass
            return

        if low == ".размут":
            target = message.reply_to_message.from_user.id if message.reply_to_message else chat_id
            if target in mutes:
                del mutes[target]
                await clear_cmd(chat_id, message.message_id, bc_id)
                await bot.send_message(chat_id, "🔊 Мут успешно снят!", business_connection_id=bc_id)
            return

        if low == "печать -":
            save_setting(chat_id, 'typing_disabled', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "⌨️ В этом чате печать выключена и сохранена.", business_connection_id=bc_id)
            return

        if low == "печать +":
            save_setting(chat_id, 'typing_disabled', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            if chat_id not in typing_tasks:
                typing_tasks[chat_id] = asyncio.create_task(typing_worker(chat_id, bc_id))
            await bot.send_message(chat_id, "⌨️ В этом чате печать включена и сохранена!", business_connection_id=bc_id)
            return

        if low == "+реплай":
            save_setting(chat_id, 'reply_guard', True)
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "🛡 Защита от реплаев включена!", business_connection_id=bc_id)
            return

        if low == "-реплай":
            save_setting(chat_id, 'reply_guard', False)
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, "🛡 Защита от реплаев выключена.", business_connection_id=bc_id)
            return

        if low in ["мой ид", "моид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id, f"🆔 Твой ID: <code>{uid}</code>", parse_mode="html", business_connection_id=bc_id)
            return

        if low in ["твой ид", "твоид"]:
            await clear_cmd(chat_id, message.message_id, bc_id)
            if message.reply_to_message:
                await bot.send_message(chat_id, f"🆔 ID пользователя: <code>{message.reply_to_message.from_user.id}</code>", parse_mode="html", business_connection_id=bc_id)
            else:
                await bot.send_message(chat_id, "❌ Ответьте на сообщение пользователя!", business_connection_id=bc_id)
            return

        if low == "!команды":
            await clear_cmd(chat_id, message.message_id, bc_id)
            await bot.send_message(chat_id,
                "📋 **КОМАНДЫ БОТА:**\n\n"
                "`.мут X` — мут на X минут\n"
                "`.размут` — снять мут\n"
                "`.стоп` / `.старт` — включить/выключить ссылки\n"
                "`печать +` / `печать -` — вечная печать\n"
                "`подмена текст 1` — текст в начало\n"
                "`подмена текст 2` — текст в конец\n"
                "`подмена выкл` — выключить подмену\n"
                "`ss` — спам\n"
                "`dd` — стоп спам\n"
                "`set текст` — текст для спама\n"
                "`+реплай` / `-реплай` — защита от реплаев\n"
                "`+линк ссылка` — сменить ссылку\n"
                "`мой ид` / `твой ид` — ID",
                parse_mode="Markdown", business_connection_id=bc_id
            )
            return

        # ===== ЕСЛИ НЕ КОМАНДА - ПРИМЕНЯЕМ ПОДМЕНУ И ССЫЛКИ =====
        final_text = text_raw
        need_modify = False
        
        # 1. Сначала применяем подмену
        if chat_id in substitutions:
            try:
                sub = substitutions[chat_id]
                if sub["mode"] == 1:
                    final_text = f"{sub['text']} {text_raw}"
                else:
                    final_text = f"{text_raw} {sub['text']}"
                need_modify = True
            except:
                pass
        
        # 2. Потом добавляем ссылку (если включено)
        if chat_id in link_chats:
            try:
                # Проверяем, есть ли уже ссылка в сообщении
                has_link = False
                if message.entities:
                    for entity in message.entities:
                        if entity.type in ["url", "text_link"]:
                            has_link = True
                            break
                
                if not has_link and CHANNEL_LINK not in text_raw:
                    final_text = f'<a href="{CHANNEL_LINK}"><u>{final_text}</u></a>'
                    need_modify = True
            except:
                pass
        
        # Если нужно модифицировать - удаляем оригинал и отправляем новое
        if need_modify:
            # Удаляем оригинальное сообщение
            try:
                await bot.delete_business_messages(business_connection_id=bc_id, message_ids=[message.message_id])
            except:
                try:
                    await bot.delete_message(chat_id, message.message_id)
                except:
                    pass
            
            # Отправляем новое сообщение с модифицированным текстом
            try:
                if chat_id in link_chats and '<a href=' in final_text:
                    await bot.send_message(
                        chat_id,
                        final_text,
                        parse_mode="HTML",
                        disable_web_page_preview=True,
                        business_connection_id=bc_id
                    )
                else:
                    await bot.send_message(
                        chat_id,
                        final_text,
                        business_connection_id=bc_id
                    )
            except Exception as e:
                print(f"Ошибка отправки модифицированного сообщения: {e}")
                # Если не получилось отправить новое - ничего не делаем
                
    except Exception as e:
        print(f"❌ Ошибка в обработчике: {e}")

@dp.business_message()
async def cache_incoming(message: Message):
    if not message.from_user or not message.text:
        return
    msg_cache[message.message_id] = {
        "text": message.text,
        "user": message.from_user.first_name,
        "user_id": message.from_user.id,
        "chat_id": message.chat.id,
        "bc_id": message.business_connection_id
    }
    if len(msg_cache) > 3000:
        msg_cache.pop(next(iter(msg_cache)))

@dp.update()
async def global_update_handler(update: Update, bot: Bot):
    try:
        if update.deleted_business_messages:
            data = update.deleted_business_messages
            bc_id = data.business_connection_id
            msg_ids = data.message_ids
            
            for msg_id in msg_ids:
                if msg_id in msg_cache:
                    cached = msg_cache[msg_id]
                    user_link = f"<a href='tg://user?id={cached['user_id']}'>{cached['user']}</a>"
                    
                    await bot.send_message(
                        cached["chat_id"],
                        f"👤 {user_link}\n🗑 Удалил ↓\n{cached['text']}",
                        parse_mode="html",
                        business_connection_id=bc_id or cached["bc_id"]
                    )
                    msg_cache.pop(msg_id, None)
    except Exception as e:
        print(f"❌ Ошибка обработчика удалений: {e}")

# ===== HEALTH-CHECK ДЛЯ RENDER =====
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 10000)
    await site.start()
    print("✅ Health check server started on port 10000")

async def main():
    await start_health_server()
    print("🔥 БОТ УСПЕШНО ЗАПУЩЕН НА POLLING!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
