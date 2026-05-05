import requests
import time

def clean_key(key):
    """清理API Key，去除首尾空格"""
    return key.strip()

def call_llm(prompt, api_key, retry=3):
    """
    调用智谱GLM-4-Flash模型
    :param prompt: 提示词
    :param api_key: 智谱API Key
    :param retry: 重试次数
    :return: 模型返回的内容（失败返回空字符串）
    """
    for i in range(retry):
        try:
            r = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": f"Bearer {clean_key(api_key)}"},
                json={"model": "glm-4-flash", "messages": [{"role": "user", "content": prompt}]},
                timeout=10
            )
            r.raise_for_status()  # 触发HTTP错误异常
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            print(f"LLM调用失败（第{i+1}次重试）：{str(e)}")
            time.sleep(1)
    return ""

def generate_content(topic, api_key, custom_prompt=None):
    """
    生成发文内容（新增自定义提示词支持）
    :param topic: 话题字典（必须包含text字段）
    :param api_key: 智谱API Key
    :param custom_prompt: 自定义提示词（优先使用，可选）
    :return: 生成的内容, 原始响应（保持原有返回格式，原始响应固定为None）
    """
    # 1. 定义你的原始默认提示词（完全保留你的原有逻辑）
    default_prompt = "分析行情：{text}，用简短口语化发文，不带AI腔，带交易对标签。"
    
    # 2. 优先使用自定义提示词，为空则用默认提示词
    if custom_prompt and custom_prompt.strip():
        # 自定义提示词模式：直接使用用户配置的提示词 + 行情文本
        prompt = f"{custom_prompt.strip()}\n\n{topic.get('text','')}"
    else:
        # 默认模式：保留你原来的提示词格式
        prompt = default_prompt.format(text=topic.get('text',''))
    
    # 3. 调用LLM生成内容（完全复用你的call_llm函数）
    content = call_llm(prompt, api_key)
    
    # 4. 保持原有返回格式（content + None），确保和app.py兼容
    return content, None

# 测试用例（可选，方便验证）
if __name__ == "__main__":
    # 模拟测试数据
    test_topic = {
        "text": "BTCUSDT 价格:80826.80 24h涨跌幅:0.734%",
        "symbol": "BTCUSDT"
    }
    test_api_key = "你的智谱API Key"  # 替换为真实Key
    
    # 测试1：使用默认提示词（和你原有逻辑一致）
    content1, _ = generate_content(test_topic, test_api_key)
    print("【默认提示词生成结果】")
    print(content1)
    print("\n" + "-"*50 + "\n")
    
    # 测试2：使用自定义提示词（账号专属配置）
    custom_prompt = "你是幽默风格的币圈博主，用网络热词分析行情，结尾加#加密货币#标签，字数控制在80字内。"
    content2, _ = generate_content(test_topic, test_api_key, custom_prompt)
    print("【自定义提示词生成结果】")
    print(content2)
