# post_main.py 开头修改示例
from ai_core import ai_generate

class PostManager:
    def __init__(self, sys_config):
        self.cfg = sys_config

    def generate_post(self, topic, requirement=""):
        prompt = f"围绕主题：{topic}，{requirement}，生成一篇适合广场发布的优质文案"
        # 根据当前配置自动选模型、密钥、温度
        if self.cfg["current_model_type"] == "deepseek":
            key = self.cfg["deepseek_api_key"]
        else:
            key = self.cfg["zhipu_api_key"]
        
        return ai_generate(
            model_type=self.cfg["current_model_type"],
            model_name=self.cfg["current_model_name"],
            api_key=key,
            prompt=prompt,
            temperature=self.cfg["temperature"]
        )

    # 下面你原有 publish 等函数完全不动