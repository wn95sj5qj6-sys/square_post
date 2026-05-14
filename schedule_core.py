# -*- coding: utf-8 -*-
import random
import datetime
import threading

# ========== 全局默认配置（账号无配置时用） ==========
DEFAULT_DAILY_MIN = 10
DEFAULT_DAILY_MAX = 20
DEFAULT_INTERVAL_MIN = 8
DEFAULT_INTERVAL_MAX = 25
DEFAULT_ACTIVE_START = "08:00"
DEFAULT_ACTIVE_END = "22:00"
# =====================================================

account_schedule = {}
schedule_lock = threading.Lock()

def get_today_str():
    return datetime.date.today().strftime("%Y-%m-%d")

def is_in_active_time(start_time: str, end_time: str) -> bool:
    now = datetime.datetime.now().time()
    try:
        start_h, start_m = map(int, start_time.split(":"))
        end_h, end_m = map(int, end_time.split(":"))
    except:
        return True

    start_t = datetime.time(start_h, start_m)
    end_t = datetime.time(end_h, end_m)

    if start_t <= end_t:
        return start_t <= now <= end_t
    else:
        return now >= start_t or now <= end_t

def get_account_schedule_config(acc_cfg):
    s = acc_cfg.get("schedule", {})
    return {
        "daily_min": s.get("daily_min", DEFAULT_DAILY_MIN),
        "daily_max": s.get("daily_max", DEFAULT_DAILY_MAX),
        "interval_min": s.get("interval_min", DEFAULT_INTERVAL_MIN),
        "interval_max": s.get("interval_max", DEFAULT_INTERVAL_MAX),
        "active_start": s.get("active_start", DEFAULT_ACTIVE_START),
        "active_end": s.get("active_end", DEFAULT_ACTIVE_END)
    }

def init_daily_plan(account_name, daily_min, daily_max):
    today = get_today_str()
    with schedule_lock:
        if account_name in account_schedule:
            plan = account_schedule[account_name]
            if plan.get("date") == today:
                return plan

        daily_target = random.randint(daily_min, daily_max)
        new_plan = {
            "date": today,
            "daily_target": daily_target,
            "published": 0
        }
        account_schedule[account_name] = new_plan
        return new_plan

def get_random_interval(interval_min, interval_max):
    return random.randint(interval_min, interval_max)

def inc_published(account_name):
    today = get_today_str()
    with schedule_lock:
        if account_name not in account_schedule:
            return
        plan = account_schedule[account_name]
        if plan.get("date") == today:
            plan["published"] += 1

def can_publish(account_name, acc_cfg):
    cfg = get_account_schedule_config(acc_cfg)
    if not is_in_active_time(cfg["active_start"], cfg["active_end"]):
        return False
    plan = init_daily_plan(account_name, cfg["daily_min"], cfg["daily_max"])
    if plan["published"] >= plan["daily_target"]:
        return False
    return True
