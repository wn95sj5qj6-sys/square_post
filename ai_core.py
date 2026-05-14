import requests
import time
import config

def clean_key(key):
    return key.strip()

def call_zhipu(prompt,api_key,retry=3):
    for i in range(retry):
        try:
            r=requests.post("https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization":f"Bearer {clean_key(api_key)}"},
                json={"model":"glm-4-flash","messages":[{"role":"user","content":prompt}]},timeout=15)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print("智谱失败",e)
            time.sleep(1)
    return ""

def call_deepseek(prompt,api_key,retry=3):
    for i in range(retry):
        try:
            r=requests.post("https://api.deepseek.com/chat/completions",
                headers={"Authorization":f"Bearer {clean_key(api_key)}","Content-Type":"application/json"},
                json={"model":"deepseek-v4-flash","messages":[{"role":"user","content":prompt}],"stream":False},timeout=15)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print("DeepSeek失败",e)
            time.sleep(1)
    return ""

def generate_content(topic,api_key,model_type="zhipu",custom_prompt=None):
    text=topic.get("text","")
    default_prompt="分析行情：{text}，用简短口语化发文，不带AI腔，带交易对标签。"
    if custom_prompt and custom_prompt.strip():
        prompt=f"{custom_prompt}\n\n{text}"
    else:
        prompt=default_prompt.format(text=text)
    if model_type=="deepseek":
        return call_deepseek(prompt,config.DEEPSEEK_API_KEY),None
    else:
        return call_zhipu(prompt,config.ZHIPU_API_KEY),None
