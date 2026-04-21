import os
import sys
from config import ZHIPU_API_KEY, BINANCE_API_KEYS, EXPIRE_HOURS
from topic_main import run_topic
from ai_core import generate_content, save_result
from utils import load_json, save_json, clean_expired, now

OUTPUT_FILE = os.path.join("data", "outputs.json")

def select_binance_account():
    if not BINANCE_API_KEYS:
        print("❌ 未配置任何币安账号")
        sys.exit(1)

    print("\n========================================")
    print("📱 请选择要发文的币安广场账号")
    print("========================================")
    for i, key in enumerate(BINANCE_API_KEYS):
        show_key = key[:6] + "********" + key[-4:] if len(key) > 10 else key
        print(f"  {i+1}. 账号 {i+1}  |  {show_key}")

    while True:
        try:
            choice = int(input("\n请输入序号：")) - 1
            if 0 <= choice < len(BINANCE_API_KEYS):
                print(f"\n✅ 已选择：账号 {choice + 1}")
                return BINANCE_API_KEYS[choice]
        except:
            pass
        print("❌ 输入无效，请重试")

def main():
    print("🚀 币安广场自动发文系统（多账号版）")
    selected_api_key = select_binance_account()

    topic = run_topic()
    if not topic:
        print("❌ 话题生成失败")
        return

    content, strategy = generate_content(topic, ZHIPU_API_KEY)
    if not content:
        print("❌ AI生成内容失败")
        return

    from post_main import post_with_key
    success = post_with_key(content, selected_api_key)

    if success:
        save_result(topic, strategy, content)
        data = clean_expired(load_json(OUTPUT_FILE), EXPIRE_HOURS)
        data.append({
            "time": now().isoformat(),
            "topic": topic,
            "strategy": strategy,
            "content": content
        })
        save_json(OUTPUT_FILE, data)
        print("\n🎉 全部流程执行完成！")
    else:
        print("\n❌ 发文失败，流程终止")

if __name__ == "__main__":
    main()
