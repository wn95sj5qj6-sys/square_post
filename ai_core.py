import os, json, time, requests, random
from datetime import datetime, UTC, timedelta
from utils import now
from config import EXPIRE_HOURS

BASE_DIR = "data"
CONTENT_DIR = os.path.join(BASE_DIR, "ai_content")
LOG_DIR = os.path.join(BASE_DIR, "ai_logs")
PROMPT_DIR = os.path.join(BASE_DIR, "ai_prompts")
os.makedirs(CONTENT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(PROMPT_DIR, exist_ok=True)

# ====================== 工具 ======================
def clean_api_key(key):
    return str(key).strip().replace(" ","").replace("\n","")

def call_llm(prompt, api_key, retry=3):
    """修复：增加重试机制"""
    api_key = clean_api_key(api_key)
    for i in range(retry):
        try:
            r = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=8
            )
            r.raise_for_status()
            res = r.json()
            content = res["choices"][0]["message"]["content"].strip()
            return content if content else ""
        except Exception as e:
            print(f"LLM调用失败（第{i+1}次）：{str(e)}")
            time.sleep(1)
    return ""

# ====================== 清理机制 ======================
def clean_old_files(directory):
    now_ts = datetime.now(UTC)
    for file in os.listdir(directory):
        path = os.path.join(directory, file)
        try:
            t = datetime.fromtimestamp(os.path.getmtime(path), UTC)
            if (now_ts - t).total_seconds() > EXPIRE_HOURS * 3600:
                os.remove(path)
        except:
            continue

# ====================== 语义库（真人交易员风格） ======================
PERSONAS = [
    "你是币圈3年老交易员，经历牛熊，说话带实战感。",
    "你是短线合约交易者，富有激情、聊盘口、仓位、止损，语气像肯定能发财的人。",
    "你是懂市场情绪的老玩家，能够仍受被套，但有判断，对行情的涨跌有明确倾向。",
    "你是老散户韭菜，喜欢骂庄家（狗庄），情绪化，坚定自己的扛单行为，但也无奈。",
]

STYLES = [
    "用第一视角感受切入，真实不生硬。",
    "抓市场情绪痛点，引发共鸣。",
    "用反直觉视角，让人愿意看下去。",
    "轻分析重行为，给用户明确行为思路。",
]

STRUCTURES = [
    "先结论→再解释→给行为边界。",
    "先现象→再拆解→给操作倾向。",
    "先感受→再判断→留风险提示。",
]

GOALS = [
    "引发讨论，建立信任，艺术的引导观众交易",
    "引导用户按判断执行交易，但不直接喊单，观点要鲜明",
    "强化个人判断，有理有据，让用户跟随",
]

OPENING_LINES = [
    "这波有点不太对劲。",
    "很多人忽略了一个关键点。",
    "如果只看表面，很容易误判。",
    "盘面已经给出信号了。",
]

ENDING_LINES = [
    "懂的都懂。",
    "自行判断，不构成建议。",
    "风险自己把控。",
    "别盲目跟风。",
]

# 🔥 核心新增：情绪钩子（抓恐惧/贪婪）
EMOTION_HOOKS = {
    "上涨(强)": ["踏空比亏损更难受", "这波不吃一口等于白玩"],
    "上涨(弱)": ["别等大涨才后悔", "机会就在犹豫中错过"],
    "下跌(强)": ["再扛就要爆仓", "别用本金赌反弹"],
    "下跌(弱)": ["现在抄底就是接飞刀", "耐心比勇气重要"],
    "震荡": ["横盘越久波动越大", "别在震荡里把本金玩没"]
}

# 🔥 核心新增：行动引导库（软引导交易，完全合规）
ACTION_PROMPTS = {
    "上涨(强)": [
        "突破关键位，敢上车的轻仓试错。",
        "趋势确认，拿住比频繁操作更重要。",
        "放量走强，回踩就是机会。"
    ],
    "上涨(弱)": [
        "小仓博弈，破位就走。",
        "别追高，等回踩再接。",
        "轻仓玩，不恋战。"
    ],
    "下跌(强)": [
        "风控第一，不抄底。",
        "空头明确，别逆势扛单。",
        "破位就止损，别幻想反弹。"
    ],
    "下跌(弱)": [
        "观望为主，不急着动手。",
        "小反弹不参与，等明确信号。"
    ],
    "震荡": [
        "高抛低吸，破位就撤。",
        "区间内短线，不贪大利润。"
    ]
}

# ====================== 策略（增强版：更贴合真实市场） ======================
def build_strategy(topic):
    change = topic.get("change", 0)
    volume_ratio = topic.get("volume_ratio", 1.0)
    news = topic.get("news", "")

    if change > 8:
        scene = "上涨(强)"
    elif change > 3:
        scene = "上涨(弱)"
    elif change < -8:
        scene = "下跌(强)"
    elif change < -3:
        scene = "下跌(弱)"
    else:
        scene = "震荡"

    # 增强：带量能标签
    if volume_ratio > 1.5:
        scene += "（放量）"
    elif volume_ratio < 0.8:
        scene += "（缩量）"

    # 增强：带消息面
    if "利好" in news:
        scene += "（利好驱动）"
    elif "利空" in news:
        scene += "（利空驱动）"

    # 偏向更真实
    abs_c = abs(change)
    if change > 0:
        bias = "偏多(谨慎)" if abs_c < 5 else "偏多(强势)"
    elif change < 0:
        bias = "偏空(谨慎)" if abs_c < 5 else "偏空(强势)"
    else:
        bias = "观望"

    risk = "高风险" if abs(change) > 10 else "中等风险"

    return {
        "bias": bias,
        "risk": risk,
        "scene": scene.split("（")[0],  # 给行动引导用纯场景
        "scene_full": scene
    }

