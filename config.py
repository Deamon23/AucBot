import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CLIENT_ID = os.getenv("CLIENT_ID", "")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "")
REGION = os.getenv("REGION", "ru").lower()
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "45"))
MAX_WATCH_PER_USER = int(os.getenv("MAX_WATCH_PER_USER", "20"))

# Кто может пользоваться ботом (Telegram user id через запятую)
_raw_users = os.getenv("ALLOWED_USERS", "")
ALLOWED_USERS: set[int] = set()
for part in _raw_users.split(","):
    part = part.strip()
    if part.isdigit():
        ALLOWED_USERS.add(int(part))

DATA_FILE = "data/watches.json"

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан в .env")
if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError("CLIENT_ID и CLIENT_SECRET должны быть заданы в .env")

# DNS cache TTL (в секундах) - кешируем DNS записи чтобы избежать проблем при временных сбоях
DNS_CACHE_TTL = int(os.getenv("DNS_CACHE_TTL", "300"))