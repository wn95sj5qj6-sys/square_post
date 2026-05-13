import requests
import json
from config import ZHIPU_API_URL, DEEPSEEK_API_URL, ZHIPU_MODEL, DEEPSEEK_MODEL

def call_ai(model_type, api_key, prompt, retry=3):
    if not api_key:
        return "错误：请先在账号配置中填写模型API Key"
    if model_type == "zhipu":
        url = ZHIPU_API_URL
        model = ZHIPU_MODEL
    else:
        url = DEEPSEEK_API_URL
        model = DEEPSEEK_MODEL

    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 2048,
        "stream": False
    }

    for i in range(retry):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"AI调用失败（{model_type}）：{e}")
    return "错误：AI生成失败，请检查API Key或网络"

def generate_post_content(topic_text, model_type, api_key, custom_prompt=None):
    """生成最终发文内容，适配手动/自动模式"""
    default_prompt = """
    你是币安广场的专业行情分析师，请基于以下交易对行情，生成一篇适合广场发布的优质短文：
    1. 语言要口语化，不要生硬，带一点交易员的感觉
    2. 突出关键数据：价格、涨跌幅、振幅
    3. 结尾可以带一句简短的市场看法，不要过于肯定
    4. 必须带上交易对标签，比如 $BTCUSDT
    行情数据：
    """
    if custom_prompt and custom_prompt.strip():
        prompt = f"{custom_prompt}\n\n{topic_text}"
    else:
        prompt = f"{default_prompt}\n{topic_text}"
    return call_ai(model_type, api_key, prompt)
