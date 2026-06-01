# -*- coding: utf-8 -*-
"""
币安广场发文助手 - 话题生成模块（增强版）
整合市场行为特征分析器，生成深度市场分析报告。
对外接口保持不变：run_topic(target_symbol=None) 和 get_single_symbol_topic(symbol)
输出包含K线技术分析 + 资金费率/OI/大户多空比等资金博弈分析。
"""
import requests
import math
import random
import json
import os
import time
from datetime import datetime, timedelta, UTC
from concurrent.futures import ThreadPoolExecutor, as_completed

# ======================== 原有配置（保留） ========================
HISTORY_FILE = "data/memory.json"
OUTPUT_FILE = "data/topics.json"
MAX_PER_SYMBOL_24H = 2
COOLDOWN_MINUTES = 30
SOFT_COOLDOWN_MINUTES = 120

# ======================== 市场行为分析器参数配置 ========================
# 周期定义（名称, K线间隔, 短期窗口, 中期窗口, 长期窗口, 最小K线数, 是否计算订单流, 是否计算微观形态）
PERIODS = [
    {"name": "15m", "interval": "15m", "short": 2, "mid": 4, "long": 8,
     "min_klines": 100, "calc_orderflow": True, "calc_micro": True},
    {"name": "1h",  "interval": "1h",  "short": 4, "mid": 8, "long": 24,
     "min_klines": 100, "calc_orderflow": True, "calc_micro": True},
    {"name": "4h",  "interval": "4h",  "short": 12, "mid": 18, "long": 24,
     "min_klines": 100, "calc_orderflow": False, "calc_micro": False},
    {"name": "1d",  "interval": "1d",  "short": 7, "mid": 15, "long": 30,
     "min_klines": 100, "calc_orderflow": False, "calc_micro": False},
]

# 总体价格特征参数
OVERALL_MONTHLY_LIMIT = 24   # 近2年月度K线根数
OVERALL_QUARTER_DAYS = 90    # 近3个月日线根数
OVERALL_MONTH_DAYS = 30      # 近1个月日线根数

# 通用阈值
TREND_SLOPE_THRESHOLD_RATIO = 0.0001   # 趋势方向斜率阈值系数
EFFICIENCY_WINDOW = 7                  # 趋势强度窗口
ATR_PERIOD = 7                        # ATR周期
ATR_COMPARE_WINDOW = 11                # ATR比较窗口
BOLL_PERIOD = 11                       # 布林带周期
BOLL_STD_MULT = 2                      # 布林带标准差倍数
VOLUME_MA_WINDOW = 20                  # 成交量均线窗口
HIGH_VOLUME_RATIO = 1.2                # 放量阈值
LOW_VOLUME_RATIO = 0.8                 # 缩量阈值
AMPLITUDE_HIGH_RATIO = 2.0             # 异常振幅阈值
DELTA_TREND_WINDOW = 5                 # Delta趋势窗口
DIVERGENCE_LOOKBACK = 11               # 背离检测窗口
ENERGY_EXHAUSTION_PRICE_WINDOW = 7    # 价格创新高窗口
ENERGY_EXHAUSTION_DELTA_DOWN = 3       # Delta连续下降窗口

# ======================== 资金费率/OI/多空比参数 ========================
FUNDING_HISTORY_DAYS = 30
FUNDING_EXTREME_PERCENTILE = 90
FUNDING_HIGH_WARNING = 0.0005
FUNDING_LOW_WARNING = -0.0005
OI_PERIOD = "15m"
OI_HISTORY_LIMIT = 200
OI_HIGH_WATERMARK_PERCENTILE = 90
LS_RATIO_PERIOD = "1h"
LS_RATIO_LIMIT = 168
LS_RATIO_EXTREME_LONG = 1.5
LS_RATIO_EXTREME_SHORT = 0.6

# ======================== 等级映射函数 ========================
def map_trend_strength(score):
    if score >= 70: return "极强"
    if score >= 50: return "较强"
    if score >= 30: return "中等"
    if score >= 10: return "较弱"
    return "极弱"

def map_price_position(pct):
    if pct >= 80: return "高位区"
    if pct >= 60: return "中高位"
    if pct >= 40: return "中位"
    if pct >= 20: return "中低位"
    return "低位区"

def map_volatility_expansion(rate):
    if rate > 30: return "显著扩张"
    if rate > 10: return "温和扩张"
    if rate < -10: return "收缩"
    return "持平"

def map_bollinger_compression(bandwidth):
    if bandwidth < 2: return "极度压缩"
    if bandwidth < 4: return "压缩"
    if bandwidth < 8: return "正常"
    if bandwidth < 12: return "扩张"
    return "极度扩张"

def map_amplitude_ratio(ratio):
    if ratio >= 2.0: return "异常波动"
    if ratio >= 1.2: return "放大"
    if ratio <= 0.5: return "萎缩"
    return "正常"

def map_delta_strength(strength):
    if strength >= 30: return "强攻击性"
    if strength >= 15: return "中等"
    return "弱"

def map_active_buy_ratio(ratio):
    if ratio > 60: return "买方主动主导"
    if ratio < 40: return "卖方主动主导"
    return "多空平衡"

def map_avg_trade_size(ratio):
    if ratio >= 1.5: return "大资金参与"
    if ratio <= 0.8: return "散户为主"
    return "正常"

def map_relative_volume(ratio):
    if ratio >= 2.0: return "异常放量"
    if ratio >= 1.2: return "放量"
    if ratio <= 0.5: return "缩量"
    return "正常"

def map_body_ratio(ratio):
    if ratio >= 70: return "大实体"
    if ratio >= 40: return "中实体"
    if ratio >= 10: return "小实体"
    return "十字星"

def map_wick_ratio(ratio):
    if ratio > 50: return "长影线"
    if ratio > 20: return "中等"
    return "短影线"

def map_momentum_acceleration(acc):
    return "加速" if acc > 0 else "减速" if acc < 0 else "平稳"

def map_vwap_position(deviation):
    if deviation > 0.5: return "多头控制"
    if deviation < -0.5: return "空头控制"
    return "中性"

# ======================== 辅助函数：根据周期和窗口根数返回时间描述 ========================
def _get_time_desc(period_name, window):
    """返回如 '过去半小时' 的时间描述"""
    if period_name == "15m":
        minutes = 15 * window
        if minutes < 60:
            return f"过去{minutes}分钟"
        else:
            hours = minutes / 60
            return f"过去{int(hours)}小时" if hours == int(hours) else f"过去{hours:.1f}小时"
    elif period_name == "1h":
        hours = 1 * window
        if hours < 24:
            return f"过去{hours}小时"
        else:
            days = hours / 24
            return f"过去{int(days)}天" if days == int(days) else f"过去{days:.1f}天"
    elif period_name == "4h":
        hours = 4 * window
        if hours < 24:
            return f"过去{hours}小时"
        else:
            days = hours / 24
            return f"过去{int(days)}天" if days == int(days) else f"过去{days:.1f}天"
    elif period_name == "1d":
        days = 1 * window
        return f"过去{days}天"
    else:
        return f"过去{window}根"

# ======================== 工具函数（保留原有） ========================
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

# ======================== 增强的API数据拉取（同步，带缓存） ========================
_klines_cache = {}
_oi_cache = {}
_funding_rate_cache = {}
_funding_hist_cache = {}
_funding_info_cache = {}
_ls_account_cache = {}
_ls_position_cache = {}
_cache_ttl = 300  # 5分钟

def _get_cache(cache_dict, key):
    if key in cache_dict:
        data, ts = cache_dict[key]
        if time.time() - ts < _cache_ttl:
            return data
        del cache_dict[key]
    return None

def _set_cache(cache_dict, key, data):
    cache_dict[key] = (data, time.time())

