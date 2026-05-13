# -*- coding: utf-8 -*-
import requests
import math
import random
import json
import os
import time
from datetime import datetime, timedelta, UTC
from concurrent.futures import ThreadPoolExecutor, as_completed

HISTORY_FILE = "data/memory.json"
OUTPUT_FILE = "data/topics.json"
MAX_PER_SYMBOL_24H = 2
COOLDOWN_MINUTES = 30
SOFT_COOLDOWN_MINUTES = 120

SHORT_K_INTERVAL = "15m"
SHORT_K_LIMIT = 12
SHORT_OI_PERIOD = "15m"
SHORT_OI_LIMIT = 12

LONG_K_INTERVAL = "1h"
LONG_K_LIMIT = 24
LONG_OI_PERIOD = "1h"
LONG_OI_LIMIT = 24

TREND_STRONG_UP = "强势上涨"
TREND_WEAK_UP = "震荡上行"
TREND_RANGE = "横盘震荡"
TREND_WEAK_DOWN = "震荡下行"
TREND_STRONG_DOWN = "强势下跌"
TREND_UP_STATES = {TREND_STRONG_UP, TREND_WEAK_UP}
TREND_DOWN_STATES = {TREND_STRONG_DOWN, TREND_WEAK_DOWN}

OI_STRONG_INCREASE = "持仓大增"
OI_INCREASE = "持仓增加"
OI_STABLE = "持仓平稳"
OI_DECREASE = "持仓减少"
OI_STRONG_DECREASE = "持仓大减"

FUNDING_EXTREME_LONG = "极端多头"
FUNDING_LONG_BIAS = "偏多头"
FUNDING_NEUTRAL = "多空平衡"
FUNDING_SHORT_BIAS = "偏空头"
FUNDING_EXTREME_SHORT = "极端空头"

MAX_WORKERS = 2
PER_SYMBOL_WORKERS = 2
REQUEST_DELAY_MIN = 0.3
REQUEST_DELAY_MAX = 0.5

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

MAIN_STREAM_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "TRXUSDT"
}

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
        return None

