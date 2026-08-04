import os
import logging
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Конфигурация из .env ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")  # Ваш Telegram ID цифрами
MODEL = "llama-3.3-70b-versatile"

if not TELEGRAM_TOKEN or not GROQ_API_KEY or not ADMIN_ID:
    raise RuntimeError("Проверьте .env! Отсутствует TELEGRAM_TOKEN, GROQ_API_KEY или ADMIN_ID")

ADMIN_ID = int(ADMIN_ID)
GROQ_API_URL = "https://groq.com"

# Хранилище в оперативной памяти
chat_histories: dict[str, list[dict]] = {}
paused_chats: set[int] = set()

SYSTEM_PROMPT = (
    "Ты — личный секретарь пользователя, отвечающий от его имени в личных сообщениях Telegram. "
    "Будь вежливым, кратким и естественным, как будто отвечает сам человек. "
    "Если тебя спрашивают о чём-то, чего ты не знаешь (личные планы, договорённости, точная информация о пользователе), "
    "честно скажи, что человек скоро ответит сам лично, вместо того чтобы придумывать ответ."
)

MAX_HISTORY_MESSAGES = 20

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        await update.message.reply_text(
            "👑 Добро пожаловать, Босс!\n\n"
            "Команды управления ИИ-секретарем:\n"
            "• `/history ID` — Посмотреть историю чата\n"
            "• `/pause_ai ID` — Отключить ИИ для пользователя\n"
            "• `/resume_ai ID` — Снова включить ИИ\n"
            "• `/reset_chat ID` — Очистить контекст ИИ"
        )
    else:
        chat_histories[str(update.effective_chat.id)] = []
        await update.message.reply_text("Привет! Я ИИ-секретарь. Оставьте ваше сообщение, я передам его владельцу.")

# --- Панель управления администратора ---

async def admin_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID: return
    try:
        # ИСПРАВЛЕНО: Берём первый элемент списка аргументов context.args[0]
        target_chat = int(context.args[0])  
        paused_chats.add(target_chat)
        await update.message.reply_text(f"⏸ ИИ отключен для чата `{target_chat}`. Теперь вы можете общаться там лично.", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: `/pause_ai ЧАТ_ID`")

async def admin_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID: return
    try:
        # ИСПРАВЛЕНО: Добавлен индекс [0]
        target_chat = int(context.args[0])
        paused_chats.discard(target_chat)
        await update.message.reply_text(f"▶️ ИИ снова активен в чате `{target_chat}`.", parse_mode="Markdown")
    except (IndexError, ValueError):
        await update.message.reply_text("Использование: `/resume_ai ЧАТ_ID`")

async def admin_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID: return
    try:
        # ИСПРАВЛЕНО: Добавлен индекс [0]
        target_chat = context.args[0]
        history_key = f"biz:{target_chat}"
        history = chat_histories.get(history_key, [])
        if not history:
            await update.message.reply_text("История пуста или чат не найден.")
            return
        
        text = f"📜 **История чата {target_chat}:**\n\n"
        for msg in history[-10:]:
            role = "🤖 ИИ" if msg["role"] == "assistant" else "👤 Юзер"
            text += f"**{role}**: {msg['content']}\n"
        await update.message.reply_text(text, parse_mode="Markdown")
    except IndexError:
        await update.message.reply_text("Использование: `/history ЧАТ_ID`")

async def admin_reset_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID: return
    try:
        # ИСПРАВЛЕНО: Добавлен индекс [0]
        target_chat = context.args[0]
        chat_histories[f"biz:{target_chat}"] = []
        await update.message.reply_text(f"🧹 История ИИ для чата `{target_chat}` очищена.", parse_mode="Markdown")
    except IndexError:
        await update.message.reply_text("Использование: `/reset_chat ЧАТ_ID`")

# --- Главный обработчик логики секретарства ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat_id = message.chat_id
    user_text = message.text
    business_connection_id = getattr(message, "business_connection_id", None)

    # 1. Если это ваше личное сообщение в бизнес-чате — заносим его в память ИИ как ответ
    if business_connection_id and message.from_user.id == ADMIN_ID:
        history_key = f"biz:{chat_id}"
        history = chat_histories.setdefault(history_key, [])
        history.append({"role": "assistant", "content": user_text})
        history[:] = history[-MAX_HISTORY_MESSAGES:]
        return

    # 2. Проверяем, не выключен ли ИИ для этого человека
    if chat_id in paused_chats:
        return

    # 3. Формируем ключ истории диалога
    history_key = f"biz:{chat_id}" if business_connection_id else str(chat_id)
    history = chat_histories.setdefault(history_key, [])
    history.append({"role": "user", "content": user_text})
    history[:] = history[-MAX_HISTORY_MESSAGES:]

    # 4. Уведомление в личку бота админу о входящем запросе
    if business_connection_id:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔔 **Новый диалог в Telegram Business!**\n"
                     f"Чат ID: `{chat_id}`\n"
                     f"Имя: {message.from_user.full_name}\n"
                     f"Текст: *{user_text}*",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
            business_connection_id=business_connection_id,
        )
    except Exception:
        pass

    # 5. Запрос к Groq API (Llama 3.3)
    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        reply_text = data["choices"]["message"]["content"]
    except Exception as e:
        logger.exception("Ошибка при обращении к Groq API")
        reply_text = "Извините, сейчас я затрудняюсь ответить. Мой владелец скоро свяжется с вами лично."

    history.append({"role": "assistant", "content": reply_text})

    # 6. Отправка ответа
    if business_connection_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            business_connection_id=business_connection_id,
        )
    else:
        await message.reply_text(reply_text)

# --- Настройка Health-check (Render) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def log_message(self, format, *args): pass

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health-check сервер запущен на порту {port}")
    server.serve_forever()

# --- Точка входа ---
def main() -> None:
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Регистрация обработчиков команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pause_ai", admin_pause))
    app.add_handler(CommandHandler("resume_ai", admin_resume))
    app.add_handler(CommandHandler("history", admin_history))
    app.add_handler(CommandHandler("reset_chat", admin_reset_chat))
    
    # Регистрация обработчика текста
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот-секретарь успешно инициализирован.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
