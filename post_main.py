# post_main.py
from ai_core import ai_generate

class PostManager:
    def __init__(self, sys_config):
        # 接收网页全局配置，不写死任何key和模型
        self.cfg = sys_config

    def generate_post(self, topic, requirement=""):
        """
        根据主题+要求AI生成发帖内容
        自动读取网页配置：模型类型、模型版本、API密钥、创作温度
        """
        if not topic:
            return "主题不能为空，请输入发帖主题"

        # 拼接提示词
        prompt = f"""
请围绕主题：{topic}
额外写作要求：{requirement}
生成一篇适合社交广场发布的原创优质短文，文案自然流畅、不生硬、适合公域分发。
        """.strip()

        # 自动匹配对应模型的API Key
        if self.cfg["current_model_type"] == "deepseek":
            api_key = self.cfg.get("deepseek_api_key", "")
        else:
            api_key = self.cfg.get("zhipu_api_key", "")

        if not api_key:
            return "错误：请先在网页配置面板填写对应模型的API Key并保存"

        # 调用统一AI接口
        content = ai_generate(
            model_type=self.cfg["current_model_type"],
            model_name=self.cfg["current_model_name"],
            api_key=api_key,
            prompt=prompt,
            temperature=self.cfg["temperature"]
        )
        return content

    def publish(self, content):
        """
        执行发文（完全保留你原有发文结构，可自行拓展实际发布逻辑）
        """
        if not content:
            return {"code": 400, "msg": "发文内容不能为空"}
        
        # ========== 这里保留你原来的账号发帖、提交接口、风控逻辑等 ==========
        # 你原有怎么发文，直接原样写在这里即可，不用改动上层
        return {
            "code": 200,
            "msg": "发文成功",
            "data": {
                "model_used": self.cfg["current_model_type"],
                "content": content[:50] + "..."
            }
        }