def fetch_all_for_symbol(symbol):
    with ThreadPoolExecutor(PER_SYMBOL_WORKERS) as executor:
        tasks = {
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={SHORT_K_INTERVAL}&limit={SHORT_K_LIMIT}"): "short_k",
            executor.submit(fetch_url, f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={SHORT_OI_PERIOD}&limit={SHORT_OI_LIMIT}"): "short_oi",
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={LONG_K_INTERVAL}&limit={LONG_K_LIMIT}"): "long_k",
            executor.submit(fetch_url, f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={LONG_OI_PERIOD}&limit={LONG_OI_LIMIT}"): "long_oi",
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"): "funding",
        }
        res = {}
        for future in as_completed(tasks):
            res[tasks[future]] = future.result()
    return (
        res.get("short_k", []), res.get("short_oi", []),
        res.get("long_k", []), res.get("long_oi", []),
        res.get("funding", None)
    )

def get_trend(k_data):
    if len(k_data) < 6:
        return TREND_RANGE
    closes = [float(i[4]) for i in k_data]
    highs = [float(i[2]) for i in k_data]
    lows = [float(i[3]) for i in k_data]
    first_close = closes[0]
    last_close = closes[-1]
    change_pct = (last_close - first_close) / first_close * 100
    higher_highs = highs[-1] > max(highs[:-1])
    lower_lows = lows[-1] < min(lows[:-1])
    recent_chg = (closes[-1] - closes[-4]) / closes[-4] * 100 if len(closes)>=4 else 0

    if change_pct > 15:
        return TREND_STRONG_UP
    if change_pct < -15:
        return TREND_STRONG_DOWN
    if change_pct > 2 and higher_highs:
        return TREND_WEAK_UP
    if change_pct < -2 and lower_lows:
        return TREND_WEAK_DOWN
    if recent_chg > 3:
        return TREND_WEAK_UP
    if recent_chg < -3:
        return TREND_WEAK_DOWN
    return TREND_RANGE

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

def get_funding_state(f_data, symbol):
    if not f_data:
        return FUNDING_NEUTRAL
    v = float(f_data.get("lastFundingRate", 0))

    if symbol in MAIN_STREAM_SYMBOLS:
        if v > 0.0005:
            return FUNDING_LONG_BIAS
        elif v < -0.0005:
            return FUNDING_SHORT_BIAS
    else:
        if v > 0.01:
            return FUNDING_EXTREME_LONG
        elif v > 0.001:
            return FUNDING_LONG_BIAS
        elif v < -0.01:
            return FUNDING_EXTREME_SHORT
        elif v < -0.001:
            return FUNDING_SHORT_BIAS
    return FUNDING_NEUTRAL

def detect_signal(short_trend, long_trend, short_oi, long_oi, funding, chg):
    signals = []
    if abs(chg) > 50:
        signals.append("极端行情")
    if (short_trend in TREND_UP_STATES and long_trend in TREND_UP_STATES) and (short_oi in {OI_INCREASE, OI_STRONG_INCREASE} and long_oi in {OI_INCREASE, OI_STRONG_INCREASE}):
        signals.append("量价齐升，资金进场")
    if (short_trend in TREND_STRONG_UP or short_trend in TREND_STRONG_DOWN) and (long_trend in TREND_STRONG_UP or long_trend in TREND_STRONG_DOWN):
        signals.append("趋势强化")
    if funding in {FUNDING_LONG_BIAS, FUNDING_EXTREME_LONG} and short_trend in TREND_UP_STATES:
        signals.append("多头偏强，注意回调风险")
    if funding in {FUNDING_SHORT_BIAS, FUNDING_EXTREME_SHORT} and short_trend in TREND_DOWN_STATES:
        signals.append("空头偏强，注意反弹风险")
    if short_trend in TREND_UP_STATES and long_trend in TREND_DOWN_STATES:
        signals.append("短期上涨、长期下跌，背离信号，反弹不持久")
    if short_trend in TREND_DOWN_STATES and long_trend in TREND_UP_STATES:
        signals.append("短期下跌、长期上涨，背离信号，回调是机会")
    return signals if signals else ["中性"]

def detect_conflict(short_trend, long_trend, short_oi, long_oi, funding, chg):
    conflicts = []
    if abs(chg) > 100:
        conflicts.append("超级极端波动")
    if (short_trend in TREND_UP_STATES or long_trend in TREND_UP_STATES) and (short_oi in {OI_DECREASE, OI_STRONG_DECREASE} or long_oi in {OI_DECREASE, OI_STRONG_DECREASE}):
        conflicts.append("上涨无资金支撑，主力出货风险")
    if (short_trend in TREND_DOWN_STATES or long_trend in TREND_DOWN_STATES) and (short_oi in {OI_INCREASE, OI_STRONG_INCREASE} or long_oi in {OI_INCREASE, OI_STRONG_INCREASE}):
        conflicts.append("下跌资金入场，逆势抄底信号")
    if funding in {FUNDING_LONG_BIAS, FUNDING_EXTREME_LONG} and short_trend in TREND_DOWN_STATES:
        conflicts.append("多头支付、价格下跌，多单被套，大概率继续跌")
    if funding in {FUNDING_SHORT_BIAS, FUNDING_EXTREME_SHORT} and short_trend in TREND_UP_STATES:
        conflicts.append("空头支付、价格上涨，空单被套，大概率逼空")
    return conflicts if conflicts else ["无明显冲突"]

def calc_score(d, short_trend, long_trend, short_oi, long_oi):
    score = math.log(float(d["quoteVolume"]) + 1) + abs(float(d["priceChangePercent"])) / 2
    if short_trend in TREND_STRONG_UP or short_trend in TREND_STRONG_DOWN:
        score += 2
    if long_trend in TREND_STRONG_UP or long_trend in TREND_STRONG_DOWN:
        score += 3
    if short_oi in {OI_INCREASE, OI_STRONG_INCREASE}:
        score += 2
    if long_oi in {OI_INCREASE, OI_STRONG_INCREASE}:
        score += 3
    return round(score, 2)

def clean_expired_memory(memory):
    current = now()
    cleaned = {}
    for sym, rec in memory.items():
        try:
            last_time = parse_time(rec["last_time"])
            if (current - last_time).total_seconds() < 86400:
                cleaned[sym] = rec
            else:
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

def build_topic_text(d, short_trend, long_trend, short_oi, long_oi, funding, funding_rate_val, signals, conflicts):
    price = f"{float(d['lastPrice']):.8f}".rstrip("0").rstrip(".")
    chg = round(float(d["priceChangePercent"]), 2)
    high = d["highPrice"]
    low = d["lowPrice"]
    amplitude = round((float(high) - float(low)) / float(low) * 100, 2)
    s_trend = short_trend
    l_trend = long_trend
    s_oi = short_oi
    l_oi = long_oi
    fund = funding
    sig = "；".join(signals)
    conf = "；".join(conflicts)
    funding_val_str = f"{funding_rate_val:.4%}"

    return (
        f"{d['symbol']}，价格{price}，24h涨跌幅{chg}%，24h振幅{amplitude}%\n"
        f"市场趋势：过去3小时{s_trend}，过去24小时{l_trend}。\n"
        f"持仓情况：过去3小时{s_oi}，过去24小时{l_oi}\n"
        f"资金费率情况：{fund}（当前费率：{funding_val_str}）。\n"
        f"市场信号：{sig}\n"
        f"市场信号冲突：{conf}"
    )

def run_topic():
    ticker = fetch_url("https://fapi.binance.com/fapi/v1/ticker/24hr")
    exchange_info = fetch_url("https://fapi.binance.com/fapi/v1/exchangeInfo")
    if not ticker or not exchange_info:
        print("❌ 基础行情数据抓取失败")
        return None

    active = {s["symbol"] for s in exchange_info.get("symbols", []) if s["status"] == "TRADING"}
    usdt = [d for d in ticker if d["symbol"].endswith("USDT") and d["symbol"] in active]
    usdt_sorted = sorted(usdt, key=lambda x: abs(float(x["priceChangePercent"])), reverse=True)
    top20 = usdt_sorted[:20]
    selected_item = random.choice(top20)
    symbol = selected_item["symbol"]

    short_k, short_oi_data, long_k, long_oi_data, funding_data = fetch_all_for_symbol(symbol)
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

    memory_list = load_json(HISTORY_FILE)
    memory = {m["symbol"]: m for m in memory_list}
    memory = clean_expired_memory(memory)

    rec = memory.get(symbol, {"symbol": symbol, "count_24h": 0})
    rec["last_time"] = now().isoformat()
    rec["count_24h"] += 1
    memory[symbol] = rec
    save_json(HISTORY_FILE, list(memory.values()))

    topic_text = build_topic_text(
        selected_item, short_trend, long_trend,
        short_oi, long_oi, funding_st,
        funding_val, sig, conf
    )

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

def get_random_topic():
    return run_topic()

def get_single_symbol_topic(symbol):
    ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
    if not ticker:
        return {"symbol": symbol, "text": "行情数据获取失败", "change": 0, "volume_ratio": 1.0, "news": ""}
    short_k, short_oi_data, long_k, long_oi_data, funding_data = fetch_all_for_symbol(symbol)
    short_trend = get_trend(short_k)
    long_trend = get_trend(long_k)
    short_oi = get_oi_state(short_oi_data, symbol)
    long_oi = get_oi_state(long_oi_data, symbol)
    funding_st = get_funding_state(funding_data, symbol)
    funding_val = float(funding_data.get("lastFundingRate", 0)) if funding_data else 0.0
    chg = float(ticker["priceChangePercent"])
    sig = detect_signal(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
    conf = detect_conflict(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
    topic_text = build_topic_text(
        ticker, short_trend, long_trend,
        short_oi, long_oi, funding_st,
        funding_val, sig, conf
    )
    return {
        "symbol": symbol,
        "text": topic_text,
        "change": chg,
        "volume_ratio": 1.0,
        "news": ""
    }

if __name__ == "__main__":
    print(run_topic())
