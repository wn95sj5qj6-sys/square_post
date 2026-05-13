import requests
import json
import time

def call_ai(model_type, api_key, prompt, retry=3):
    if not api_key:
        return "错误：请先配置模型API Key"
    
    if model_type == "zhipu":
        url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        model = "glm-4.7-flash"
    else:
        url = "https://api.deepseek.com/chat/completions"
        model = "deepseek-v4-flash"

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
            r = requests.post(url, headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"AI调用失败，重试 {i+1}/{retry}：{e}")
            time.sleep(2)
    return "错误：AI调用频繁或API Key错误，请稍后再试"

def generate_post_content(topic_text, model_type, api_key, custom_prompt=None):
    default_prompt = """
你是币安广场专业行情分析师，请基于以下行情数据，生成一篇自然、专业、适合币安广场发布的短文。
要求：
1. 口语化，流畅，有交易员风格
2. 突出关键数据
3. 带简单观点，不绝对
4. 必须带上 #交易对 标签
5. 控制在200字以内
行情数据：
"""
    if custom_prompt and custom_prompt.strip():
        prompt = f"{custom_prompt}\n\n{topic_text}"
    else:
        prompt = f"{default_prompt}\n{topic_text}"
    return call_ai(model_type, api_key, prompt)
