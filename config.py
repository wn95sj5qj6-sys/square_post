# config.py
# 接口固定地址，不存密钥
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# 可选模型列表
ZHIPU_MODEL_LIST = ["glm-4", "glm-4-flash", "glm-3-turbo"]
DEEPSEEK_MODEL_LIST = ["deepseek-chat", "deepseek-coder"]

def get_headers(api_key: str):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }