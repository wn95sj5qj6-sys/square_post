# config.py
# 全局配置文件，无需手动修改，配置统一在 app.py 管理

# 模型接口地址
ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"

# 请求头模板
def get_headers(api_key, model_type):
    if model_type == "deepseek":
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }