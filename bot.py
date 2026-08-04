import os
import logging
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from dotenv import load_dotenv
from telethon import TelegramClient, events

# --- Настройка логирования ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not GROQ_API_KEY:
    raise RuntimeError("Проверьте .env! Отсутствует GROQ_API_KEY")

# Используем официальные публичные ключи Telegram для Android
API_ID = 4
API_HASH = "014b35b6184100b085b0d0572f9b5103"

GROQ_API_URL = "https://groq.com"
MODEL = "llama-3.3-70b-versatile"

chat_histories = {}
MAX_HISTORY_MESSAGES = 15

SYSTEM_PROMPT = (
    "Ты — ИИ-секретарь пользователя, отвечающий вместо него в его ЛИЧНЫХ сообщениях. "
    "Пиши кратко, дружелюбно и естественно на русском языке, как живой человек. "
    "Скажи, что ты сейчас немного занят, но зафиксировал вопрос. "
    "Если у тебя спросят точные личные данные или планы, которых ты не знаешь, "
    "вежливо ответь, что освободишься и ответишь точнее чуть позже."
)

# Инициализация клиента Telethon
client = TelegramClient("my_account_session", API_ID, API_HASH)

# 1. Логика для ВХОДЯЩИХ личных сообщений (ИИ отвечает людям)
@client.on(events.NewMessage(incoming=True, private=True))
async def handle_incoming(event):
    if not event.text:
        return
        
    chat_id = event.chat_id
    user_text = event.text

    # Формируем контекст
    history = chat_histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})
    history[:] = history[-MAX_HISTORY_MESSAGES:]

    try:
        resp = requests.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 512,
                "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
            },
            timeout=20,
        )
        resp.raise_for_status()
        reply_text = resp.json()["choices"]["message"]["content"]
    except Exception as e:
        logger.exception("Ошибка Groq API")
        reply_text = "Сейчас я немного занят, отвечу вам чуть позже!"

    history.append({"role": "assistant", "content": reply_text})
    
    # Отвечаем человеку в личке
    await event.reply(reply_text)

# 2. Логика для ИСХОДЯЩИХ сообщений (чтобы ИИ помнил, что вы ответили сами)
@client.on(events.NewMessage(outgoing=True, private=True))
async def handle_outgoing(event):
    if event.text:
        chat_id = event.chat_id
        history = chat_histories.setdefault(chat_id, [])
        history.append({"role": "assistant", "content": event.text})
        history[:] = history[-MAX_HISTORY_MESSAGES:]

# --- Health Check Server для Render ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"UserBot is running")
    def log_message(self, format, *args): pass

def run_health_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    logger.info("Юзербот на Telethon запускается...")
    client.start()
    client.run_until_disconnected()
