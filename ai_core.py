import requests
import json
from config import ZHIPU_API_URL, DEEPSEEK_API_URL, ZHIPU_MODEL, DEEPSEEK_MODEL

def clean_key(key):
    return key.strip()

def call_ai(model_type, api_key, prompt, retry=3):
    """
    model_type: zhipu / deepseek
    api_key: 网页传入（内存）
    """
    if model_type == "zhipu":
        url = ZHIPU_API_URL
        model = ZHIPU_MODEL
    else:
        url = DEEPSEEK_API_URL
        model = DEEPSEEK_MODEL

    headers = {
        "Authorization": f"Bearer {clean_key(api_key)}",
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
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"AI失败（{model_type}）：{e}")
    return ""

def generate_content(topic, api_key, model_type, custom_prompt=None):
    """
    topic: {text, symbol}
    api_key: 网页传入
    model_type: zhipu/deepseek
    """
    default_prompt = "分析行情：{text}，用简短口语化发文，不带AI腔，带交易对标签。"
    if custom_prompt and custom_prompt.strip():
        prompt = f"{custom_prompt}\n\n{topic.get('text','')}"
    else:
        prompt = default_prompt.format(text=topic.get('text',''))

    content = call_ai(model_type, api_key, prompt)
    return content, None
