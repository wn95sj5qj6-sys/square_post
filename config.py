import os
from dotenv import load_dotenv
load_dotenv()
ZHIPU_API_KEY=os.getenv("ZHIPU_API_KEY","").strip()
DEEPSEEK_API_KEY=os.getenv("DEEPSEEK_API_KEY","").strip()
BINANCE_ACCOUNTS=os.getenv("BINANCE_ACCOUNTS","").strip()
BINANCE_API_KEYS=BINANCE_ACCOUNTS.split(",")
DEFAULT_AUTO_INTERVAL=int(os.getenv("AUTO_INTERVAL_MINUTES","60"))
DEFAULT_DAILY_LIMIT=int(os.getenv("DAILY_MAX_LIMIT","8"))
EXPIRE_HOURS=72
