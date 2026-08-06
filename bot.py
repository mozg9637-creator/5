import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from ctransformers import AutoModelForCausalLM

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# 1. Сразу поднимаем веб-сервер для Render, чтобы порт открылся моментально
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logger.info(f"Health-check сервер запущен на порту {port}")
    server.serve_forever()

# Запускаем сервер в фоновом потоке до загрузки модели
threading.Thread(target=run_health_server, daemon=True).start()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("Не задан TELEGRAM_TOKEN")

# 2. Загружаем модель локально из файла, который лежит рядом с bot.py
MODEL_PATH = "NekoSSV1_0-F32-LoRA.gguf"

logger.info("Загружаю локальную модель...")
model = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH, 
    model_type="llama"
)
logger.info("✓ Модель успешно загружена!")

SYSTEM_PROMPT = "Ты дружелюбный ассистент в Telegram. Отвечай кратко и по делу."
chat_histories = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text("Привет! Я бот с локальной моделью 🚀\n/reset — очистить историю")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_histories[update.effective_chat.id] = []
    await update.message.reply_text("История очищена.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_text = update.message.text

    history = chat_histories.setdefault(chat_id, [])
    history.append(user_text)
    history = history[-10:]

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except:
        pass

    try:
        prompt = f"[INST] {user_text} [/INST]"
        reply_text = model(prompt, max_new_tokens=512, temperature=0.7)
    except Exception as e:
        logger.exception("Ошибка модели")
        reply_text = f"Ошибка: {e}"

    chat_histories[chat_id] = history
    await update.message.reply_text(reply_text)

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()