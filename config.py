import os
from dotenv import load_dotenv

load_dotenv()

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()

BINANCE_API_KEYS = os.getenv("BINANCE_ACCOUNTS", "").strip().split(",")
BINANCE_API_KEYS = [k.strip() for k in BINANCE_API_KEYS if k.strip()]

EXPIRE_HOURS = 72
