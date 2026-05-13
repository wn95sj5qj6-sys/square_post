# config.py
# 智谱 & DeepSeek 模型接口配置
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# 模型名称
ZHIPU_MODEL_NAME = "glm-4"
DEEPSEEK_MODEL_NAME = "deepseek-chat"

def get_headers(api_key: str, model_type: str):
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    return headers