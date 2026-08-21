# -*- coding: utf-8 -*-
import random
import datetime
import threading

# ========== 强制时区：北京时间 UTC+8 ==========
def beijing_now():
    return datetime.datetime.utcnow() + datetime.timedelta(hours=8)

def get_today_str():
    return beijing_now().strftime("%Y-%m-%d")

# ========== 全局默认配置 ==========
DEFAULT_DAILY_MIN = 10
DEFAULT_DAILY_MAX = 20
DEFAULT_INTERVAL_MIN = 8
DEFAULT_INTERVAL_MAX = 25
DEFAULT_ACTIVE_START = "08:00"
DEFAULT_ACTIVE_END = "22:00"

account_schedule = {}
# 使用 RLock 可重入锁，彻底避免嵌套调用时的线程死锁
schedule_lock = threading.RLock()

def is_in_active_time(start_time: str, end_time: str) -> bool:
    now = beijing_now().time()
    try:
        start_h, start_m = map(int, start_time.split(":"))
        end_h, end_m = map(int, end_time.split(":"))
    except:
        return False

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

def init_daily_plan(account_name, daily_min, daily_max, auto_published=0, manual_published=0):
    today = get_today_str()
    with schedule_lock:
        if account_name in account_schedule:
            plan = account_schedule[account_name]
            if plan.get("date") == today:
                return plan

        daily_target = random.randint(daily_min, daily_max)
        new_plan = {
            "date": today,
            "auto_target": daily_target,
            "auto_published": auto_published,
            "manual_published": manual_published
        }
        account_schedule[account_name] = new_plan
        return new_plan

def get_random_interval(interval_min, interval_max):
    return random.randint(interval_min, interval_max)

def inc_auto_published(account_name):
    today = get_today_str()
    with schedule_lock:
        if account_name not in account_schedule:
            return
        plan = account_schedule[account_name]
        if plan.get("date") == today:
            plan["auto_published"] += 1

def inc_manual_published(account_name):
    today = get_today_str()
    with schedule_lock:
        if account_name not in account_schedule:
            return
        plan = account_schedule[account_name]
        if plan.get("date") == today:
            plan["manual_published"] += 1

def can_publish(account_name: str, acc_cfg: dict) -> bool:
    cfg = get_account_schedule_config(acc_cfg)
    if not is_in_active_time(cfg["active_start"], cfg["active_end"]):
        return False
    plan = init_daily_plan(account_name, cfg["daily_min"], cfg["daily_max"])
    if plan["auto_published"] >= plan["auto_target"]:
        return False
    return True

def get_daily_stats(account_name: str, acc_cfg: dict):
    cfg = get_account_schedule_config(acc_cfg)
    plan = init_daily_plan(account_name, cfg["daily_min"], cfg["daily_max"])
    return plan["auto_target"], plan["auto_published"], plan["manual_published"]

def set_daily_stats(account_name: str, acc_cfg: dict, auto_published=None, manual_published=None):
    today = get_today_str()
    cfg = get_account_schedule_config(acc_cfg)
    with schedule_lock:
        if account_name not in account_schedule or account_schedule[account_name].get("date") != today:
            init_daily_plan(account_name, cfg["daily_min"], cfg["daily_max"], 0, 0)
        plan = account_schedule[account_name]
        if auto_published is not None:
            plan["auto_published"] = auto_published
        if manual_published is not None:
            plan["manual_published"] = manual_published
