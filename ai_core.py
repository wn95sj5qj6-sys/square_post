# ai_core.py
import requests
import json
from config import (
    ZHIPU_API_URL,
    DEEPSEEK_API_URL,
    ZHIPU_MODEL_NAME,
    DEEPSEEK_MODEL_NAME,
    get_headers
)

class AICore:
    def __init__(self, model_type: str, api_key: str):
        """
        :param model_type: zhipu 或 deepseek
        :param api_key: 对应模型的API Key
        """
        self.model_type = model_type.strip().lower()
        self.api_key = api_key.strip()
        self.headers = get_headers(self.api_key, self.model_type)

    def generate(self, prompt: str, temperature=0.7, max_tokens=2048):
        try:
            # 选择模型 & 接口
            if self.model_type == "deepseek":
                url = DEEPSEEK_API_URL
                model = DEEPSEEK_MODEL_NAME
            else:
                url = ZHIPU_API_URL
                model = ZHIPU_MODEL_NAME

            # 请求体（双模型兼容）
            data = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False
            }

            response = requests.post(
                url=url,
                headers=self.headers,
                data=json.dumps(data),
                timeout=60
            )

            if response.status_code != 200:
                return f"【{self.model_type}】API调用失败：{response.status_code} {response.text}"

            result = response.json()
            return result["choices"][0]["message"]["content"].strip()

        except Exception as e:
            return f"【{self.model_type}】生成异常：{str(e)}"

# 给业务层调用的快捷函数
def get_ai_client(model_type: str, api_key: str):
    return AICore(model_type, api_key)