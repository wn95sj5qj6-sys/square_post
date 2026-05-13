# ai_core.py
import requests
import json
from config import ZHIPU_API_URL, DEEPSEEK_API_URL, get_headers

def ai_generate(model_type, model_name, api_key, prompt, temperature=0.7, max_tokens=2048):
    try:
        if model_type == "deepseek":
            url = DEEPSEEK_API_URL
        else:
            url = ZHIPU_API_URL

        headers = get_headers(api_key)
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False
        }

        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        if resp.status_code != 200:
            return f"接口请求失败：{resp.status_code} {resp.text}"
        
        res_json = resp.json()
        return res_json["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"AI生成异常：{str(e)}"