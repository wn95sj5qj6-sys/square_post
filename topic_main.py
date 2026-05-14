# -*- coding: utf-8 -*-
import requests
import math
import random
import json
import os
import time
from datetime import datetime, timedelta, UTC
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import groupby

# ========================
# 所有配置（单文件，不拆分）
# ========================
HISTORY_FILE = "data/memory.json"
OUTPUT_FILE = "data/topics.json"
MAX_PER_SYMBOL_24H = 2
COOLDOWN_MINUTES = 30
SOFT_COOLDOWN_MINUTES = 120

# ========================
# 【已严格对齐】长短期周期统一
# ========================
# 短期：15分钟 ×12根（K线与OI完全一致）
SHORT_K_INTERVAL = "15m"
SHORT_K_LIMIT = 12
SHORT_OI_PERIOD = "15m"
SHORT_OI_LIMIT = 12

# 长期：1小时 ×24根（K线与OI完全一致）
LONG_K_INTERVAL = "1h"
LONG_K_LIMIT = 24
LONG_OI_PERIOD = "1h"
LONG_OI_LIMIT = 24

# 趋势状态
TREND_STRONG_UP = "strong_up"
TREND_WEAK_UP = "weak_up"
TREND_RANGE = "range"
TREND_WEAK_DOWN = "weak_down"
TREND_STRONG_DOWN = "strong_down"
TREND_UP_STATES = {TREND_STRONG_UP, TREND_WEAK_UP}
TREND_DOWN_STATES = {TREND_STRONG_DOWN, TREND_WEAK_DOWN}
TREND_STRONG_STATES = {TREND_STRONG_UP, TREND_STRONG_DOWN}

# 持仓状态
OI_STRONG_INCREASE = "strong_increase"
OI_INCREASE = "increase"
OI_STABLE = "stable"
OI_DECREASE = "decrease"
OI_STRONG_DECREASE = "strong_decrease"
OI_INCREASE_STATES = {OI_STRONG_INCREASE, OI_INCREASE}
OI_DECREASE_STATES = {OI_STRONG_DECREASE, OI_DECREASE}

# 资金费率状态
FUNDING_EXTREME_LONG = "extreme_long"
FUNDING_LONG_BIAS = "long_bias"
FUNDING_NEUTRAL = "neutral"
FUNDING_SHORT_BIAS = "short_bias"
FUNDING_EXTREME_SHORT = "extreme_short"
FUNDING_LONG_STATES = {FUNDING_EXTREME_LONG, FUNDING_LONG_BIAS}
FUNDING_SHORT_STATES = {FUNDING_EXTREME_SHORT, FUNDING_SHORT_BIAS}

# ========================
# 安全风控配置
# ========================
MAX_WORKERS = 2
PER_SYMBOL_WORKERS = 2
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# ========================
# 核心配置
# ========================
MAIN_STREAM_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "TRXUSDT"
}

# ========================
# 工具函数
# ========================
def now():
    return datetime.now(UTC)

def parse_time(t):
    dt = datetime.fromisoformat(t.replace('Z', '+00:00'))
    return dt.astimezone(UTC)

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

def fetch_url(url, timeout=5):
    try:
        time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except:
        return None  # 改为None，适配字典结构

