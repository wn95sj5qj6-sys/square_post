# ai_core.py
import requests
import json
from config import ZHIPU_API_URL, DEEPSEEK_API_URL, get_headers

class AIClient:
    def __init__(self, model_type, api_key):
        """
        初始化AI客户端
        :param model_type: 模型类型 zhipu / deepseek
        :param api_key: 对应模型的API密钥
        """
        self.model_type = model_type
        self.api_key = api_key
        self.api_url = DEEPSEEK_API_URL if model_type == "deepseek" else ZHIPU_API_URL
        self.headers = get_headers(api_key, model_type)

    def generate_content(self, prompt, temperature=0.7, max_tokens=2048):
        """
        统一调用接口生成文案（双模型通用）
        :param prompt: 提示词
        :return: 生成的文本内容
        """
        try:
            # 请求体适配双模型
            model_name = "deepseek-chat" if self.model_type == "deepseek" else "glm-4"
            data = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False
            }

            response = requests.post(
                url=self.api_url,
                headers=self.headers,
                data=json.dumps(data),
                timeout=60
            )

            if response.status_code != 200:
                return f"接口调用失败：{response.status_code} - {response.text}"

            result = response.json()
            # 统一解析返回结果
            return result["choices"][0]["message"]["content"].strip()

        except Exception as e:
            return f"AI生成失败：{str(e)}"

# 快捷调用函数（给业务层使用）
def create_ai_client(model_type, api_key):
    return AIClient(model_type, api_key)