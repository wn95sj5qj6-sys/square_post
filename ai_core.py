import requests
import time

def clean_key(key):
    return key.strip()

def call_llm(prompt, api_key, retry=3):
    for i in range(retry):
        try:
            r = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": f"Bearer {clean_key(api_key)}"},
                json={"model": "glm-4-flash", "messages": [{"role": "user", "content": prompt}]},
                timeout=10
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            time.sleep(1)
    return ""

def generate_content(topic, api_key):
    prompt = f"分析行情：{topic.get('text','')}，用简短口语化发文，不带AI腔，带交易对标签。"
    content = call_llm(prompt, api_key)
    return content, None
