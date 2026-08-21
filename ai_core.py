import requests
import time

def clean_key(key):
    return key.strip() if key else ""

def call_zhipu(prompt, api_key, retry=3):
    if not api_key:
        return ""
    for i in range(retry):
        try:
            r = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": f"Bearer {clean_key(api_key)}"},
                json={"model": "glm-4-flash", "messages": [{"role": "user", "content": prompt}]},
                timeout=30
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print("智谱生成异常:", e)
            time.sleep(2)
    return ""

def call_deepseek(prompt, api_key, retry=3):
    if not api_key:
        return ""
    for i in range(retry):
        try:
            r = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {clean_key(api_key)}", "Content-Type": "application/json"},
                json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "stream": False},
                timeout=30
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print("DeepSeek生成异常:", e)
            time.sleep(2)
    return ""

def generate_content(topic, api_key, model_type="zhipu", custom_prompt=None):
    text = topic.get("text", "") if isinstance(topic, dict) else str(topic)
    default_prompt = "分析行情：{text}，用简短口语化发文，不带AI腔，带交易对标签。"
    if custom_prompt and custom_prompt.strip():
        prompt = f"{custom_prompt}\n\n{text}"
    else:
        prompt = default_prompt.format(text=text)
    
    if model_type == "deepseek":
        return call_deepseek(prompt, api_key), None
    else:
        return call_zhipu(prompt, api_key), None
