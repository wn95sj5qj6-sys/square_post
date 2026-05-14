# -*- coding: utf-8 -*-
import random
import datetime
import threading

# 全局内存存储：账号当日计划数据
# 结构：{账号名: {"date":"2026-05-14","daily_target":16,"published":3}}
account_schedule = {}
schedule_lock = threading.Lock()


def get_today_str():
    """获取今日日期字符串 YYYY-MM-DD"""
    return datetime.date.today().strftime("%Y-%m-%d")


def is_in_active_time(start_time: str, end_time: str) -> bool:
    """
    判断当前时间是否在自定义活跃时段内
    传入格式：start_time="08:30"  end_time="22:00"
    """
    now = datetime.datetime.now()
    now_time = now.time()

    # 解析配置的时分
    try:
        start_h, start_m = map(int, start_time.split(":"))
        end_h, end_m = map(int, end_time.split(":"))
    except:
        # 格式错误默认允许发文
        return True

    start_t = datetime.time(start_h, start_m)
    end_t = datetime.time(end_h, end_m)

    # 跨零点场景兼容（如 22:00 - 02:00）
    if start_t <= end_t:
        return start_t <= now_time <= end_t
    else:
        return now_time >= start_t or now_time <= end_t


def init_account_daily_plan(account_name: str, min_daily: int, max_daily: int):
    """
    初始化/重置账号当日发文计划
    每日首次调用自动随机生成当日目标条数
    """
    today = get_today_str()
    with schedule_lock:
        # 已有当天计划且日期一致，直接返回
        if account_name in account_schedule:
            plan = account_schedule[account_name]
            if plan.get("date") == today:
                return plan

        # 生成当日随机发文目标
        daily_target = random.randint(min_daily, max_daily)
        new_plan = {
            "date": today,
            "daily_target": daily_target,
            "published": 0
        }
        account_schedule[account_name] = new_plan
        return new_plan


def get_random_sleep_min(interval_min: int, interval_max: int) -> int:
    """在间隔区间内随机生成休眠分钟数"""
    return random.randint(interval_min, interval_max)


def inc_published_count(account_name: str):
    """账号发文计数 +1"""
    today = get_today_str()
    with schedule_lock:
        if account_name not in account_schedule:
            return
        plan = account_schedule[account_name]
        if plan.get("date") == today:
            plan["published"] += 1


def can_publish_now(account_name: str, cfg: dict) -> (bool, str):
    """
    综合判断当前是否可以发文
    返回：是否可发文、提示原因
    """
    # 1. 校验时段
    start_t = cfg.get("active_start", "08:00")
    end_t = cfg.get("active_end", "22:00")
    if not is_in_active_time(start_t, end_t):
        return False, "不在自定义活跃时段内"

    # 2. 初始化当日计划
    min_d = cfg.get("daily_min", 10)
    max_d = cfg.get("daily_max", 20)
    plan = init_account_daily_plan(account_name, min_d, max_d)

    # 3. 判断是否已发够今日条数
    if plan["published"] >= plan["daily_target"]:
        return False, "已达到今日随机发文上限"

    return True, "可正常发文"


def get_account_schedule_info(account_name: str) -> dict:
    """获取账号当日计划信息：今日目标、已发数量"""
    with schedule_lock:
        return account_schedule.get(account_name, {"daily_target":0, "published":0})
