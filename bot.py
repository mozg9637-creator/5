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

# --- Конфиг (берём из переменных окружения, см. .env) ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL = "llama-3.3-70b-versatile"  # мощная бесплатная модель на Groq

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN (см. .env)")
if not GROQ_API_KEY:
    raise RuntimeError("Не задан GROQ_API_KEY (см. .env)")

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Храним историю диалога отдельно для каждого чата (в памяти, без базы данных)
chat_histories: dict[int, list[dict]] = {}

# Системный промпт — можно изменить под свои задачи
SYSTEM_PROMPT = (
    "Ты — личный секретарь пользователя, отвечающий от его имени в личных сообщениях Telegram. "
    "Будь вежливым, кратким и естественным, как будто отвечает сам человек. "
    "Если тебя спрашивают о чём-то, чего ты не знаешь (личные планы, договорённости, точная информация о пользователе), "
    "честно скажи, что человек скоро ответит сам лично, вместо того чтобы придумывать ответ."
)

MAX_HISTORY_MESSAGES = 20  # сколько последних сообщений держим в памяти на чат


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text(
        "Привет! Я ИИ-бот на базе Groq (Llama 3.3). Просто напиши мне что-нибудь.\n"
        "/reset — очистить историю диалога"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text("История диалога очищена.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat_id = message.chat_id
    user_text = message.text
    # Если сообщение пришло через Telegram Business (пишут вам лично,
    # а бот отвечает от вашего имени), у него будет заполнен business_connection_id
    business_connection_id = getattr(message, "business_connection_id", None)

    # Отдельная история для бизнес-переписки, чтобы не путать с обычным диалогом с ботом
    history_key = f"biz:{chat_id}" if business_connection_id else chat_id
    history = chat_histories.setdefault(history_key, [])
    history.append({"role": "user", "content": user_text})
    history[:] = history[-MAX_HISTORY_MESSAGES:]  # обрезаем историю

    try:
        await context.bot.send_chat_action(
            chat_id=chat_id,
            action="typing",
            business_connection_id=business_connection_id,
        )
    except Exception:
        pass  # не критично, если индикатор "печатает" не отправился

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
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        reply_text = data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.exception("Ошибка при обращении к Groq API")
        reply_text = f"Произошла ошибка при обращении к ИИ: {e}"

    history.append({"role": "assistant", "content": reply_text})

    if business_connection_id:
        # Отправляем ответ от имени пользователя через Telegram Business
        await context.bot.send_message(
            chat_id=chat_id,
            text=reply_text,
            business_connection_id=business_connection_id,
        )
    else:
        await message.reply_text(reply_text)


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Простой обработчик, чтобы Render видел, что сервис 'жив'."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")

    def log_message(self, format, *args):
        pass  # отключаем лишние логи по каждому health-check запросу


def run_health_server():
    port = int(os.getenv("PORT", 10000))  # Render передаёт порт через переменную PORT
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health-check сервер запущен на порту {port}")
    server.serve_forever()


def main() -> None:
    # Render (бесплатный тариф) требует, чтобы сервис слушал порт,
    # поэтому поднимаем health-check сервер в отдельном потоке
    threading.Thread(target=run_health_server, daemon=True).start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