# ====================== 语义控制（场景绑定，不再纯随机） ======================
def build_semantic_control(strategy):
    scene = strategy["scene"]

    # 大跌 → 风险警告型人设
    if "下跌" in scene:
        persona = random.choice([
            "你是懂市场情绪的老玩家，能够仍受被套，但有判断，对行情的涨跌有明确倾向。",
            "你是老散户韭菜，喜欢骂庄家（狗庄），情绪化，坚定自己的扛单行为，但也无奈。"
        ])
        style = random.choice([
            "强调风险，给明确行为边界。",
            "戳中恐惧，让用户冷静决策。"
        ])
    # 大涨 → 顺势引导型人设
    elif "上涨" in scene:
        persona = random.choice([
            "你是币圈3年老交易员，经历牛熊，说话带实战感。",
            "你是短线合约交易者，富有激情、聊盘口、仓位、止损，语气像肯定能发财的人。"
        ])
        style = random.choice([
            "抓贪婪心理，引导轻仓试错。",
            "讲趋势逻辑，给明确操作倾向。"
        ])
    # 震荡 → 观望策略型人设
    else:
        persona = random.choice(PERSONAS)
        style = random.choice(STYLES)

    return {
        "persona": persona,
        "style": style,
        "structure": random.choice(STRUCTURES),
        "goal": random.choice(GOALS)
    }

# ====================== Prompt（强化引导+去AI+合规） ======================
def build_prompt(topic, strategy, semantic):
    return f"""
【人设】{semantic['persona']}

【现在这个盘面状态】
{strategy['scene_full']}
当前倾向：{strategy['bias']} 

【标的】
{topic['symbol']}
涨跌幅：{topic.get('change',0)}%

【盘面/信息】
{topic['text']}

【严格表达规则】
完全口语化，只说第一反应，不做任何解释、不铺垫逻辑
禁止主语人称：严禁我、咱们、你、这、那、投资者、建议
禁止 AI 话术：综上、因此、指标显示、数据表明、分析来看
只讲动作倾向，不讲完整行情梳理，允许短句、碎句、断句
可带狗庄、散户、砸盘、吸筹、跑路这类盘口黑话
绝对不喊单、不说具体点位、不提具体杠杆倍数
强判断优先，情绪要野，像实盘盯盘的老手脱口而出

【隐性逻辑必须藏在话里】
直接给直觉判断（偏空 / 不对劲 / 要变盘）
带一句盘口 / 量能 / 资金依据
明确动作（观望 / 轻仓试空 / 等破位 / 不接飞刀）

【输出要求】
调取我在质谱ai设置的知识库里面的文字作为语气参考
必须带 必须把${topic['symbol']}合约标签带入
4-7句，分段排版，短句为主
无 AI 腔，无教学感，读完想跟着动手
全程不出现任何指代性代词，不啰嗦风控大道理

"""
# ====================== 后处理（精简纯净版：去AI + 强引导，无虚假修饰） ======================
def post_process(content, strategy):
    if not content:
        return ""

    scene = strategy["scene"]

    # 1. 仅保留：AI套话替换（去AI腔）
    replace_map = {
        "综上所述": "说白了",
        "从数据来看": "盘面上看",
        "由此可见": "明显能看出来",
        "可以得出": "能感觉到",
        "分析表明": "我看下来"
    }
    for old, new in replace_map.items():
        content = content.replace(old, new)

    # 2. 保留：行动引导（核心引导交易，自然不生硬）
    if scene in ACTION_PROMPTS:
        action = random.choice(ACTION_PROMPTS[scene])
        content += " " + action

    # 3. 长度控制 + 清理空格
    return content[:300].strip()

# ====================== 存储（完全保留原结构） ======================
def save_all(topic, strategy, semantic, prompt, content):
    ts = int(time.time())
    content_path = os.path.join(CONTENT_DIR, f"{ts}.json")
    with open(content_path, "w", encoding="utf-8") as f:
        json.dump({
            "topic": topic,
            "strategy": strategy,
            "content": content,
            "time": str(now())
        }, f, ensure_ascii=False, indent=2)

    log_path = os.path.join(LOG_DIR, f"{ts}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({
            "topic": topic,
            "strategy": strategy,
            "semantic_control": semantic,
            "prompt": prompt,
            "content": content,
            "time": str(now())
        }, f, ensure_ascii=False, indent=2)

# ====================== 主函数（接口100%兼容） ======================
def generate_content(topic, api_key):
    clean_old_files(CONTENT_DIR)
    clean_old_files(LOG_DIR)
    clean_old_files(PROMPT_DIR)

    strategy = build_strategy(topic)
    semantic = build_semantic_control(strategy)
    prompt = build_prompt(topic, strategy, semantic)

    # 保存prompt本地用于分析
    ts = int(time.time())
    prompt_file = os.path.join(PROMPT_DIR, f"{ts}.txt")
    with open(prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    raw = call_llm(prompt, api_key)
    content = post_process(raw, strategy)

    save_all(topic, strategy, semantic, prompt, content)

    return content, strategy

# ====================== 兼容旧接口（完全不变） ======================
def save_result(topic, strategy, content):
    save_all(topic, strategy, {}, "", content)
