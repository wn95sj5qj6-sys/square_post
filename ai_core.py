import requests
import time
import config

def clean_key(key):
    if not key:
        return ""
    return key.strip()

# ------------------- 智谱模型调用 -------------------
def call_zhipu(prompt, retry=3):
    api_key = config.ZHIPU_API_KEY
    if not api_key:
        print("智谱API Key未配置")
        return ""

    for i in range(retry):
        try:
            resp = requests.post(
                url="https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={
                    "Authorization": f"Bearer {clean_key(api_key)}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=20
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"智谱调用失败 {i+1}: {str(e)}")
            time.sleep(1)
    return ""

# ------------------- DeepSeek 模型调用（新增） -------------------
def call_deepseek(prompt, retry=3):
    api_key = config.DEEPSEEK_API_KEY
    if not api_key:
        print("DeepSeek API Key未配置")
        return ""

    for i in range(retry):
        try:
            resp = requests.post(
                url="https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {clean_key(api_key)}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False
                },
                timeout=20
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"DeepSeek调用失败 {i+1}: {str(e)}")
            time.sleep(1)
    return ""

# ------------------- 统一生成入口 -------------------
def generate_content(topic, model_type="zhipu", custom_prompt=None):
    text = topic.get("text", "")
    if not text:
        return "", None

    # 默认提示词
    default_prompt = f"请根据以下行情内容，生成一条简洁、口语化、无AI感的发文内容：\n{text}"

    # 使用自定义提示词
    if custom_prompt and custom_prompt.strip():
        final_prompt = f"{custom_prompt.strip()}\n\n内容：{text}"
    else:
        final_prompt = default_prompt

    # 模型选择
    if model_type == "deepseek":
        content = call_deepseek(final_prompt)
    else:
        content = call_zhipu(final_prompt)

    return content.strip(), None
