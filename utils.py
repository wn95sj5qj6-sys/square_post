import json
import os
from datetime import datetime, UTC, timedelta

def now():
    return datetime.now(UTC)

def load_json(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_expired(data, hours):
    cutoff = now() - timedelta(hours=hours)
    return [item for item in data if item.get("time") and datetime.fromisoformat(item["time"]) >= cutoff]
