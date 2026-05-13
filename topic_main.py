# -*- coding: utf-8 -*-
import requests
import math
import random
import json
import os
import time
from datetime import datetime, timedelta, UTC
from concurrent.futures import ThreadPoolExecutor, as_completed

# 配置
HISTORY_FILE = "data/memory.json"
OUTPUT_FILE = "data/topics.json"
MAX_PER_SYMBOL_24H = 2
COOLDOWN_MINUTES = 30
SOFT_COOLDOWN_MINUTES = 120

# K线、持仓周期
SHORT_K_INTERVAL = "15m"
SHORT_K_LIMIT = 12
SHORT_OI_PERIOD = "15m"
SHORT_OI_LIMIT = 12

LONG_K_INTERVAL = "1h"
LONG_K_LIMIT = 24
LONG_OI_PERIOD = "1h"
LONG_OI_LIMIT = 24

# 趋势
TREND_STRONG_UP = "强势上涨"
TREND_WEAK_UP = "震荡上行"
TREND_RANGE = "横盘震荡"
TREND_WEAK_DOWN = "震荡下行"
TREND_STRONG_DOWN = "强势下跌"
TREND_UP_STATES = {TREND_STRONG_UP, TREND_WEAK_UP}
TREND_DOWN_STATES = {TREND_STRONG_DOWN, TREND_WEAK_DOWN}

# 持仓
OI_STRONG_INCREASE = "持仓大增"
OI_INCREASE = "持仓增加"
OI_STABLE = "持仓平稳"
OI_DECREASE = "持仓减少"
OI_STRONG_DECREASE = "持仓大减"
OI_INCREASE_STATES = {OI_STRONG_INCREASE, OI_INCREASE}

# 资金费率
FUNDING_EXTREME_LONG = "极端多头"
FUNDING_LONG_BIAS = "偏多头"
FUNDING_NEUTRAL = "多空平衡"
FUNDING_SHORT_BIAS = "偏空头"
FUNDING_EXTREME_SHORT = "极端空头"

# 请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
}

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

def fetch_url(url, timeout=5):
    try:
        time.sleep(random.uniform(0.3, 0.5))
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except:
        return None

def fetch_all_for_symbol(symbol):
    with ThreadPoolExecutor(max_workers=2) as executor:
        tasks = {
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={SHORT_K_INTERVAL}&limit={SHORT_K_LIMIT}"): "short_k",
            executor.submit(fetch_url, f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={SHORT_OI_PERIOD}&limit={SHORT_OI_LIMIT}"): "short_oi",
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={LONG_K_INTERVAL}&limit={LONG_K_LIMIT}"): "long_k",
            executor.submit(fetch_url, f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period={LONG_OI_PERIOD}&limit={LONG_OI_LIMIT}"): "long_oi",
            executor.submit(fetch_url, f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"): "funding"
        }
        res = {}
        for future in as_completed(tasks):
            res[tasks[future]] = future.result()
    return res.get("short_k", []), res.get("short_oi", []), res.get("long_k", []), res.get("long_oi", []), res.get("funding")

def get_trend(k_data):
    if len(k_data) < 6:
        return TREND_RANGE
    closes = [float(i[4]) for i in k_data]
    highs = [float(i[2]) for i in k_data]
    lows = [float(i[3]) for i in k_data]
    change = (closes[-1] - closes[0]) / closes[0] * 100
    if change > 15:
        return TREND_STRONG_UP
    if change < -15:
        return TREND_STRONG_DOWN
    if change > 2 and highs[-1] > max(highs[:-1]):
        return TREND_WEAK_UP
    if change < -2 and lows[-1] < min(lows[:-1]):
        return TREND_WEAK_DOWN
    return TREND_RANGE

def get_oi_state(oi_data, symbol):
    if len(oi_data) < 2:
        return OI_STABLE
    vs = [float(x["sumOpenInterest"]) for x in oi_data]
    delta = (vs[-1] - vs[0]) / vs[0]
    if delta > 0.01:
        return OI_STRONG_INCREASE
    if delta > 0:
        return OI_INCREASE
    if delta < -0.01:
        return OI_STRONG_DECREASE
    if delta < 0:
        return OI_DECREASE
    return OI_STABLE

def get_funding_state(f_data, symbol):
    if not f_data:
        return FUNDING_NEUTRAL
    v = float(f_data.get("lastFundingRate", 0))
    if v > 0.0005:
        return FUNDING_LONG_BIAS
    if v < -0.0005:
        return FUNDING_SHORT_BIAS
    return FUNDING_NEUTRAL

def detect_signal(short_trend, long_trend, short_oi, long_oi, funding, chg):
    sig = []
    if abs(chg) > 50:
        sig.append("极端行情")
    if short_trend in TREND_UP_STATES and long_trend in TREND_UP_STATES and short_oi in OI_INCREASE_STATES:
        sig.append("量价齐升，资金进场")
    return sig if sig else ["中性"]

def detect_conflict(short_trend, long_trend, short_oi, long_oi, funding, chg):
    con = []
    if short_trend in TREND_UP_STATES and short_oi not in OI_INCREASE_STATES:
        con.append("上涨无资金支撑")
    return con if con else ["无明显冲突"]

def build_topic_text(d, short_trend, long_trend, short_oi, long_oi, funding, funding_rate_val, signals, conflicts):
    price = f"{float(d['lastPrice']):.8f}".rstrip("0").rstrip(".")
    chg = round(float(d["priceChangePercent"]), 2)
    high = d["highPrice"]
    low = d["lowPrice"]
    amplitude = round((float(high) - float(low)) / float(low) * 100, 2)
    sig = "；".join(signals)
    conf = "；".join(conflicts)
    funding_val_str = f"{funding_rate_val:.4%}"

    return (
        f"{d['symbol']}，价格{price}，24h涨跌幅{chg}%，24h振幅{amplitude}%\n"
        f"市场趋势：过去3小时{short_trend}，过去24小时{long_trend}。\n"
        f"持仓情况：过去3小时{short_oi}，过去24小时{long_oi}\n"
        f"资金费率情况：{funding}（当前费率：{funding_val_str}）。\n"
        f"市场信号：{sig}\n"
        f"市场信号冲突：{conf}"
    )

def run_topic():
    ticker = fetch_url("https://fapi.binance.com/fapi/v1/ticker/24hr")
    exchange_info = fetch_url("https://fapi.binance.com/fapi/v1/exchangeInfo")
    if not ticker or not exchange_info:
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

    topic_text = build_topic_text(selected_item, short_trend, long_trend, short_oi, long_oi, funding_st, funding_val, sig, conf)
    topic_dict = {
        "symbol": symbol,
        "text": topic_text,
        "change": chg,
        "volume_ratio": 1.0,
        "news": ""
    }
    return topic_dict

def get_single_symbol_topic(symbol):
    ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
    if not ticker:
        return {"symbol": symbol, "text": "行情获取失败", "change": 0, "volume_ratio": 1.0, "news": ""}
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
    topic_text = build_topic_text(ticker, short_trend, long_trend, short_oi, long_oi, funding_st, funding_val, sig, conf)
    return {"symbol": symbol, "text": topic_text, "change": chg, "volume_ratio": 1.0, "news": ""}

if __name__ == "__main__":
    print(run_topic())
