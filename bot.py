import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Конфиг (берём из переменных окружения, см. .env) ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
MODEL = "deepseek-chat"  # есть также "deepseek-reasoner" (модель с рассуждениями)

if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN (см. .env)")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("Не задан DEEPSEEK_API_KEY (см. .env)")

# DeepSeek API совместим по формату с OpenAI, поэтому используем OpenAI SDK
# с указанием другого base_url
client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")

# Храним историю диалога отдельно для каждого чата (в памяти, без базы данных)
chat_histories: dict[int, list[dict]] = {}

# Системный промпт — можно изменить под свои задачи
SYSTEM_PROMPT = "Ты дружелюбный ассистент в Telegram. Отвечай кратко и по делу, на языке пользователя."

MAX_HISTORY_MESSAGES = 20  # сколько последних сообщений держим в памяти на чат


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text(
        "Привет! Я ИИ-бот на базе DeepSeek. Просто напиши мне что-нибудь.\n"
        "/reset — очистить историю диалога"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text("История диалога очищена.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    history = chat_histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    history[:] = history[-MAX_HISTORY_MESSAGES:]  # обрезаем историю

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
        )
        reply_text = response.choices[0].message.content
    except Exception as e:
        logger.exception("Ошибка при обращении к DeepSeek API")
        reply_text = f"Произошла ошибка при обращении к ИИ: {e}"

    history.append({"role": "assistant", "content": reply_text})
    await update.message.reply_text(reply_text)


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
    app.run_polling()


if __name__ == "__main__":
    main()