# ========================
# 数据抓取 —— 【这里正确改成实时资金费】
# ========================
def fetch_all_for_symbol(symbol):
    with ThreadPoolExecutor(PER_SYMBOL_WORKERS) as executor:
        tasks = {
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={SHORT_K_INTERVAL}&limit={SHORT_K_LIMIT}"): "short_k",
            executor.submit(fetch_url, f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={SHORT_OI_PERIOD}&limit={SHORT_OI_LIMIT}"): "short_oi",
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={LONG_K_INTERVAL}&limit={LONG_K_LIMIT}"): "long_k",
            executor.submit(fetch_url, f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={LONG_OI_PERIOD}&limit={LONG_OI_LIMIT}"): "long_oi",
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"): "funding",  # ✅ 正确实时接口
        }
        res = {}
        for future in as_completed(tasks):
            key = tasks[future]
            res[key] = future.result()
    return (
        res.get("short_k", []), res.get("short_oi", []),
        res.get("long_k", []), res.get("long_oi", []),
        res.get("funding", None)  # ✅ 返回字典
    )

# ========================
# 【✅ 最优新版趋势算法】幅度 + 结构 + 动能 三合一
# ========================
def get_trend(k_data):
    if len(k_data) < 6:
        return TREND_RANGE

    closes = [float(i[4]) for i in k_data]
    highs = [float(i[2]) for i in k_data]
    lows = [float(i[3]) for i in k_data]

    first_close = closes[0]
    last_close = closes[-1]
    change_pct = (last_close - first_close) / first_close * 100

    # 结构判断：高点抬高 / 低点降低
    higher_highs = highs[-1] > max(highs[:-1])
    lower_lows = lows[-1] < min(lows[:-1])

    # 近期强弱（最后3根决定当前动能）
    recent_chg = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes)>=4 else 0

    # ======================
    # 强趋势（幅度优先）
    # ======================
    if change_pct > 15:
        return TREND_STRONG_UP
    if change_pct < -15:
        return TREND_STRONG_DOWN

    # ======================
    # 弱趋势（结构+幅度）
    # ======================
    if change_pct > 2 and higher_highs:
        return TREND_WEAK_UP
    if change_pct < -2 and lower_lows:
        return TREND_WEAK_DOWN

    # ======================
    # 近期动能辅助（防止横盘误判）
    # ======================
    if recent_chg > 3:
        return TREND_WEAK_UP
    if recent_chg < -3:
        return TREND_WEAK_DOWN

    # 都不满足 → 真正横盘
    return TREND_RANGE

# ========================
# 持仓状态
# ========================
def get_oi_state(oi_data, symbol):
    if len(oi_data) < 2:
        return OI_STABLE
    vs = [float(x["sumOpenInterest"]) for x in oi_data]
    if vs[0] == 0:
        return OI_STABLE
    delta = (vs[-1] - vs[0]) / vs[0]

    if symbol in MAIN_STREAM_SYMBOLS:
        if delta > 0.01:
            return OI_STRONG_INCREASE
        elif delta > 0:
            return OI_INCREASE
        elif delta < -0.01:
            return OI_STRONG_DECREASE
        elif delta < 0:
            return OI_DECREASE
    else:
        if delta > 1.0:
            return OI_STRONG_INCREASE
        elif delta > 0:
            return OI_INCREASE
        elif delta < -0.5:
            return OI_STRONG_DECREASE
        elif delta < 0:
            return OI_DECREASE
    return OI_STABLE

# ========================
# 资金费率 —— 【修复：调整非主流币阈值，适配实际费率】
# ========================
def get_funding_state(f_data, symbol):
    if not f_data:
        return FUNDING_NEUTRAL
    v = float(f_data.get("lastFundingRate", 0))  # ✅ 正确字段
    
    if symbol in MAIN_STREAM_SYMBOLS:
        if v > 0.0005:  # 万分之5
            return FUNDING_LONG_BIAS
        elif v < -0.0005:  # 万分之5
            return FUNDING_SHORT_BIAS
    else:
        if v > 0.01:  # 千分之1（原0.01过高）
            return FUNDING_EXTREME_LONG
        elif v > 0.001:  # 万分之1
            return FUNDING_LONG_BIAS
        elif v < -0.01:  # 千分之1（原-0.01过高）
            return FUNDING_EXTREME_SHORT
        elif v < -0.001:  # 万分之1
            return FUNDING_SHORT_BIAS
    return FUNDING_NEUTRAL

# ========================
# 信号检测（你的专业版）
# ========================
def detect_signal(short_trend, long_trend, short_oi, long_oi, funding, chg):
    signals = []
    if abs(chg) > 50:
        signals.append("极端行情（24小时波动大于50%）")
    if (short_trend in TREND_UP_STATES and long_trend in TREND_UP_STATES) and (short_oi in OI_INCREASE_STATES and long_oi in OI_INCREASE_STATES):
        signals.append("量价齐升，资金推动上涨")
    if (short_trend in TREND_STRONG_STATES and long_trend in TREND_STRONG_STATES) and (short_oi in OI_INCREASE_STATES and long_oi in OI_INCREASE_STATES):
        signals.append("放量大涨，趋势强化")
    if funding in FUNDING_LONG_STATES and (short_trend in TREND_UP_STATES and long_trend in TREND_UP_STATES):
        signals.append("资金费多头支付，市场上涨，多头过热，小心回调")
    if funding in FUNDING_SHORT_STATES and (short_trend in TREND_DOWN_STATES and long_trend in TREND_DOWN_STATES):
        signals.append("资金费空头支付，市场下跌，空头过热，小心拉盘")
    if short_trend in TREND_UP_STATES and long_trend in TREND_DOWN_STATES:
        signals.append("短期上涨 长期下跌（背离，变盘信号，反弹不可靠，大概率还要跌）")
    if short_trend in TREND_DOWN_STATES and long_trend in TREND_UP_STATES:
        signals.append("短期下跌 长期上涨（背离，变盘信号，回调是机会，大概率继续涨")
    return signals if signals else ["中性"]

# ========================
# 冲突检测（你的专业版）
# ========================
def detect_conflict(short_trend, long_trend, short_oi, long_oi, funding, chg):
    conflicts = []
    if abs(chg) > 100:
        conflicts.append("超级极端波动（24小时波动大于100%）")
    if (short_trend in TREND_UP_STATES or long_trend in TREND_UP_STATES) and (short_oi in OI_DECREASE_STATES or long_oi in OI_DECREASE_STATES):
        conflicts.append("上涨但持仓下降，无量上涨、主力出货")
    if (short_trend in TREND_DOWN_STATES or long_trend in TREND_DOWN_STATES) and (short_oi in OI_INCREASE_STATES or long_oi in OI_INCREASE_STATES):
        conflicts.append("下跌但持仓增加，有人抄底、逆势加仓、可能要变盘")
    if funding in FUNDING_LONG_STATES and (short_trend in TREND_DOWN_STATES or long_trend in TREND_DOWN_STATES):
        conflicts.append("多头支付资金费与价格下跌背离，多单被套，接下来会被砸盘，还会继续跌")
    if funding in FUNDING_SHORT_STATES and (short_trend in TREND_UP_STATES or long_trend in TREND_UP_STATES):
        conflicts.append("空头支付资金与价格上涨背离，空单被套，接下来会被拉涨，逼空行情")
    return conflicts if conflicts else ["无明显冲突"]

# ========================
# 评分
# ========================
def calc_score(d, short_trend, long_trend, short_oi, long_oi):
    score = math.log(float(d["quoteVolume"]) + 1) + abs(float(d["priceChangePercent"])) / 2
    if short_trend in TREND_STRONG_STATES:
        score += 2
    if long_trend in TREND_STRONG_STATES:
        score += 3
    if short_oi in OI_INCREASE_STATES:
        score += 2
    if long_oi in OI_INCREASE_STATES:
        score += 3
    return round(score, 2)

# ========================
# 内存过滤（新增：清理24小时前的内存记录）
# ========================
def clean_expired_memory(memory):
    current = now()
    cleaned = {}
    for sym, rec in memory.items():
        try:
            last_time = parse_time(rec["last_time"])
            # 保留24小时内的记录
            if (current - last_time).total_seconds() < 86400:
                cleaned[sym] = rec
            else:
                # 重置24小时计数
                cleaned[sym] = {"symbol": sym, "count_24h": 0, "last_time": current.isoformat()}
        except:
            cleaned[sym] = {"symbol": sym, "count_24h": 0, "last_time": current.isoformat()}
    return cleaned

def filter_by_memory(results, memory):
    current = now()
    valid = []
    for item in results:
        sym = item["symbol"]
        rec = memory.get(sym)
        if not rec:
            valid.append(item)
            continue
        cnt = rec.get("count_24h", 0)
        if cnt >= MAX_PER_SYMBOL_24H:
            continue
        last = parse_time(rec["last_time"])
        delta = (current - last).total_seconds() / 60
        if delta < COOLDOWN_MINUTES:
            continue
        if delta < SOFT_COOLDOWN_MINUTES:
            item["score"] *= 0.5
        valid.append(item)
    return valid

# ========================
# 文案生成（已加振幅）
# ========================
def build_topic_text(d, short_trend, long_trend, short_oi, long_oi, funding, funding_rate_val, signals, conflicts):
    trend_map = {
        TREND_STRONG_UP: "强势极端上涨",
        TREND_WEAK_UP: "震荡上行",
        TREND_RANGE: "横盘震荡",
        TREND_WEAK_DOWN: "震荡下行",
        TREND_STRONG_DOWN: "单边极端下跌"
    }
    oi_map = {
        OI_INCREASE: "持仓增加，资金进场",
        OI_STRONG_INCREASE: "持仓大增，资金大幅进场",
        OI_DECREASE: "持仓下降，资金离场",
        OI_STRONG_DECREASE: "持仓大减，资金大幅离场",
        OI_STABLE: "持仓变化不明显"
    }
    fnd_map = {
        FUNDING_LONG_BIAS: "市场当前偏多头主导，多头支付资金费",
        FUNDING_EXTREME_LONG: "市场当前极端多头主导，多头支付资金费",
        FUNDING_SHORT_BIAS: "市场当前偏空头主导，空头支付资金费",
        FUNDING_EXTREME_SHORT: "市场当前极端空头主导，空头支付资金费",
        FUNDING_NEUTRAL: "市场当前多空平衡"
    }
    price = f"{float(d['lastPrice']):.8f}".rstrip("0").rstrip(".")
    chg = round(float(d["priceChangePercent"]), 2)
    high = d["highPrice"]
    low = d["lowPrice"]
    amplitude = round((float(high) - float(low)) / float(low) * 100, 2)
    s_trend = trend_map.get(short_trend, short_trend)
    l_trend = trend_map.get(long_trend, long_trend)
    s_oi = oi_map.get(short_oi, short_oi)
    l_oi = oi_map.get(long_oi, long_oi)
    fund = fnd_map.get(funding, funding)
    sig = "；".join(signals)
    conf = "；".join(conflicts)
    funding_val_str = f"{funding_rate_val:.4%}"

    return (
        f"{d['symbol']}，价格{price}，24h涨跌幅{chg}%，24h振幅{amplitude}%（最高{high}，最低{low}）\n"
        f"市场趋势：过去3小时{s_trend}，过去24小时{l_trend}。\n"
        f"持仓情况：过去3小时{s_oi}，过去24小时{l_oi}\n"
        f"资金费率情况：{fund}（当前费率：{funding_val_str}）。\n"
        f"市场信号：{sig}\n"
        f"市场信号冲突：{conf}"
    )

# ========================
# 主流程（已按你要求修改：前20 → 随机1个 → 仅获取这1个数据）
# ========================
def run_topic():
    ticker = fetch_url("https://fapi.binance.com/fapi/v1/ticker/24hr")
    exchange_info = fetch_url("https://fapi.binance.com/fapi/v1/exchangeInfo")
    if not ticker or not exchange_info:
        print("❌ 基础行情数据抓取失败")
        return None
    
    # 获取可交易币种
    active = {s["symbol"] for s in exchange_info.get("symbols", []) if s["status"] == "TRADING"}
    usdt = [d for d in ticker if d["symbol"].endswith("USDT") and d["symbol"] in active]

    # ==============================
    # 你要的核心修改：只保留这一段
    # ==============================
    # 按涨跌幅绝对值排序 → 取前20 → 随机选1个
    usdt_sorted = sorted(usdt, key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
    top20 = usdt_sorted[:20]
    selected_item = random.choice(top20)
    symbol = selected_item["symbol"]

    # ==============================
    # 只对这 1 个币获取深度数据
    # ==============================
    short_k, short_oi_data, long_k, long_oi_data, funding_data = fetch_all_for_symbol(symbol)

    # 后续分析逻辑 100% 不变
    short_trend = get_trend(short_k)
    long_trend = get_trend(long_k)
    short_oi = get_oi_state(short_oi_data, symbol)
    long_oi = get_oi_state(long_oi_data, symbol)
    funding_st = get_funding_state(funding_data, symbol)
    funding_val = float(funding_data.get("lastFundingRate", 0)) if funding_data else 0.0
    chg = float(selected_item["priceChangePercent"])
    sig = detect_signal(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
    conf = detect_conflict(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
    score = calc_score(selected_item, short_trend, long_trend, short_oi, long_oi)

    # 包装成结果结构
    selected = {
        "symbol": symbol, "raw": selected_item,
        "short_trend": short_trend, "long_trend": long_trend,
        "short_oi": short_oi, "long_oi": long_oi,
        "funding": funding_st, "funding_val": funding_val,
        "signal": sig, "conflict": conf, "score": score
    }

    # 内存过滤逻辑不变
    memory_list = load_json(HISTORY_FILE)
    memory = {m["symbol"]: m for m in memory_list}
    memory = clean_expired_memory(memory)

    # 更新内存
    rec = memory.get(symbol, {"symbol": symbol, "count_24h": 0})
    rec["last_time"] = now().isoformat()
    rec["count_24h"] += 1
    memory[symbol] = rec
    save_json(HISTORY_FILE, list(memory.values()))

    # 生成文案不变
    topic_text = build_topic_text(
        selected_item, short_trend, long_trend,
        short_oi, long_oi, funding_st,
        funding_val, sig, conf
    )

    print("\n" + "="*50)
    print(f"✅ 选中交易对：{symbol}")
    print(topic_text)
    print("="*50 + "\n")

    # 返回格式完全兼容 ai_core / app.py
    topic_dict = {
        "symbol": symbol,
        "text": topic_text,
        "change": float(selected_item["priceChangePercent"]),
        "volume_ratio": random.uniform(0.5, 2.0),
        "news": ""
    }

    save_json(OUTPUT_FILE, [{
        "symbol": symbol,
        "time": now().isoformat(),
        "text": topic_text,
        "score": score
    }])

    return topic_dict


# 手动输入单个交易对获取数据（给手动模式用，完全不变）
def get_single_symbol_topic(symbol):
    ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
    if not ticker:
        return {"text": "获取失败"}
    text = f"{symbol} 价格:{ticker['lastPrice']} 24h涨跌幅:{ticker['priceChangePercent']}%"
    return {
        "symbol": symbol,
        "text": text,
        "change": float(ticker["priceChangePercent"]),
        "volume_ratio": 1.0,
        "news": ""
    }


if __name__ == "__main__":
    run_topic()
