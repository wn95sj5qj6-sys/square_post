import requests
import random

def get_all_symbols():
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        usdt_pairs = [item for item in data if item['symbol'].endswith('USDT')]
        return usdt_pairs
    except Exception as e:
        print(f"获取交易对失败：{e}")
        return []

def filter_volatile_pairs(pairs, min_change=5, min_amplitude=10):
    filtered = []
    for p in pairs:
        try:
            change = float(p['priceChangePercent'])
            high = float(p['highPrice'])
            low = float(p['lowPrice'])
            if low == 0:
                continue
            amplitude = (high - low) / low * 100
            if abs(change) >= min_change and amplitude >= min_amplitude:
                filtered.append(p)
        except:
            continue
    return filtered

def get_random_topic():
    pairs = get_all_symbols()
    if not pairs:
        return None
    filtered = filter_volatile_pairs(pairs)
    if not filtered:
        filtered = pairs
    selected = random.choice(filtered)
    symbol = selected['symbol']
    price = float(selected['lastPrice'])
    change = float(selected['priceChangePercent'])
    high = float(selected['highPrice'])
    low = float(selected['lowPrice'])
    amplitude = (high - low) / low * 100 if low != 0 else 0
    text = f"{symbol}，价格{price:.6f}，24h涨跌幅{change:.2f}%，24h振幅{amplitude:.2f}%（最高{high:.6f}，最低{low:.6f}）"
    return {"symbol": symbol, "text": text}

def get_single_symbol_topic(symbol):
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol.upper()}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        selected = resp.json()
        symbol = selected['symbol']
        price = float(selected['lastPrice'])
        change = float(selected['priceChangePercent'])
        high = float(selected['highPrice'])
        low = float(selected['lowPrice'])
        amplitude = (high - low) / low * 100 if low != 0 else 0
        text = f"{symbol}，价格{price:.6f}，24h涨跌幅{change:.2f}%，24h振幅{amplitude:.2f}%（最高{high:.6f}，最低{low:.6f}）"
        return {"symbol": symbol, "text": text}
    except Exception as e:
        print(f"获取单个交易对失败：{e}")
        return None