def fetch_klines_sync(symbol, interval, limit=200):
    """同步获取K线数据，支持15m/1h/4h/1d/1M"""
    cache_key = f"{symbol}_{interval}_{limit}"
    cached = _get_cache(_klines_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print(f"请求K线失败 {symbol} {interval}: {e}")
        return None
    klines = []
    for k in raw:
        item = {
            "timestamp": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        if interval in ["15m", "1h"]:
            item["taker_buy_volume"] = float(k[9]) if len(k) > 9 else 0.0
            item["trades_count"] = int(k[8]) if len(k) > 8 else 0
        klines.append(item)
    _set_cache(_klines_cache, cache_key, klines)
    return klines

def fetch_oi_hist_sync(symbol, period="15m", limit=200):
    cache_key = f"oi_{symbol}_{period}_{limit}"
    cached = _get_cache(_oi_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": period, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"请求OI失败 {symbol}: {e}")
        return None
    oi_points = [{"timestamp": item["timestamp"], "oi_value": float(item["sumOpenInterest"])} for item in data]
    _set_cache(_oi_cache, cache_key, oi_points)
    return oi_points

def fetch_current_funding_rate_sync(symbol):
    cache_key = f"funding_rate_{symbol}"
    cached = _get_cache(_funding_rate_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    params = {"symbol": symbol}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rate = float(data["lastFundingRate"])
        next_time = data.get("nextFundingTime", 0)
    except Exception as e:
        print(f"请求资金费率失败 {symbol}: {e}")
        return None
    _set_cache(_funding_rate_cache, cache_key, (rate, next_time))
    return (rate, next_time)

def fetch_funding_history_sync(symbol, limit=500):
    cache_key = f"funding_hist_{symbol}_{limit}"
    cached = _get_cache(_funding_hist_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/fapi/v1/fundingRate"
    params = {"symbol": symbol, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"请求资金费率历史失败 {symbol}: {e}")
        return None
    history = [{"timestamp": item["fundingTime"], "funding_rate": float(item["fundingRate"])} for item in data]
    _set_cache(_funding_hist_cache, cache_key, history)
    return history

def fetch_funding_info_sync(symbol):
    """获取结算周期（小时），默认8"""
    cache_key = f"funding_info_{symbol}"
    cached = _get_cache(_funding_info_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/fapi/v1/fundingInfo"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"请求资金费率信息失败 {symbol}: {e}")
        return 8
    interval = 8
    for item in data:
        if item.get("symbol") == symbol:
            interval = int(item.get("fundingIntervalHours", 8))
            break
    _set_cache(_funding_info_cache, cache_key, interval)
    return interval

def fetch_ls_ratio_account_sync(symbol, period="1h", limit=168):
    """大户账户数多空比"""
    cache_key = f"ls_account_{symbol}_{period}_{limit}"
    cached = _get_cache(_ls_account_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/futures/data/topLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"请求大户账户多空比失败 {symbol}: {e}")
        return None
    ratios = [{"timestamp": item["timestamp"], "ratio": float(item["longShortRatio"])} for item in data]
    _set_cache(_ls_account_cache, cache_key, ratios)
    return ratios

def fetch_ls_ratio_position_sync(symbol, period="1h", limit=168):
    """大户持仓量多空比"""
    cache_key = f"ls_position_{symbol}_{period}_{limit}"
    cached = _get_cache(_ls_position_cache, cache_key)
    if cached:
        return cached
    url = "https://fapi.binance.com/futures/data/topLongShortPositionRatio"
    params = {"symbol": symbol, "period": period, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"请求大户持仓多空比失败 {symbol}: {e}")
        return None
    ratios = [{"timestamp": item["timestamp"], "ratio": float(item["longShortRatio"])} for item in data]
    _set_cache(_ls_position_cache, cache_key, ratios)
    return ratios

# ======================== 特征计算函数（与 analyzer.py 完全一致） ========================
def linear_regression_slope(y):
    n = len(y)
    if n < 2:
        return 0.0
    x = list(range(n))
    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi*yi for xi,yi in zip(x,y))
    sum_xx = sum(xi*xi for xi in x)
    denom = n*sum_xx - sum_x*sum_x
    if denom == 0:
        return 0.0
    return (n*sum_xy - sum_x*sum_y) / denom

def calc_trend_direction(closes, window, last_price):
    if len(closes) < window:
        return None, "数据不足"
    y = closes[-window:]
    slope = linear_regression_slope(y)
    threshold = last_price * TREND_SLOPE_THRESHOLD_RATIO
    if abs(slope) < threshold:
        dir_str = "横盘"
    else:
        dir_str = "上涨" if slope > 0 else "下跌"
    return slope, dir_str

def calc_trend_strength(closes):
    if len(closes) < EFFICIENCY_WINDOW:
        return None, "数据不足"
    y = closes[-EFFICIENCY_WINDOW:]
    net = abs(y[-1] - y[0])
    path = sum(abs(y[i]-y[i-1]) for i in range(1, len(y)))
    if path == 0:
        ratio = 0.0
    else:
        ratio = net / path
    score = ratio * 100
    grade = map_trend_strength(score)
    return score, grade

def calc_price_position(highs, lows, current_close):
    if not highs or not lows:
        return None, "数据不足"
    h = max(highs)
    l = min(lows)
    if h == l:
        pct = 50.0
    else:
        pct = (current_close - l) / (h - l) * 100
    pct = max(0, min(100, pct))
    grade = map_price_position(pct)
    return pct, grade

def calc_atr(highs, lows, closes):
    if len(closes) < ATR_PERIOD + 1:
        return None, None
    tr_list = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr = max(hl, hc, lc)
        tr_list.append(tr)
    atr = sum(tr_list[-ATR_PERIOD:]) / ATR_PERIOD
    if len(tr_list) >= ATR_COMPARE_WINDOW:
        atr_avg = sum(tr_list[-ATR_COMPARE_WINDOW:]) / ATR_COMPARE_WINDOW
    else:
        atr_avg = atr
    return atr, atr_avg

def calc_volatility_expansion(atr, atr_avg):
    if atr_avg == 0:
        rate = 0.0
    else:
        rate = (atr / atr_avg - 1) * 100
    grade = map_volatility_expansion(rate)
    return rate, grade

def calc_bollinger_bandwidth(closes):
    if len(closes) < BOLL_PERIOD:
        return None, "数据不足"
    window = closes[-BOLL_PERIOD:]
    sma = sum(window) / BOLL_PERIOD
    variance = sum((x - sma)**2 for x in window) / BOLL_PERIOD
    std = math.sqrt(variance)
    bandwidth = (2 * BOLL_STD_MULT * std) / sma * 100 if sma != 0 else 0
    grade = map_bollinger_compression(bandwidth)
    return bandwidth, grade

def calc_amplitude_ratio(high, low, avg_amplitude):
    amp = (high - low) / low * 100 if low != 0 else 0
    if avg_amplitude == 0:
        ratio = 1.0
    else:
        ratio = amp / avg_amplitude
    grade = map_amplitude_ratio(ratio)
    return amp, ratio, grade

def calc_delta(volume, taker_buy):
    return 2 * taker_buy - volume

def calc_delta_strength(delta, volume):
    if volume == 0:
        return 0, "弱"
    strength = abs(delta) / volume * 100
    grade = map_delta_strength(strength)
    return strength, grade

def calc_delta_trend(deltas):
    if len(deltas) < 2:
        return "平稳"
    slope = linear_regression_slope(deltas)
    if slope > 2:
        return "上升"
    elif slope < -2:
        return "下降"
    else:
        return "平稳"

def calc_delta_divergence(prices, deltas):
    if len(prices) < DIVERGENCE_LOOKBACK or len(deltas) < DIVERGENCE_LOOKBACK:
        return False
    recent_prices = prices[-DIVERGENCE_LOOKBACK:]
    recent_deltas = deltas[-DIVERGENCE_LOOKBACK:]
    price_max = max(recent_prices)
    delta_max = max(recent_deltas)
    if prices[-1] >= price_max * 0.99 and deltas[-1] < delta_max * 0.8:
        return True
    return False

def calc_active_buy_ratio(taker_buy, volume):
    if volume == 0:
        return 50.0, "多空平衡"
    ratio = taker_buy / volume * 100
    grade = map_active_buy_ratio(ratio)
    return ratio, grade

def calc_avg_trade_size(volume, trades_count, hist_avg):
    if trades_count == 0:
        return 0, "数据不足"
    cur = volume / trades_count
    if hist_avg == 0:
        ratio = 1.0
    else:
        ratio = cur / hist_avg
    grade = map_avg_trade_size(ratio)
    return cur, ratio, grade

def calc_relative_volume(current_vol, vol_ma):
    if vol_ma == 0:
        return 1.0, "正常"
    ratio = current_vol / vol_ma
    grade = map_relative_volume(ratio)
    return ratio, grade

def calc_volume_trend(volumes):
    if len(volumes) < 2:
        return "平稳"
    slope = linear_regression_slope(volumes)
    if slope > 0:
        return "上升"
    elif slope < 0:
        return "下降"
    else:
        return "平稳"

def calc_volume_extreme(current_vol, vol_history):
    sorted_vol = sorted(vol_history, reverse=True)
    top2 = sorted_vol[:2] if len(sorted_vol) >= 2 else sorted_vol
    return current_vol in top2

def calc_volume_cluster(volumes, ma, threshold=1.5):
    recent = volumes[-3:] if len(volumes) >= 3 else volumes
    cluster = sum(1 for v in recent if v > ma * threshold)
    return cluster >= 2

def calc_swing_structure(highs, lows):
    if len(highs) < 20 or len(lows) < 20:
        return "数据不足"
    recent_highs = highs[-20:]
    recent_lows = lows[-20:]
    high_trend = recent_highs[-1] > recent_highs[0]
    low_trend = recent_lows[-1] > recent_lows[0]
    if high_trend and low_trend:
        return "HH+HL (上升结构)"
    elif not high_trend and not low_trend:
        return "LH+LL (下降结构)"
    else:
        return "杂乱 (震荡)"

def calc_structure_breakout(close, swing_high, swing_low, vol_ratio):
    if close > swing_high:
        direction = "向上突破"
        effective = "有效突破" if vol_ratio >= 1.5 else "疑似假突破"
        return direction, effective
    elif close < swing_low:
        direction = "向下突破"
        effective = "有效突破" if vol_ratio >= 1.5 else "疑似假突破"
        return direction, effective
    return "无", ""

def calc_support_resistance(highs, lows):
    short_res = max(highs[-5:]) if len(highs) >= 5 else 0
    short_sup = min(lows[-5:]) if len(lows) >= 5 else 0
    main_res = max(highs[-20:]) if len(highs) >= 20 else 0
    main_sup = min(lows[-20:]) if len(lows) >= 20 else 0
    return short_res, short_sup, main_res, main_sup

def calc_body_ratio(open_p, close, high, low):
    body = abs(close - open_p)
    total = high - low
    if total == 0:
        return 0, "十字星"
    ratio = body / total * 100
    grade = map_body_ratio(ratio)
    return ratio, grade

def calc_wick_ratios(open_p, close, high, low):
    total = high - low
    if total == 0:
        return 0, 0, "短影线", "短影线"
    top = high - max(open_p, close)
    bottom = min(open_p, close) - low
    upper_ratio = top / total * 100
    lower_ratio = bottom / total * 100
    grade_upper = map_wick_ratio(upper_ratio)
    grade_lower = map_wick_ratio(lower_ratio)
    return upper_ratio, lower_ratio, grade_upper, grade_lower

def calc_candlestick_pattern(k1, k2):
    if k1['close'] < k1['open'] and k2['close'] > k2['open']:
        if k2['open'] <= k1['close'] and k2['close'] >= k1['open']:
            return "看涨吞没"
    if k1['close'] > k1['open'] and k2['close'] < k2['open']:
        if k2['open'] >= k1['close'] and k2['close'] <= k1['open']:
            return "看跌吞没"
    return "无"

def calc_momentum(closes, n):
    if len(closes) < n+1:
        return None
    return closes[-1] - closes[-n-1]

def calc_momentum_acceleration(mom_cur, mom_prev):
    if mom_cur is None or mom_prev is None:
        return 0, "平稳"
    acc = mom_cur - mom_prev
    grade = map_momentum_acceleration(acc)
    return acc, grade

def calc_vwap_position(closes, volumes, atr):
    if len(closes) < 20 or atr == 0:
        return 0, "中性"
    vwap = sum(c*v for c,v in zip(closes[-20:], volumes[-20:])) / sum(volumes[-20:])
    deviation = (closes[-1] - vwap) / atr
    grade = map_vwap_position(deviation)
    return deviation, grade

def calc_volume_price_coordination(price_dir, vol_ratio):
    if price_dir == "涨":
        if vol_ratio >= 1.2:
            return "量价齐升 (健康上涨)"
        elif vol_ratio <= 0.8:
            return "价升量缩 (上涨乏力)"
        else:
            return "量价正常"
    else:
        if vol_ratio >= 1.2:
            return "量价齐跌 (健康下跌)"
        elif vol_ratio <= 0.8:
            return "价跌量缩 (下跌乏力)"
        else:
            return "量价正常"

def calc_energy_exhaustion(prices, deltas, relative_vols):
    if len(prices) < 15:
        return False
    recent_high = max(prices[-5:])
    earlier_high = max(prices[-15:-5])
    if recent_high <= earlier_high:
        return False
    if len(deltas) >= 3 and all(deltas[-i] <= deltas[-i-1] for i in range(1,3)):
        return True
    if len(relative_vols) >= 3 and all(relative_vols[-i] <= relative_vols[-i-1] for i in range(1,3)):
        return True
    return False

# ==================== 单周期分析函数（不含短期建议） ====================
def analyze_period(klines, period_cfg):
    if not klines or len(klines) < period_cfg["min_klines"]:
        return None
    closes = [k['close'] for k in klines]
    highs = [k['high'] for k in klines]
    lows = [k['low'] for k in klines]
    volumes = [k['volume'] for k in klines]
    last_close = closes[-1]
    last_high = highs[-1]
    last_low = lows[-1]
    last_open = klines[-1]['open']

    short_slope, short_dir = calc_trend_direction(closes, period_cfg['short'], last_close)
    mid_slope, mid_dir = calc_trend_direction(closes, period_cfg['mid'], last_close)
    long_slope, long_dir = calc_trend_direction(closes, period_cfg['long'], last_close)
    strength_score, strength_grade = calc_trend_strength(closes)
    persistence = 0
    for i in range(len(klines)-1, -1, -1):
        if klines[i]['close'] > klines[i]['open']:
            persistence += 1
        else:
            break
    pos_pct, pos_grade = calc_price_position(highs[-period_cfg['long']:], lows[-period_cfg['long']:], last_close)

    atr, atr_avg = calc_atr(highs, lows, closes)
    if atr is not None:
        exp_rate, exp_grade = calc_volatility_expansion(atr, atr_avg)
    else:
        exp_rate, exp_grade = None, "数据不足"
    bandwidth, bb_grade = calc_bollinger_bandwidth(closes)
    amp_avg = sum((highs[i]-lows[i])/lows[i]*100 for i in range(-20,0)) / 20 if len(lows)>=20 else 0
    amp_cur, amp_ratio, amp_grade = calc_amplitude_ratio(last_high, last_low, amp_avg)

    delta_cur = None
    delta_strength = None
    delta_strength_grade = None
    delta_trend = None
    divergence = None
    active_ratio = None
    active_grade = None
    avg_trade = None
    at_grade = None
    delta_vals = []

    if period_cfg.get("calc_orderflow", False) and "taker_buy_volume" in klines[-1]:
        taker_buy = [k['taker_buy_volume'] for k in klines]
        trades = [k['trades_count'] for k in klines]
        delta_vals = [calc_delta(volumes[i], taker_buy[i]) for i in range(len(klines))]
        delta_cur = delta_vals[-1]
        delta_strength, delta_strength_grade = calc_delta_strength(delta_cur, volumes[-1])
        delta_trend = calc_delta_trend(delta_vals[-5:])
        divergence = calc_delta_divergence(closes, delta_vals)
        active_ratio, active_grade = calc_active_buy_ratio(taker_buy[-1], volumes[-1])
        hist_avg_trade = sum(volumes[-20-i]/trades[-20-i] for i in range(20) if trades[-20-i]>0) / 20 if len(trades)>=20 else 0
        avg_trade, _, at_grade = calc_avg_trade_size(volumes[-1], trades[-1], hist_avg_trade)

    vol_ma = sum(volumes[-VOLUME_MA_WINDOW:]) / VOLUME_MA_WINDOW if len(volumes)>=VOLUME_MA_WINDOW else 0
    rel_vol_ratio, rel_vol_grade = calc_relative_volume(volumes[-1], vol_ma)
    vol_trend = calc_volume_trend(volumes[-5:])
    vol_extreme = calc_volume_extreme(volumes[-1], volumes[-20:])
    vol_cluster = calc_volume_cluster(volumes, vol_ma)

    swing_struct = calc_swing_structure(highs, lows)
    swing_high = max(highs[-10:]) if len(highs)>=10 else last_high
    swing_low = min(lows[-10:]) if len(lows)>=10 else last_low
    break_dir, break_eff = calc_structure_breakout(last_close, swing_high, swing_low, rel_vol_ratio)
    short_res, short_sup, main_res, main_sup = calc_support_resistance(highs, lows)

    body_ratio = None
    body_grade = None
    upper_wick = None
    lower_wick = None
    uw_grade = None
    lw_grade = None
    pattern = None
    if period_cfg.get("calc_micro", False):
        body_ratio, body_grade = calc_body_ratio(last_open, last_close, last_high, last_low)
        upper_wick, lower_wick, uw_grade, lw_grade = calc_wick_ratios(last_open, last_close, last_high, last_low)
        if len(klines) >= 2:
            pattern = calc_candlestick_pattern(klines[-2], klines[-1])
        else:
            pattern = "无"

    mom5 = calc_momentum(closes, 5)
    mom10 = calc_momentum(closes, 10)
    mom20 = calc_momentum(closes, 20)
    mom10_prev = calc_momentum(closes[:-1], 10) if len(closes)>1 else None
    mom_acc, mom_acc_grade = calc_momentum_acceleration(mom10, mom10_prev)
    vwap_dev, vwap_grade = calc_vwap_position(closes, volumes, atr if atr else 0)
    price_dir = "涨" if last_close > closes[-2] else "跌"
    vol_price_coord = calc_volume_price_coordination(price_dir, rel_vol_ratio)
    energy_exhaust = calc_energy_exhaustion(closes, delta_vals, [rel_vol_ratio]*5)

    return {
        "trend": {
            "short": {"slope": short_slope, "dir": short_dir},
            "mid": {"slope": mid_slope, "dir": mid_dir},
            "long": {"slope": long_slope, "dir": long_dir},
            "strength": {"score": strength_score, "grade": strength_grade},
            "persistence": persistence,
            "price_position": {"pct": pos_pct, "grade": pos_grade}
        },
        "volatility": {
            "atr": atr, "atr_avg": atr_avg,
            "expansion_rate": exp_rate, "expansion_grade": exp_grade,
            "bollinger_bandwidth": bandwidth, "bollinger_grade": bb_grade,
            "amplitude_ratio": amp_ratio, "amplitude_grade": amp_grade
        },
        "orderflow": {
            "delta": delta_cur,
            "delta_strength": delta_strength, "delta_strength_grade": delta_strength_grade,
            "delta_trend": delta_trend,
            "divergence": divergence,
            "active_buy_ratio": active_ratio, "active_buy_grade": active_grade,
            "avg_trade_size": avg_trade, "avg_trade_grade": at_grade
        },
        "volume": {
            "relative_ratio": rel_vol_ratio, "relative_grade": rel_vol_grade,
            "trend": vol_trend,
            "extreme": vol_extreme,
            "cluster": vol_cluster
        },
        "structure": {
            "swing_structure": swing_struct,
            "breakout_direction": break_dir, "breakout_effectiveness": break_eff,
            "short_resistance": short_res, "short_support": short_sup,
            "main_resistance": main_res, "main_support": main_sup
        },
        "micro": {
            "body_ratio": body_ratio, "body_grade": body_grade,
            "upper_wick": upper_wick, "lower_wick": lower_wick,
            "upper_wick_grade": uw_grade, "lower_wick_grade": lw_grade,
            "pattern": pattern
        },
        "energy": {
            "momentum5": mom5, "momentum10": mom10, "momentum20": mom20,
            "momentum_acceleration": mom_acc, "momentum_acc_grade": mom_acc_grade,
            "vwap_deviation": vwap_dev, "vwap_grade": vwap_grade,
            "volume_price_coordination": vol_price_coord,
            "energy_exhaustion": energy_exhaust
        }
    }

# ==================== 总体价格特征（同步） ====================
def calc_overall_price_features_sync(symbol):
    result = {}
    def fmt_month(ts):
        return time.strftime("%Y年%m月", time.localtime(ts/1000))
    def fmt_day(ts):
        return time.strftime("%Y年%m月%d日", time.localtime(ts/1000))

    monthly = fetch_klines_sync(symbol, "1M", OVERALL_MONTHLY_LIMIT)
    if monthly:
        m_count = len(monthly)
        highs = [k['high'] for k in monthly]
        lows = [k['low'] for k in monthly]
        high_val = max(highs)
        low_val = min(lows)
        high_idx = highs.index(high_val)
        low_idx = lows.index(low_val)
        high_time = fmt_month(monthly[high_idx]['timestamp'])
        low_time = fmt_month(monthly[low_idx]['timestamp'])
        current = monthly[-1]['close']
        if high_val == low_val:
            pct = 50.0
        else:
            pct = (current - low_val) / (high_val - low_val) * 100
        pct = max(0, min(100, pct))
        grade = map_price_position(pct)
        result['long'] = {
            'count': m_count,
            'current': current,
            'high': high_val, 'high_time': high_time,
            'low': low_val, 'low_time': low_time,
            'pct': pct, 'grade': grade,
            'sufficient': m_count >= OVERALL_MONTHLY_LIMIT
        }
    else:
        result['long'] = {'error': True}

    daily_90 = fetch_klines_sync(symbol, "1d", OVERALL_QUARTER_DAYS)
    if daily_90:
        d90_count = len(daily_90)
        highs90 = [k['high'] for k in daily_90]
        lows90 = [k['low'] for k in daily_90]
        high_val90 = max(highs90)
        low_val90 = min(lows90)
        high_idx90 = highs90.index(high_val90)
        low_idx90 = lows90.index(low_val90)
        high_time90 = fmt_day(daily_90[high_idx90]['timestamp'])
        low_time90 = fmt_day(daily_90[low_idx90]['timestamp'])
        current = daily_90[-1]['close']
        if high_val90 == low_val90:
            pct90 = 50.0
        else:
            pct90 = (current - low_val90) / (high_val90 - low_val90) * 100
        pct90 = max(0, min(100, pct90))
        grade90 = map_price_position(pct90)
        result['mid'] = {
            'count': d90_count,
            'current': current,
            'high': high_val90, 'high_time': high_time90,
            'low': low_val90, 'low_time': low_time90,
            'pct': pct90, 'grade': grade90,
            'sufficient': d90_count >= OVERALL_QUARTER_DAYS
        }

        if len(daily_90) >= OVERALL_MONTH_DAYS:
            daily_30 = daily_90[-OVERALL_MONTH_DAYS:]
        else:
            daily_30 = daily_90
        d30_count = len(daily_30)
        highs30 = [k['high'] for k in daily_30]
        lows30 = [k['low'] for k in daily_30]
        high_val30 = max(highs30)
        low_val30 = min(lows30)
        high_idx30 = highs30.index(high_val30)
        low_idx30 = lows30.index(low_val30)
        high_time30 = fmt_day(daily_30[high_idx30]['timestamp'])
        low_time30 = fmt_day(daily_30[low_idx30]['timestamp'])
        if high_val30 == low_val30:
            pct30 = 50.0
        else:
            pct30 = (current - low_val30) / (high_val30 - low_val30) * 100
        pct30 = max(0, min(100, pct30))
        grade30 = map_price_position(pct30)
        result['short'] = {
            'count': d30_count,
            'current': current,
            'high': high_val30, 'high_time': high_time30,
            'low': low_val30, 'low_time': low_time30,
            'pct': pct30, 'grade': grade30,
            'sufficient': d30_count >= OVERALL_MONTH_DAYS
        }
    else:
        result['mid'] = {'error': True}
        result['short'] = {'error': True}
    return result

# ==================== 资金费率深度分析 ====================
def analyze_funding(symbol):
    result = {}
    # 实时费率与下次结算时间
    fr = fetch_current_funding_rate_sync(symbol)
    if fr is None:
        return None
    rate, next_time = fr
    result['current_rate'] = rate
    result['next_funding_ts'] = next_time
    # 结算周期
    interval_hours = fetch_funding_info_sync(symbol)
    result['interval_hours'] = interval_hours
    # 历史百分位
    hist = fetch_funding_history_sync(symbol, limit=500)
    if hist and len(hist) > 0:
        rates = [h['funding_rate'] for h in hist]
        # 限制30天（根据时间戳筛选）
        now_ts = time.time() * 1000
        cutoff = now_ts - FUNDING_HISTORY_DAYS * 86400000
        recent = [r for r in hist if r['timestamp'] >= cutoff]
        if recent:
            recent_rates = [r['funding_rate'] for r in recent]
            percentile = sum(1 for r in recent_rates if r < rate) / len(recent_rates) * 100
        else:
            percentile = 50.0
        result['percentile'] = percentile
        # 趋势（最近3次）
        if len(recent_rates) >= 3:
            last3 = recent_rates[-3:]
            if last3[0] < last3[1] < last3[2]:
                trend = "持续攀升"
            elif last3[0] > last3[1] > last3[2]:
                trend = "持续下降"
            else:
                trend = "震荡"
            result['trend'] = trend
        else:
            result['trend'] = "数据不足"
    else:
        result['percentile'] = 50.0
        result['trend'] = "无历史"
    return result

# ==================== OI结构分析 ====================
def analyze_oi(symbol):
    oi_data = fetch_oi_hist_sync(symbol, period=OI_PERIOD, limit=OI_HISTORY_LIMIT)
    if not oi_data or len(oi_data) < 2:
        return None
    current_oi = oi_data[-1]['oi_value']
    # 24h变化
    day_ago = None
    for item in reversed(oi_data):
        if item['timestamp'] <= time.time()*1000 - 86400000:
            day_ago = item['oi_value']
            break
    if day_ago:
        change_24h = (current_oi - day_ago) / day_ago * 100
    else:
        change_24h = 0
    # OI-价格关系需要价格数据，在外部传入
    return {
        'current_oi': current_oi,
        'change_24h': change_24h,
        'oi_data': oi_data
    }

# ==================== 大户多空比分析 ====================
def analyze_ls_ratio(symbol):
    account_ratios = fetch_ls_ratio_account_sync(symbol, period=LS_RATIO_PERIOD, limit=LS_RATIO_LIMIT)
    position_ratios = fetch_ls_ratio_position_sync(symbol, period=LS_RATIO_PERIOD, limit=LS_RATIO_LIMIT)
    if not account_ratios or not position_ratios:
        return None
    last_account = account_ratios[-1]['ratio'] if account_ratios else 1.0
    last_position = position_ratios[-1]['ratio'] if position_ratios else 1.0
    # 趋势（最近3次）
    if len(account_ratios) >= 3:
        acc_vals = [r['ratio'] for r in account_ratios[-3:]]
        if acc_vals[0] < acc_vals[1] < acc_vals[2]:
            acc_trend = "持续上升"
        elif acc_vals[0] > acc_vals[1] > acc_vals[2]:
            acc_trend = "持续下降"
        else:
            acc_trend = "震荡"
    else:
        acc_trend = "数据不足"
    return {
        'account_ratio': last_account,
        'position_ratio': last_position,
        'acc_trend': acc_trend,
        'account_ratios': account_ratios
    }

# ==================== 综合微观资金博弈分析（生成文本） ====================
def generate_micro_analysis(symbol, funding, oi, ls_ratio, price_change_24h):
    lines = []
    lines.append("\n" + "="*80)
    lines.append("【📊 微观结构与资金博弈分析】")
    lines.append("="*80)

    # 资金费率
    if funding:
        lines.append("\n💸 资金费率分析:")
        rate = funding['current_rate']
        lines.append(f"   当前费率: {rate:.4%} (每{funding['interval_hours']}小时结算一次")
        if funding.get('next_funding_ts'):
            remain_sec = max(0, (funding['next_funding_ts'] - time.time()*1000)/1000)
            remain_min = int(remain_sec // 60)
            lines.append(f"   下次结算约 {remain_min} 分钟后")
        lines.append(f"   历史30天百分位: {funding['percentile']:.1f}%")
        if funding['percentile'] >= FUNDING_EXTREME_PERCENTILE:
            lines.append(f"   ⚠️ 极端拥挤，多头支付高昂")
        elif funding['percentile'] >= 75:
            lines.append(f"   🔴 偏高拥挤")
        elif funding['percentile'] <= 10:
            lines.append(f"   ⚠️ 极端恐慌，空头支付高昂")
        lines.append(f"   费率趋势: {funding['trend']}")
        if rate > FUNDING_HIGH_WARNING:
            lines.append(f"   ⚠️ 多头支付极端高昂，持仓成本极高，多头过热信号，警惕回调。")
        elif rate < FUNDING_LOW_WARNING:
            lines.append(f"   ⚠️ 空头支付极端高昂，逼空风险高。")
    else:
        lines.append("\n💸 资金费率分析: 数据获取失败")

    # OI分析
    if oi and oi.get('current_oi'):
        cur_oi = oi['current_oi']
        change_24h = oi['change_24h']
        lines.append("\n🔥 持仓量（OI）分析:")
        lines.append(f"   当前OI: {cur_oi:.2f} USD (24h变化: {change_24h:+.1f}%)")
        # OI与价格关系（需要价格变化，从外部传入）
        if price_change_24h is not None:
            if price_change_24h > 0 and change_24h > 0:
                lines.append(f"   OI-价格关系: 价格涨 + OI增 → 新多头入场，趋势健康")
            elif price_change_24h > 0 and change_24h < 0:
                lines.append(f"   OI-价格关系: 价格涨 + OI降 → 空头平仓推升，上涨可能不可持续")
            elif price_change_24h < 0 and change_24h > 0:
                lines.append(f"   OI-价格关系: 价格跌 + OI增 → 新空头入场，下跌趋势健康")
            elif price_change_24h < 0 and change_24h < 0:
                lines.append(f"   OI-价格关系: 价格跌 + OI降 → 多头平仓砸盘，下跌可能衰竭")
        # OI百分位（粗略，用最近30天的历史）
        # 此处简化，可选
        lines.append(f"   ⚠️ OI变化显著，市场分歧大" if abs(change_24h) > 15 else "")
    else:
        lines.append("\n🔥 持仓量（OI）分析: 数据获取失败")

    # 大户多空比
    if ls_ratio:
        acc = ls_ratio['account_ratio']
        pos = ls_ratio['position_ratio']
        lines.append("\n🐋 大户多空比分析:")
        lines.append(f"   大户账户数多空比: {acc:.2f} ({'多头账户占优' if acc>1 else '空头账户占优' if acc<1 else '平衡'})")
        lines.append(f"   大户持仓量多空比: {pos:.2f} ({'多头持仓占优' if pos>1 else '空头持仓占优' if pos<1 else '平衡'})")
        if acc > LS_RATIO_EXTREME_LONG and pos > LS_RATIO_EXTREME_LONG:
            lines.append(f"   ⚠️ 大户多头极度拥挤，注意风险")
        elif acc < LS_RATIO_EXTREME_SHORT and pos < LS_RATIO_EXTREME_SHORT:
            lines.append(f"   ⚠️ 大户空头极度拥挤，逼空风险高")
        if (acc > 1 and pos < 1) or (acc < 1 and pos > 1):
            lines.append(f"   ⚠️ 账户偏多但持仓谨慎，大户内部存在分歧")
        lines.append(f"   多空比趋势: {ls_ratio['acc_trend']}")
    else:
        lines.append("\n🐋 大户多空比分析: 数据获取失败")

    # 综合评估
    lines.append("\n【综合微观结构评估】")
    risks = []
    if funding and funding['percentile'] >= FUNDING_EXTREME_PERCENTILE:
        risks.append("资金费率极端拥挤")
    if oi and abs(oi.get('change_24h', 0)) > 15:
        risks.append("OI大幅变动")
    if ls_ratio and (ls_ratio['account_ratio'] > LS_RATIO_EXTREME_LONG or ls_ratio['account_ratio'] < LS_RATIO_EXTREME_SHORT):
        risks.append("大户多空极端")
    if risks:
        lines.append(f"   ⚠️ 当前市场存在: {', '.join(risks)}，短期过热信号")
    else:
        lines.append(f"   ✅ 微观结构无明显极端信号")
    if funding and funding['percentile'] >= FUNDING_EXTREME_PERCENTILE and oi and oi.get('change_24h', 0) > 0:
        lines.append(f"   ⚠️ 费率极端 + OI增长，高成本杠杆多头堆积，回调风险大")
    elif funding and funding['percentile'] <= 10 and oi and oi.get('change_24h', 0) > 0:
        lines.append(f"   ⚠️ 费率极端 + OI增长，空头拥挤，逼空风险高")
    else:
        lines.append(f"   → 建议结合技术面进一步判断")

    return "\n".join(lines)

# ==================== 综合分析与短期策略参考（替代原K线短期建议） ====================
def generate_comprehensive_strategy(symbol, period_results, funding, oi, ls_ratio, price_change_1h, delta_15m, vol_15m, micro_15m):
    lines = []
    lines.append("\n" + "="*80)
    lines.append("【📋 综合分析与短期策略参考】（基于15分钟/1小时周期 + 资金博弈）")
    lines.append("="*80)

    data_15m = period_results.get("15m")
    data_1h = period_results.get("1h")
    if not data_15m or not data_1h:
        lines.append("⚠️ 无法获取15分钟或1小时数据，暂时无法生成综合策略。")
        return "\n".join(lines)

    t15 = data_15m['trend']
    t1h = data_1h['trend']
    o15 = data_15m['orderflow']
    v15 = data_15m['volatility']
    micro15 = data_15m['micro']

    lines.append(f"1️⃣ 方向判断（技术面）:")
    lines.append(f"   15分钟{t15['short']['dir']}（斜率 {t15['short']['slope']:.2f}），1小时中期{t1h['mid']['dir']}。")
    if t15['short']['dir'] == "上涨" and t1h['mid']['dir'] == "下跌":
        lines.append(f"   → 短线反弹受制于中周期下跌趋势，追多需谨慎。")
    elif t15['short']['dir'] == "下跌" and t1h['mid']['dir'] == "上涨":
        lines.append(f"   → 短线回调但中周期向上，可等待企稳后低吸。")
    else:
        lines.append(f"   → 周期方向一致，顺势交易。")
    # 补充资金面
    if funding and funding['percentile'] >= 75:
        lines.append(f"   补充资金面: 资金费率百分位{funding['percentile']:.1f}%（极端拥挤），追高风险加剧。")
    elif funding and funding['percentile'] <= 25:
        lines.append(f"   补充资金面: 资金费率低位，恐慌情绪存在，可能反弹。")

    lines.append(f"\n2️⃣ 动能评估（技术面+订单流）:")
    if o15['delta'] is not None:
        lines.append(f"   15分钟Delta = {o15['delta']:.0f}（{'主动买强' if o15['delta']>0 else '主动卖强'}），强度 {o15['delta_strength']:.1f}%({o15['delta_strength_grade']})。")
        if o15['delta_strength'] > 30 and o15['delta'] > 0:
            lines.append(f"   → 主动买盘强劲，短期多头动能充足。")
        elif o15['delta_strength'] > 30 and o15['delta'] < 0:
            lines.append(f"   → 主动卖盘强劲，短期空头占优。")
        else:
            lines.append(f"   → 多空力量均衡，方向不明。")
    # 资金面补充
    if oi and abs(oi.get('change_24h', 0)) > 10:
        lines.append(f"   补充资金面: 24h OI变化{oi['change_24h']:+.1f}%，资金大幅{'流入' if oi['change_24h']>0 else '流出'}。")

    lines.append(f"\n3️⃣ 波动与结构:")
    lines.append(f"   15分钟布林带宽度 {v15['bollinger_bandwidth']:.2f}%（{v15['bollinger_grade']}），波动扩张率 {v15['expansion_rate']:.1f}%。")
    if "压缩" in v15['bollinger_grade']:
        lines.append(f"   → 变盘在即，突破方向有待确认。")
    else:
        lines.append(f"   → 正常波动，维持当前趋势。")
    if micro15['pattern']:
        lines.append(f"   微观形态: {micro15['pattern']}，短期方向可能变化。")

    lines.append(f"\n4️⃣ 综合资金面提示:")
    if funding and funding['percentile'] >= FUNDING_EXTREME_PERCENTILE:
        lines.append(f"   ⚠️ 资金费率百分位{funding['percentile']:.1f}%（极端拥挤）+ OI高位 → 市场过热，追高风险极大。")
    if oi and oi.get('current_oi'):
        lines.append(f"   ✅ 若价格回调后费率冷却、OI维持增长，则是健康调整；若放量下跌配合多头踩踏，则趋势可能反转。")

    lines.append(f"\n5️⃣ 短期策略参考（非交易信号）:")
    lines.append(f"   - 当前短线偏多但空间受限，叠加{'极端费率' if funding and funding['percentile']>=75 else '正常费率'}，建议等待回调至支撑区再观察。")
    lines.append(f"   - 若15分钟跌破最近低点或Delta持续为负，则可能转为空头，需警惕踩踏。")
    if funding and funding.get('next_funding_ts'):
        lines.append(f"   - 关注下期资金费率结算（约{max(0,int((funding['next_funding_ts']-time.time()*1000)/60000))}分钟后），若费率大幅下降可缓解过热压力。")

    return "\n".join(lines)

# ==================== 完整报告生成（删除原短期建议，增加微观分析和综合策略） ====================
def generate_full_report(symbol, period_results, overall, funding, oi, ls_ratio, price_change_24h, price_change_1h, delta_15m, vol_15m, micro_15m):
    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"📊 市场行为特征分析报告 - {symbol}")
    lines.append(f"⏱️ 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append('='*80)

    # ----- 总体价格特征（不变） -----
    lines.append("\n【🌍 总体价格特征】")
    ov = overall['long']
    if not ov.get('error'):
        suff = "" if ov['sufficient'] else f"（实际{ov['count']}个月，不足24个月）"
        lines.append(f"📅 近2年（定义为长周期，使用月度K线计算{suff}）:")
        lines.append(f"   💰 当前价格: {ov['current']}")
        lines.append(f"   📈 过去 {ov['count']} 个月高点: {ov['high']}（{ov['high_time']}）")
        lines.append(f"   📉 过去 {ov['count']} 个月低点: {ov['low']}（{ov['low_time']}）")
        lines.append(f"   📍 当前价格位置（低到高的分位数）: {ov['pct']:.1f}%（{ov['grade']}）")
        if ov['pct'] >= 80:
            lines.append(f"   🔴 解读: 价格处于长周期高位，可能继续新高，但更要警惕回调风险。")
        elif ov['pct'] <= 20:
            lines.append(f"   🟢 解读: 价格处于长周期低位，可能存在价格修复机会。")
        else:
            lines.append(f"   🟡 解读: 价格处于长周期中间区域，观望为主。")
        if not ov['sufficient']:
            lines.append(f"   ⚠️ 注意：该币种上市共 {ov['count']} 个月。")
    else:
        lines.append("📅 近2年（月度K线）：无法获取数据")

    ov = overall['mid']
    if not ov.get('error'):
        suff = "" if ov['sufficient'] else f"（实际{ov['count']}天，不足90天）"
        lines.append(f"\n📅 近3个月（定义为中周期，使用日K线计算{suff}）:")
        lines.append(f"   💰 当前价格: {ov['current']}")
        lines.append(f"   📈 过去 {ov['count']} 天高点: {ov['high']}（{ov['high_time']}）")
        lines.append(f"   📉 过去 {ov['count']} 天低点: {ov['low']}（{ov['low_time']}）")
        lines.append(f"   📍 当前位置: {ov['pct']:.1f}%（{ov['grade']}）")
        if ov['pct'] >= 80:
            lines.append(f"   🔴 解读: 价格处于中周期高位，短期超买，追高风险大。")
        elif ov['pct'] <= 20:
            lines.append(f"   🟢 解读: 价格处于中周期低位，超卖反弹概率增加。")
        else:
            lines.append(f"   🟡 解读: 价格处于中周期正常区间，方向不明。")
        if not ov['sufficient']:
            lines.append(f"   ⚠️ 注意：该币种上市仅 {ov['count']} 天，数据不足90天。")
    else:
        lines.append("\n📅 近3个月（日K线）：无法获取数据")

    ov = overall['short']
    if not ov.get('error'):
        suff = "" if ov['sufficient'] else f"（实际{ov['count']}天，不足30天）"
        lines.append(f"\n📅 近1个月（定义为短周期，使用日K线计算{suff}）:")
        lines.append(f"   💰 当前价格: {ov['current']}")
        lines.append(f"   📈 过去 {ov['count']} 天高点: {ov['high']}（{ov['high_time']}）")
        lines.append(f"   📉 过去 {ov['count']} 天低点: {ov['low']}（{ov['low_time']}）")
        lines.append(f"   📍 当前位置: {ov['pct']:.1f}%（{ov['grade']}）")
        if ov['pct'] >= 80:
            lines.append(f"   🔴 解读: 价格处于短期高位，注意超买风险。")
        elif ov['pct'] <= 20:
            lines.append(f"   🟢 解读: 价格处于短期低位，超跌反弹可能。")
        else:
            lines.append(f"   🟡 解读: 价格处于短期正常区间。")
        if not ov['sufficient']:
            lines.append(f"   ⚠️ 注意：该币种上市仅 {ov['count']} 天，数据不足30天。")
    else:
        lines.append("\n📅 近1个月（日K线）：无法获取数据")

    # ----- 四周期状态对比表（不变） -----
    lines.append("\n【📈 四周期状态对比表】")
    header = f"{'周期':<6} {'趋势方向':<22} {'强度':<10} {'价格位置':<10} {'波动状态':<10} {'结构':<12} {'综合评语'}"
    lines.append(header)
    lines.append("-"*110)
    period_configs = {p["name"]: p for p in PERIODS}
    for period_name, data in period_results.items():
        if data is None:
            lines.append(f"{period_name:<6} 数据不足")
            continue
        t = data['trend']
        short_dir = t['short']['dir'][:2] if t['short']['dir'] else '--'
        mid_dir = t['mid']['dir'][:2] if t['mid']['dir'] else '--'
        long_dir = t['long']['dir'][:2] if t['long']['dir'] else '--'
        trend_str = f"{short_dir}/{mid_dir}/{long_dir}"
        strength = f"{t['strength']['score']:.0f}%({t['strength']['grade'][:2]})" if t['strength']['score'] else '--'
        price_pos = f"{t['price_position']['pct']:.0f}%({t['price_position']['grade'][:2]})" if t['price_position']['pct'] else '--'
        vol_exp = data['volatility']['expansion_grade'][:4] if data['volatility']['expansion_grade'] else '--'
        struct_raw = data['structure']['swing_structure']
        if "HH+HL" in struct_raw:
            struct = "上升结构"
        elif "LH+LL" in struct_raw:
            struct = "下降结构"
        elif "杂乱" in struct_raw:
            struct = "震荡"
        else:
            struct = struct_raw[:8]
        comment = ""
        if period_name == "15m":
            if t['short']['dir'] == "上涨" and t['price_position']['pct']>70:
                comment = "短多但高位"
            elif t['short']['dir'] == "下跌" and t['price_position']['pct']<30:
                comment = "短空超卖"
            else:
                comment = "方向不明"
        elif period_name == "1h":
            if t['mid']['dir'] == "上涨" and t['strength']['score']>50:
                comment = "中周期偏多"
            elif t['mid']['dir'] == "下跌" and t['strength']['score']>50:
                comment = "中周期偏空"
            else:
                comment = "中周期震荡"
        elif period_name == "4h":
            if t['long']['dir'] == "下跌" and t['price_position']['pct']<30:
                comment = "长线空头"
            elif t['long']['dir'] == "上涨" and t['price_position']['pct']>70:
                comment = "长线多头"
            else:
                comment = "长线观望"
        else:
            if t['long']['dir'] == "下跌":
                comment = "趋势向下"
            elif t['long']['dir'] == "上涨":
                comment = "趋势向上"
            else:
                comment = "趋势不明"
        lines.append(f"{period_name:<6} {trend_str:<22} {strength:<10} {price_pos:<10} {vol_exp:<10} {struct:<12} {comment}")
    lines.append("-"*110)

    # ----- 各周期详细分析（不含原短期建议） -----
    for period_name, data in period_results.items():
        if data is None:
            continue
        cfg = period_configs.get(period_name, {})
        lines.append(f"\n{'='*80}")
        lines.append(f"【🔍 {period_name}周期详细分析】")
        lines.append('='*80)
        t = data['trend']
        short_time = _get_time_desc(period_name, cfg.get('short', 0))
        mid_time = _get_time_desc(period_name, cfg.get('mid', 0))
        long_time = _get_time_desc(period_name, cfg.get('long', 0))
        lines.append(f"\n📌 趋势方向:")
        lines.append(f"   {short_time}: {t['short']['dir']} (斜率 {t['short']['slope']:.2f})")
        lines.append(f"   {mid_time}: {t['mid']['dir']} (斜率 {t['mid']['slope']:.2f})")
        lines.append(f"   {long_time}: {t['long']['dir']} (斜率 {t['long']['slope']:.2f})")
        lines.append(f"   → 综合: {short_time}/{mid_time}/{long_time} 均{'、'.join([t['short']['dir'], t['mid']['dir'], t['long']['dir']])}")
        lines.append(f"\n💪 趋势强度: {t['strength']['score']:.1f}% ({t['strength']['grade']})")
        if t['strength']['score'] > 70:
            lines.append(f"   🔥 解读: 趋势强劲，顺势交易胜率高。")
        elif t['strength']['score'] < 30:
            lines.append(f"   ❄️ 解读: 趋势极弱，宜观望或区间操作。")
        else:
            lines.append(f"   🌀 解读: 趋势一般，等待方向明朗。")
        lines.append(f"\n📏 价格位置: {t['price_position']['pct']:.1f}% ({t['price_position']['grade']})")
        if t['price_position']['pct'] > 80:
            lines.append(f"   ⚠️ 解读: 价格处于近期高位，追高风险大。")
        elif t['price_position']['pct'] < 20:
            lines.append(f"   ✅ 解读: 价格处于近期低位，可能存在支撑。")
        else:
            lines.append(f"   ➖ 解读: 价格处于中间区域，无极端信号。")
        v = data['volatility']
        lines.append(f"\n🌊 波动特征:")
        lines.append(f"   ATR: {v['atr']:.2f} (过去平均 {v['atr_avg']:.2f})")
        lines.append(f"   波动扩张率: {v['expansion_rate']:.1f}% ({v['expansion_grade']})")
        if v['expansion_rate'] > 20:
            lines.append(f"   🔔 解读: 波动放大，行情可能加速。")
        elif v['expansion_rate'] < -20:
            lines.append(f"   😴 解读: 波动收缩，市场低迷，等待变盘。")
        else:
            lines.append(f"   🔄 解读: 波动正常，维持原有节奏。")
        lines.append(f"   布林带宽度: {v['bollinger_bandwidth']:.2f}% ({v['bollinger_grade']})")
        if "压缩" in v['bollinger_grade']:
            lines.append(f"   🎯 解读: 价格横盘蓄力，即将选择方向。")
        lines.append(f"   振幅比: {v['amplitude_ratio']:.2f}倍 ({v['amplitude_grade']})")
        if data['orderflow']['delta'] is not None:
            of = data['orderflow']
            lines.append(f"\n💸 订单流特征:")
            lines.append(f"   Volume Delta: {of['delta']:.0f} ({'主动买强' if of['delta']>0 else '主动卖强' if of['delta']<0 else '平衡'})")
            lines.append(f"   Delta强度: {of['delta_strength']:.1f}% ({of['delta_strength_grade']})")
            lines.append(f"   Delta趋势: {of['delta_trend']}")
            lines.append(f"   背离: {'有' if of['divergence'] else '无'}")
            if of['divergence']:
                lines.append(f"   ⚠️ 解读: 价格与主动买卖盘背离，警惕反转。")
            lines.append(f"   主动买入比例: {of['active_buy_ratio']:.1f}% ({of['active_buy_grade']})")
            if of['avg_trade_size']:
                lines.append(f"   平均单笔成交: {of['avg_trade_size']:.3f} ({of['avg_trade_grade']})")
        vol = data['volume']
        lines.append(f"\n📊 成交量特征:")
        lines.append(f"   相对成交量: {vol['relative_ratio']:.2f}倍 ({vol['relative_grade']})")
        if vol['relative_ratio'] > 1.5:
            lines.append(f"   🔥 解读: 明显放量，资金活跃。")
        elif vol['relative_ratio'] < 0.5:
            lines.append(f"   💤 解读: 缩量，市场冷清。")
        lines.append(f"   成交量趋势: {vol['trend']}")
        lines.append(f"   极值: {'是' if vol['extreme'] else '否'}  聚类: {'有' if vol['cluster'] else '无'}")
        s = data['structure']
        swing_desc = s['swing_structure']
        if "HH+HL" in swing_desc:
            swing_desc = "高点抬高且低点抬高（上升结构）"
        elif "LH+LL" in swing_desc:
            swing_desc = "高点降低且低点降低（下降结构）"
        elif "杂乱" in swing_desc:
            swing_desc = "无明显结构（震荡）"
        lines.append(f"\n🏗️ 结构特征:")
        lines.append(f"   高低点结构: {swing_desc}")
        break_dir = s['breakout_direction']
        if break_dir == "向上突破":
            break_desc = f"向上突破（{s['breakout_effectiveness']}）"
        elif break_dir == "向下突破":
            break_desc = f"向下突破（{s['breakout_effectiveness']}）"
        else:
            break_desc = "无"
        lines.append(f"   突破: {break_desc}")
        lines.append(f"   短期阻力/支撑: {s['short_resistance']:.2f} / {s['short_support']:.2f}")
        if data['micro']['body_ratio'] is not None:
            m = data['micro']
            lines.append(f"\n🕯️ 微观形态:")
            lines.append(f"   实体比例: {m['body_ratio']:.1f}% ({m['body_grade']})")
            lines.append(f"   上影线: {m['upper_wick']:.1f}% ({m['upper_wick_grade']})")
            lines.append(f"   下影线: {m['lower_wick']:.1f}% ({m['lower_wick_grade']})")
            lines.append(f"   K线形态: {m['pattern']}")
            if "吞没" in m['pattern']:
                lines.append(f"   🚨 解读: 出现经典反转形态，注意趋势可能变化。")
        e = data['energy']
        lines.append(f"\n⚡ 能量效率:")
        lines.append(f"   动量10: {e['momentum10']:.2f}  加速度: {e['momentum_acceleration']:.2f}({e['momentum_acc_grade']})")
        lines.append(f"   VWAP位置: {e['vwap_deviation']:.2f}倍ATR ({e['vwap_grade']})")
        lines.append(f"   量价配合: {e['volume_price_coordination']}")
        lines.append(f"   能量衰竭: {'有' if e['energy_exhaustion'] else '无'}")
        if e['energy_exhaustion']:
            lines.append(f"   ⚠️ 解读: 价格创新高但动能衰减，警惕回调。")

    # ----- 微观结构与资金博弈分析（新增）-----
    micro_analysis = generate_micro_analysis(symbol, funding, oi, ls_ratio, price_change_24h)
    lines.append(micro_analysis)

    # ----- 综合分析与短期策略参考（替代原K线短期建议）-----
    strategy = generate_comprehensive_strategy(symbol, period_results, funding, oi, ls_ratio, price_change_1h, delta_15m, vol_15m, micro_15m)
    lines.append(strategy)

    return "\n".join(lines)

# ==================== 单个币种完整分析（新） ====================
def analyze_single_symbol(symbol):
    """生成单个币种的完整市场分析报告（文本）"""
    # 1. K线周期分析
    period_results = {}
    for period_cfg in PERIODS:
        klines = fetch_klines_sync(symbol, period_cfg["interval"], period_cfg["min_klines"])
        if klines is None:
            period_results[period_cfg["name"]] = None
        else:
            period_results[period_cfg["name"]] = analyze_period(klines, period_cfg)
    # 2. 总体价格特征
    overall = calc_overall_price_features_sync(symbol)
    # 3. 资金费率分析
    funding = analyze_funding(symbol)
    # 4. OI分析
    oi = analyze_oi(symbol)
    # 5. 大户多空比分析
    ls_ratio = analyze_ls_ratio(symbol)
    # 6. 获取24h价格变化（用于OI-价格关系）
    ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
    price_change_24h = float(ticker.get("priceChangePercent", 0)) if ticker else 0
    # 7. 获取1h价格变化（用于短期建议）
    df_1h = fetch_klines_sync(symbol, "1h", 2)
    if df_1h and len(df_1h) >= 2:
        price_change_1h = (df_1h[-1]['close'] - df_1h[-2]['close']) / df_1h[-2]['close'] * 100
    else:
        price_change_1h = 0
    # 8. 提取15m的Delta和波动率用于综合策略
    data_15m = period_results.get("15m")
    delta_15m = data_15m['orderflow']['delta'] if data_15m and data_15m['orderflow']['delta'] is not None else 0
    vol_15m = data_15m['volatility'] if data_15m else None
    micro_15m = data_15m['micro'] if data_15m else None

    return generate_full_report(symbol, period_results, overall, funding, oi, ls_ratio, price_change_24h, price_change_1h, delta_15m, vol_15m, micro_15m)

# ======================== 保留原有的选币逻辑和对外接口 ========================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
MAIN_STREAM_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "BNBUSDT",
    "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "TRXUSDT"
}

def fetch_url(url, timeout=5):
    try:
        time.sleep(random.uniform(0.3, 0.5))
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except:
        return None

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

def run_topic(target_symbol=None):
    """
    自动模式：随机选币生成深度报告
    手动模式：指定交易对生成深度报告
    返回格式：{"symbol": str, "text": str, "change": float, "volume_ratio": float, "news": str}
    """
    # 手动模式
    if target_symbol and target_symbol.strip():
        topic_text = analyze_single_symbol(target_symbol)
        if not topic_text:
            return {"symbol": target_symbol, "text": "获取行情失败", "change": 0, "volume_ratio": 1.0, "news": ""}
        return {
            "symbol": target_symbol,
            "text": topic_text,
            "change": 0,
            "volume_ratio": random.uniform(0.5, 2.0),
            "news": ""
        }

    # 自动模式：原选币逻辑
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

    # 内存过滤：限制同一币种每天发文次数
    memory_list = load_json(HISTORY_FILE)
    memory = {m["symbol"]: m for m in memory_list}
    memory = clean_expired_memory(memory)
    rec = memory.get(symbol, {"symbol": symbol, "count_24h": 0})
    if rec.get("count_24h", 0) >= MAX_PER_SYMBOL_24H:
        for _ in range(10):
            symbol = random.choice([d["symbol"] for d in top20])
            rec = memory.get(symbol, {"symbol": symbol, "count_24h": 0})
            if rec.get("count_24h", 0) < MAX_PER_SYMBOL_24H:
                break
    last_time = rec.get("last_time")
    if last_time:
        delta = (now() - parse_time(last_time)).total_seconds() / 60
        if delta < COOLDOWN_MINUTES:
            return None
    rec["last_time"] = now().isoformat()
    rec["count_24h"] = rec.get("count_24h", 0) + 1
    memory[symbol] = rec
    save_json(HISTORY_FILE, list(memory.values()))

    topic_text = analyze_single_symbol(symbol)
    if not topic_text:
        return None

    print("\n" + "="*50)
    print(f"✅ 选中交易对：{symbol}")
    print(topic_text)
    print("="*50 + "\n")

    save_json(OUTPUT_FILE, [{
        "symbol": symbol,
        "time": now().isoformat(),
        "text": topic_text,
        "score": random.uniform(0, 100)
    }])

    return {
        "symbol": symbol,
        "text": topic_text,
        "change": float(selected_item["priceChangePercent"]),
        "volume_ratio": random.uniform(0.5, 2.0),
        "news": ""
    }

def get_single_symbol_topic(symbol):
    topic_text = analyze_single_symbol(symbol)
    if not topic_text:
        return {"text": "获取失败"}
    return {
        "symbol": symbol,
        "text": topic_text,
        "change": 0,
        "volume_ratio": 1.0,
        "news": ""
    }

if __name__ == "__main__":
    run_topic()
