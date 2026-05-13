# post_main.py
from ai_core import create_ai_client

class PostService:
    def __init__(self, model_config):
        """
        初始化发文服务
        :param model_config: 模型配置字典 {
            "model_type": "zhipu"/"deepseek",
            "api_key": "你的API Key"
        }
        """
        self.model_type = model_config.get("model_type")
        self.api_key = model_config.get("api_key")
        # 自动创建对应模型的AI客户端
        self.ai_client = create_ai_client(self.model_type, self.api_key)

    def generate_post_content(self, topic, requirement="生成一篇优质原创发文"):
        """
        生成帖子内容（自动使用配置的模型）
        """
        prompt = f"请围绕主题【{topic}】，{requirement}，生成流畅、正式的发文内容"
        return self.ai_client.generate_content(prompt)

    def publish_post(self, content, platform="默认平台"):
        """
        执行发文（手动/自动通用）
        """
        try:
            # 这里是你原有的发文逻辑
            print(f"[{self.model_type} 模型] 正在 {platform} 发文：{content[:30]}...")
            return {"status": "success", "msg": "发文成功"}
        except Exception as e:
            return {"status": "fail", "msg": f"发文失败：{str(e)}"}