import os
from dotenv import load_dotenv

load_dotenv()

# 智谱AI
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()

# DeepSeek 新增
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()

# 币安账号配置
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()
BINANCE_API_KEYS = []
if BINANCE_ACCOUNTS:
    BINANCE_API_KEYS = [k.strip() for k in BINANCE_ACCOUNTS.split(",") if k.strip()]

# 默认全局配置
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))

# 过期时间
EXPIRE_HOURS = 72
