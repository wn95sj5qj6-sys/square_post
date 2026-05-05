from flask import Flask, render_template_string, request, jsonify, Response
import os
import json
import datetime
import threading
import time
import copy

app = Flask(__name__)

# ======================== 核心配置 ========================
# 环境变量配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

# 数据存储路径
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/records.json"
CONFIG_FILE = f"{DATA_DIR}/config.json"
PROMPT_FILE = f"{DATA_DIR}/prompts.json"  # 账号-提示词配置文件
os.makedirs(DATA_DIR, exist_ok=True)

# ======================== 数据持久化工具函数 ========================
def load_json(file_path, default=None):
    if default is None:
        default = {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ======================== 账号管理 ========================
def get_accounts_from_env():
    """从环境变量解析账号列表 BINANCE_ACCOUNTS=账号1|key1,账号2|key2"""
    accounts_env = os.getenv("BINANCE_ACCOUNTS", "").strip()
    accounts = []
    if not accounts_env:
        return accounts
    
    for item in accounts_env.split(","):
        item = item.strip()
        if "|" not in item:
            continue
        name, key = item.split("|", 1)
        name = name.strip()
        key = key.strip()
        if name and key:
            accounts.append({"name": name, "key": key})
    return accounts

def get_all_accounts():
    """获取所有账号（包含提示词配置）"""
    env_accounts = get_accounts_from_env()
    prompts = load_json(PROMPT_FILE)
    
    # 合并账号和提示词配置
    accounts = []
    for acc in env_accounts:
        acc_name = acc["name"]
        # 读取该账号的配置（提示词/日限额）
        acc_config = prompts.get(acc_name, {})
        accounts.append({
            "name": acc_name,
            "key": acc["key"],
            "prompt": acc_config.get("prompt", ""),  # 专属提示词
            "daily_limit": acc_config.get("daily_limit", DEFAULT_DAILY_LIMIT),  # 账号专属日限额
            "auto_interval": acc_config.get("auto_interval", DEFAULT_AUTO_INTERVAL)  # 账号专属间隔
        })
    return accounts

def get_account_by_name(name):
    """根据账号名获取账号完整信息"""
    accounts = get_all_accounts()
    for acc in accounts:
        if acc["name"] == name:
            return acc
    return None

def get_account_by_key(key):
    """根据key获取账号完整信息"""
    accounts = get_all_accounts()
    for acc in accounts:
        if acc["key"] == key:
            return acc
    return None

# ======================== 提示词配置管理 ========================
def save_account_prompt(account_name, prompt, daily_limit, auto_interval):
    """保存账号的提示词和配置"""
    prompts = load_json(PROMPT_FILE)
    prompts[account_name] = {
        "prompt": prompt,
        "daily_limit": int(daily_limit),
        "auto_interval": int(auto_interval)
    }
    save_json(PROMPT_FILE, prompts)

# ======================== 发文记录管理 ========================
def save_post_record(mode, account_name, symbol, content, post_id, status="success"):
    """保存发文记录"""
    record = {
        "mode": mode,  # auto/manual
        "account": account_name,
        "date": str(datetime.date.today()),
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "content": content,
        "post_id": post_id,
        "status": status
    }
    db = load_json(DB_FILE, [])
    db.append(record)
    save_json(DB_FILE, db)

def get_today_stats(account_name=None):
    """获取今日发文统计（按账号）"""
    today = str(datetime.date.today())
    db = load_json(DB_FILE, [])
    
    # 按账号分组统计
    stats = {}
    accounts = get_all_accounts()
    # 初始化所有账号的统计
    for acc in accounts:
        stats[acc["name"]] = {
            "count": 0,
            "limit": acc["daily_limit"],
            "remaining": acc["daily_limit"]
        }
    
    # 统计今日发文数
    for record in db:
        if record.get("date") == today and record.get("status") == "success":
            acc_name = record.get("account", "")
            if acc_name in stats:
                stats[acc_name]["count"] += 1
                stats[acc_name]["remaining"] = stats[acc_name]["limit"] - stats[acc_name]["count"]
    
    # 如果指定账号，只返回该账号统计
    if account_name:
        return stats.get(account_name, {"count": 0, "limit": DEFAULT_DAILY_LIMIT, "remaining": DEFAULT_DAILY_LIMIT})
    
    return stats

# ======================== 自动发文核心逻辑 ========================
def get_auto_config():
    """获取自动运行配置"""
    cfg = load_json(CONFIG_FILE)
    return {
        "auto_running": cfg.get("auto_running", False),
        "current_account": cfg.get("current_account", ""),  # 当前自动发文账号
        "last_run_time": cfg.get("last_run_time", "")
    }

def save_auto_config(data):
    """保存自动运行配置"""
    cfg = load_json(CONFIG_FILE)
    cfg.update(data)
    save_json(CONFIG_FILE, cfg)

def auto_publisher():
    """自动发文后台线程（支持账号专属配置）"""
    while True:
        # 读取自动运行状态
        auto_cfg = get_auto_config()
        if not auto_cfg["auto_running"]:
            time.sleep(3)
            continue
        
        # 获取当前选中的自动发文账号
        current_acc_name = auto_cfg["current_account"]
        if not current_acc_name:
            time.sleep(10)
            continue
        
        # 获取账号完整配置
        current_acc = get_account_by_name(current_acc_name)
        if not current_acc:
            time.sleep(10)
            continue
        
        # 检查今日限额
        today_stats = get_today_stats(current_acc_name)
        if today_stats["count"] >= today_stats["limit"]:
            print(f"账号 {current_acc_name} 今日已达发文限额 {today_stats['limit']}")
            time.sleep(60)
            continue
        
        try:
            # 1. 调用topic_main获取交易对分析
            from topic_main import run_topic
            topic = run_topic()
            if not topic:
                time.sleep(10)
                continue
            
            # 2. 生成AI内容（使用账号专属提示词）
            from ai_core import generate_content
            content, _ = generate_content(topic, ZHIPU_API_KEY, custom_prompt=current_acc["prompt"])
            if not content:
                time.sleep(10)
                continue
            
            # 3. 发布内容
            from post_main import post_content
            ok, msg, post_id = post_content(content, current_acc["key"])
            
            # 4. 保存记录
            if ok:
                save_post_record("auto", current_acc_name, topic.get("symbol", ""), content, post_id)
                print(f"自动发文成功 | 账号：{current_acc_name} | 交易对：{topic.get('symbol', '')}")
            else:
                save_post_record("auto", current_acc_name, topic.get("symbol", ""), content, post_id, "fail")
                print(f"自动发文失败 | 账号：{current_acc_name} | 原因：{msg}")
            
            # 5. 按账号专属间隔休眠
            save_auto_config({"last_run_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            time.sleep(current_acc["auto_interval"] * 60)
            
        except Exception as e:
            print(f"自动发文异常 | 账号：{current_acc_name} | 错误：{str(e)}")
            time.sleep(10)

# 启动自动发文线程
threading.Thread(target=auto_publisher, daemon=True).start()

# ======================== 全新UI模板 ========================
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安自动发文助手</title>
    <style>
        :root {
            --primary: #007aff;
            --success: #34c759;
            --danger: #ff3b30;
            --warning: #ff9500;
            --gray: #8e8e93;
            --light-gray: #f2f2f7;
            --border: #e5e5ea;
            --text: #1d1d1f;
            --bg: #ffffff;
        }
        
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        
        body {
            background-color: var(--light-gray);
            color: var(--text);
            padding: 16px;
            line-height: 1.5;
        }
        
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        
        .card {
            background: var(--bg);
            border-radius: 16px;
            box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
            padding: 24px;
            margin-bottom: 16px;
        }
        
        .header {
            display: flex;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .header h1 {
            font-size: 22px;
            font-weight: 600;
            margin-right: 12px;
        }
        
        .header .badge {
            background: var(--primary);
            color: white;
            font-size: 12px;
            padding: 2px 8px;
            border-radius: 10px;
        }
        
        .tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
        }
        
        .tab-btn {
            background: none;
            border: none;
            padding: 8px 16px;
            font-size: 15px;
            font-weight: 500;
            color: var(--gray);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .tab-btn.active {
            color: var(--primary);
            background-color: rgba(0, 122, 255, 0.1);
        }
        
        .tab-content {
            display: none;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .form-group {
            margin-bottom: 16px;
        }
        
        .form-label {
            display: block;
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 8px;
            color: var(--text);
        }
        
        .form-control {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 12px;
            font-size: 15px;
            transition: border 0.2s;
        }
        
        .form-control:focus {
            outline: none;
            border-color: var(--primary);
        }
        
        textarea.form-control {
            min-height: 120px;
            resize: vertical;
            line-height: 1.5;
        }
        
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 12px 24px;
            border: none;
            border-radius: 12px;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            gap: 8px;
        }
        
        .btn-primary {
            background: var(--primary);
            color: white;
            width: 100%;
        }
        
        .btn-primary:hover {
            background: #0066cc;
        }
        
        .btn-secondary {
            background: var(--light-gray);
            color: var(--text);
            width: 100%;
        }
        
        .btn-secondary:hover {
            background: #e5e5ea;
        }
        
        .btn-success {
            background: var(--success);
            color: white;
        }
        
        .btn-danger {
            background: var(--danger);
            color: white;
        }
        
        .btn-group {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }
        
        .status-card {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 16px;
            border-radius: 12px;
            background: var(--light-gray);
            margin-bottom: 16px;
        }
        
        .status-running {
            color: var(--success);
            font-weight: 500;
        }
        
        .status-stopped {
            color: var(--danger);
            font-weight: 500;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }
        
        .stat-card {
            background: var(--light-gray);
            border-radius: 12px;
            padding: 16px;
            text-align: center;
        }
        
        .stat-value {
            font-size: 24px;
            font-weight: 600;
            margin-bottom: 4px;
        }
        
        .stat-label {
            font-size: 12px;
            color: var(--gray);
        }
        
        .log-box {
            background: var(--light-gray);
            border-radius: 12px;
            padding: 16px;
            min-height: 80px;
            font-size: 14px;
            white-space: pre-wrap;
            margin-top: 16px;
        }
        
        .account-config-card {
            background: rgba(0, 122, 255, 0.05);
            border-left: 4px solid var(--primary);
            padding: 16px;
            border-radius: 0 12px 12px 0;
            margin-bottom: 16px;
        }
        
        .records-list {
            max-height: 400px;
            overflow-y: auto;
            gap: 12px;
            display: flex;
            flex-direction: column;
        }
        
        .record-item {
            background: var(--light-gray);
            border-radius: 12px;
            padding: 16px;
        }
        
        .record-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            font-size: 14px;
        }
        
        .record-symbol {
            font-weight: 600;
            color: var(--primary);
        }
        
        .record-time {
            color: var(--gray);
            font-size: 12px;
        }
        
        .record-content {
            font-size: 14px;
            line-height: 1.5;
        }
        
        @media (max-width: 480px) {
            .card {
                padding: 16px;
            }
            
            .btn-group {
                flex-direction: column;
            }
        }
    </style>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>币安自动发文助手</h1>
                <span class="badge">v2.0</span>
            </div>
            
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('auto')">
                    <i class="fa fa-robot"></i> 自动模式
                </button>
                <button class="tab-btn" onclick="switchTab('manual')">
                    <i class="fa fa-hand-pointer-o"></i> 手动模式
                </button>
                <button class="tab-btn" onclick="switchTab('config')">
                    <i class="fa fa-cog"></i> 账号配置
                </button>
                <button class="tab-btn" onclick="switchTab('records')">
                    <i class="fa fa-history"></i> 发文记录
                </button>
            </div>
            
            <!-- 自动模式 -->
            <div id="auto" class="tab-content active">
                <div class="form-group">
                    <label class="form-label">选择自动发文账号</label>
                    <select id="auto_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.name}}" {% if acc.name == current_auto_acc %}selected{% endif %}>
                            {{acc.name}} (今日限额: {{acc.daily_limit}}条)
                        </option>
                        {% endfor %}
                    </select>
                </div>
                
                <div class="status-card">
                    <div>
                        <div class="form-label">自动发文状态</div>
                        <div id="auto_status" class="status-stopped">❌ 已停止</div>
                    </div>
                    <button id="toggle_auto_btn" class="btn btn-primary" onclick="toggleAuto()">
                        <i class="fa fa-play"></i> 启动
                    </button>
                </div>
                
                <div class="form-label">今日发文统计（按账号）</div>
                <div class="stats-grid" id="today_stats">
                    {% for acc_name, stat in today_stats.items() %}
                    <div class="stat-card">
                        <div class="stat-value">{{stat.count}}</div>
                        <div class="stat-label">{{acc_name}}</div>
                        <div class="stat-label">剩余: {{stat.remaining}}/{{stat.limit}}</div>
                    </div>
                    {% endfor %}
                </div>
                
                <div class="form-group">
                    <div class="form-label">当前账号配置</div>
                    <div class="account-config-card">
                        <div><strong>发文间隔：</strong><span id="auto_interval">--</span> 分钟</div>
                        <div><strong>今日限额：</strong><span id="auto_daily_limit">--</span> 条</div>
                        <div><strong>最后运行：</strong><span id="auto_last_run">未运行</span></div>
                    </div>
                </div>
            </div>
            
            <!-- 手动模式 -->
            <div id="manual" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择发文账号</label>
                    <select id="manual_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">
                            {{acc.name}} (今日剩余: {{today_stats[acc.name].remaining}}/{{today_stats[acc.name].limit}})
                        </option>
                        {% endfor %}
                    </select>
                </div>
                
                <div class="form-group">
                    <label class="form-label">交易对</label>
                    <input type="text" id="manual_symbol" class="form-control" placeholder="如 BTCUSDT，支持大小写">
                </div>
                
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="autoSelectSymbol()">
                        <i class="fa fa-magic"></i> 自动选交易对
                    </button>
                    <button class="btn btn-secondary" onclick="generateFullTopic()">
                        <i class="fa fa-bar-chart"></i> 生成完整分析
                    </button>
                </div>
                
                <div class="form-group">
                    <label class="form-label">话题分析（可编辑）</label>
                    <textarea id="manual_topic" class="form-control" placeholder="点击上方按钮生成完整分析内容..."></textarea>
                </div>
                
                <button class="btn btn-secondary" onclick="generateAIContent()">
                    <i class="fa fa-pencil"></i> 生成发文内容
                </button>
                
                <div class="form-group">
                    <label class="form-label">最终内容（可编辑）</label>
                    <textarea id="manual_content" class="form-control" placeholder="AI生成的内容将显示在这里..."></textarea>
                </div>
                
                <button class="btn btn-primary" onclick="submitPost()">
                    <i class="fa fa-paper-plane"></i> 确认发文
                </button>
                
                <div class="log-box" id="manual_log">
                    等待操作...
                </div>
            </div>
            
            <!-- 账号配置 -->
            <div id="config" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择要配置的账号</label>
                    <select id="config_account" class="form-control" onchange="loadAccountConfig()">
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                
                <div class="form-group">
                    <label class="form-label">专属提示词</label>
                    <textarea id="config_prompt" class="form-control" placeholder="该账号的专属AI提示词，留空使用默认提示词..."></textarea>
                </div>
                
                <div class="form-group">
                    <label class="form-label">每日发文限额</label>
                    <input type="number" id="config_daily_limit" class="form-control" min="1" max="100" placeholder="默认：8">
                </div>
                
                <div class="form-group">
                    <label class="form-label">自动发文间隔（分钟）</label>
                    <input type="number" id="config_interval" class="form-control" min="5" max="1440" placeholder="默认：60">
                </div>
                
                <button class="btn btn-primary" onclick="saveAccountConfig()">
                    <i class="fa fa-save"></i> 保存配置
                </button>
                
                <div class="log-box" id="config_log">
                    选择账号后加载配置...
                </div>
            </div>
            
            <!-- 发文记录 -->
            <div id="records" class="tab-content">
                <div class="form-group">
                    <label class="form-label">筛选条件</label>
                    <div class="btn-group">
                        <select id="record_account" class="form-control">
                            <option value="">所有账号</option>
                            {% for acc in accounts %}
                            <option value="{{acc.name}}">{{acc.name}}</option>
                            {% endfor %}
                        </select>
                        <input type="date" id="record_date" class="form-control" value="{{today}}">
                        <button class="btn btn-secondary" onclick="loadRecords()">
                            <i class="fa fa-search"></i> 查询
                        </button>
                        <button class="btn btn-secondary" onclick="exportRecords()">
                            <i class="fa fa-download"></i> 导出
                        </button>
                    </div>
                </div>
                
                <div class="records-list" id="records_list">
                    请点击查询按钮加载记录...
                </div>
            </div>
        </div>
    </div>

    <script>
        // 当前激活的标签
        let activeTab = 'auto';
        
        // 切换标签
        function switchTab(tabId) {
            // 更新标签样式
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll(`.tab-btn[onclick="switchTab('${tabId}')"]`).forEach(btn => btn.classList.add('active'));
            
            // 更新内容显示
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            
            activeTab = tabId;
            
            // 刷新对应数据
            if (tabId === 'auto') refreshAutoStatus();
            if (tabId === 'config') loadAccountConfig();
        }
        
        // ======================== 自动模式相关 ========================
        function refreshAutoStatus() {
            fetch('/api/auto/status')
                .then(res => res.json())
                .then(data => {
                    // 更新状态显示
                    const statusEl = document.getElementById('auto_status');
                    const toggleBtn = document.getElementById('toggle_auto_btn');
                    
                    if (data.running) {
                        statusEl.textContent = '✅ 运行中';
                        statusEl.className = 'status-running';
                        toggleBtn.innerHTML = '<i class="fa fa-stop"></i> 停止';
                        toggleBtn.className = 'btn btn-danger';
                    } else {
                        statusEl.textContent = '❌ 已停止';
                        statusEl.className = 'status-stopped';
                        toggleBtn.innerHTML = '<i class="fa fa-play"></i> 启动';
                        toggleBtn.className = 'btn btn-primary';
                    }
                    
                    // 更新当前账号配置
                    document.getElementById('auto_interval').textContent = data.auto_interval || '--';
                    document.getElementById('auto_daily_limit').textContent = data.daily_limit || '--';
                    document.getElementById('auto_last_run').textContent = data.last_run_time || '未运行';
                    
                    // 刷新今日统计
                    refreshTodayStats();
                });
        }
        
        function toggleAuto() {
            // 获取当前选中的自动发文账号
            const account = document.getElementById('auto_account').value;
            
            fetch(`/api/auto/toggle?account=${account}`)
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        refreshAutoStatus();
                    } else {
                        alert('操作失败：' + data.msg);
                    }
                });
        }
        
        function refreshTodayStats() {
            fetch('/api/stats/today')
                .then(res => res.json())
                .then(stats => {
                    let html = '';
                    for (const [accName, stat] of Object.entries(stats)) {
                        html += `
                        <div class="stat-card">
                            <div class="stat-value">${stat.count}</div>
                            <div class="stat-label">${accName}</div>
                            <div class="stat-label">剩余: ${stat.remaining}/${stat.limit}</div>
                        </div>
                        `;
                    }
                    document.getElementById('today_stats').innerHTML = html;
                    
                    // 更新手动模式账号选项的剩余条数
                    document.querySelectorAll('#manual_account option').forEach(option => {
                        const accName = option.dataset.name;
                        if (accName && stats[accName]) {
                            option.textContent = `${accName} (今日剩余: ${stats[accName].remaining}/${stats[accName].limit})`;
                        }
                    });
                });
        }
        
        // ======================== 手动模式相关 ========================
        function autoSelectSymbol() {
            const logEl = document.getElementById('manual_log');
            logEl.textContent = '正在自动筛选交易对...';
            
            fetch('/api/manual/auto_symbol')
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('manual_symbol').value = data.symbol;
                        logEl.textContent = `✅ 自动选中：${data.symbol}`;
                    } else {
                        logEl.textContent = `❌ 筛选失败：${data.msg}`;
                    }
                })
                .catch(err => {
                    logEl.textContent = `❌ 错误：${err.message}`;
                });
        }
        
        function generateFullTopic() {
            const symbol = document.getElementById('manual_symbol').value.trim().toUpperCase();
            const logEl = document.getElementById('manual_log');
            
            if (!symbol) {
                logEl.textContent = '❌ 请先输入或选择交易对';
                return;
            }
            
            logEl.textContent = '正在生成完整分析，请稍候...';
            
            fetch(`/api/manual/full_topic?symbol=${symbol}`)
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('manual_topic').value = data.topic;
                        logEl.textContent = '✅ 完整分析生成成功！';
                    } else {
                        document.getElementById('manual_topic').value = '';
                        logEl.textContent = `❌ 生成失败：${data.msg}`;
                    }
                })
                .catch(err => {
                    logEl.textContent = `❌ 错误：${err.message}`;
                });
        }
        
        function generateAIContent() {
            const topic = document.getElementById('manual_topic').value.trim();
            const accountKey = document.getElementById('manual_account').value;
            const logEl = document.getElementById('manual_log');
            
            if (!topic) {
                logEl.textContent = '❌ 请先生成完整分析内容';
                return;
            }
            
            logEl.textContent = 'AI正在创作内容，请稍候...';
            
            fetch('/api/manual/generate_ai', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    topic: topic,
                    account_key: accountKey
                })
            })
            .then(res => res.text())
            .then(content => {
                if (content) {
                    document.getElementById('manual_content').value = content;
                    logEl.textContent = '✅ AI内容生成成功！';
                } else {
                    logEl.textContent = '❌ AI内容生成失败';
                }
            })
            .catch(err => {
                logEl.textContent = `❌ 错误：${err.message}`;
            });
        }
        
        function submitPost() {
            const accountKey = document.getElementById('manual_account').value;
            const content = document.getElementById('manual_content').value.trim();
            const accountName = document.querySelector(`#manual_account option[value="${accountKey}"]`).dataset.name;
            const symbol = document.getElementById('manual_symbol').value.trim() || '手动输入';
            const logEl = document.getElementById('manual_log');
            
            if (!content) {
                logEl.textContent = '❌ 请先生成发文内容';
                return;
            }
            
            // 检查今日限额
            fetch(`/api/stats/today?account=${accountName}`)
                .then(res => res.json())
                .then(stat => {
                    if (stat.count >= stat.limit) {
                        logEl.textContent = `❌ 账号 ${accountName} 今日已达发文限额 ${stat.limit} 条`;
                        return;
                    }
                    
                    logEl.textContent = '正在发布内容，请稍候...';
                    
                    fetch('/api/manual/post', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            account_key: accountKey,
                            content: content,
                            symbol: symbol
                        })
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            logEl.textContent = `✅ 发文成功！ID：${data.post_id}`;
                            // 清空内容并刷新统计
                            document.getElementById('manual_content').value = '';
                            refreshTodayStats();
                        } else {
                            logEl.textContent = `❌ 发文失败：${data.msg}`;
                        }
                    });
                });
        }
        
        // ======================== 账号配置相关 ========================
        function loadAccountConfig() {
            const accountName = document.getElementById('config_account').value;
            const logEl = document.getElementById('config_log');
            
            logEl.textContent = '正在加载账号配置...';
            
            fetch(`/api/config/load?account=${accountName}`)
                .then(res => res.json())
                .then(config => {
                    document.getElementById('config_prompt').value = config.prompt || '';
                    document.getElementById('config_daily_limit').value = config.daily_limit || 8;
                    document.getElementById('config_interval').value = config.auto_interval || 60;
                    logEl.textContent = '✅ 配置加载成功';
                })
                .catch(err => {
                    logEl.textContent = `❌ 加载失败：${err.message}`;
                });
        }
        
        function saveAccountConfig() {
            const accountName = document.getElementById('config_account').value;
            const prompt = document.getElementById('config_prompt').value;
            const dailyLimit = document.getElementById('config_daily_limit').value;
            const interval = document.getElementById('config_interval').value;
            const logEl = document.getElementById('config_log');
            
            if (!dailyLimit || dailyLimit < 1) {
                logEl.textContent = '❌ 每日限额必须大于0';
                return;
            }
            
            if (!interval || interval < 5) {
                logEl.textContent = '❌ 发文间隔不能小于5分钟';
                return;
            }
            
            logEl.textContent = '正在保存配置...';
            
            fetch('/api/config/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    account: accountName,
                    prompt: prompt,
                    daily_limit: parseInt(dailyLimit),
                    auto_interval: parseInt(interval)
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    logEl.textContent = '✅ 配置保存成功！';
                    // 刷新自动模式的统计
                    if (activeTab === 'auto') refreshAutoStatus();
                } else {
                    logEl.textContent = `❌ 保存失败：${data.msg}`;
                }
            });
        }
        
        // ======================== 记录查询相关 ========================
        function loadRecords() {
            const account = document.getElementById('record_account').value;
            const date = document.getElementById('record_date').value;
            const listEl = document.getElementById('records_list');
            
            listEl.innerHTML = '正在加载记录...';
            
            fetch(`/api/records?account=${account}&date=${date}`)
                .then(res => res.json())
                .then(records => {
                    if (records.length === 0) {
                        listEl.innerHTML = '暂无记录';
                        return;
                    }
                    
                    let html = '';
                    records.forEach(record => {
                        html += `
                        <div class="record-item">
                            <div class="record-header">
                                <span class="record-symbol">${record.symbol}</span>
                                <span>${record.mode === 'auto' ? '自动' : '手动'} | ${record.account}</span>
                                <span class="record-time">${record.time}</span>
                            </div>
                            <div class="record-content">${record.content}</div>
                        </div>
                        `;
                    });
                    listEl.innerHTML = html;
                });
        }
        
        function exportRecords() {
            window.open('/api/records/export');
        }
        
        // 页面加载初始化
        window.onload = function() {
            // 初始化自动模式状态
            refreshAutoStatus();
            
            // 设置默认日期为今天
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('record_date').value = today;
        };
    </script>
</body>
</html>
"""

# ======================== 接口路由 ========================
@app.route('/')
def index():
    """主页"""
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    auto_cfg = get_auto_config()
    today = str(datetime.date.today())
    
    return render_template_string(
        UI_TEMPLATE,
        accounts=accounts,
        today_stats=today_stats,
        current_auto_acc=auto_cfg.get("current_account", ""),
        today=today
    )

# ======================== 自动模式接口 ========================
@app.route('/api/auto/status')
def auto_status():
    """获取自动运行状态"""
    auto_cfg = get_auto_config()
    current_acc_name = auto_cfg.get("current_account", "")
    current_acc = get_account_by_name(current_acc_name) or {}
    
    return jsonify({
        "running": auto_cfg.get("auto_running", False),
        "current_account": current_acc_name,
        "auto_interval": current_acc.get("auto_interval", DEFAULT_AUTO_INTERVAL),
        "daily_limit": current_acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "last_run_time": auto_cfg.get("last_run_time", "")
    })

@app.route('/api/auto/toggle')
def auto_toggle():
    """切换自动运行状态"""
    account = request.args.get("account", "")
    if not account:
        return jsonify({"success": False, "msg": "请选择自动发文账号"})
    
    auto_cfg = get_auto_config()
    new_state = not auto_cfg.get("auto_running", False)
    
    save_auto_config({
        "auto_running": new_state,
        "current_account": account
    })
    
    return jsonify({
        "success": True,
        "running": new_state,
        "account": account
    })

# ======================== 统计接口 ========================
@app.route('/api/stats/today')
def today_stats_api():
    """获取今日统计"""
    account = request.args.get("account", "")
    if account:
        return jsonify(get_today_stats(account))
    return jsonify(get_today_stats())

# ======================== 手动模式接口 ========================
@app.route('/api/manual/auto_symbol')
def manual_auto_symbol():
    """自动选择交易对"""
    try:
        from topic_main import run_topic
        topic = run_topic()
        if not topic:
            return jsonify({"success": False, "msg": "未筛选到合适的交易对"})
        
        return jsonify({
            "success": True,
            "symbol": topic.get("symbol", ""),
            "preview": topic.get("text", "")[:50] + "..."
        })
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

@app.route('/api/manual/full_topic')
def manual_full_topic():
    """生成完整话题分析"""
    try:
        from topic_main import (
            fetch_url, fetch_all_for_symbol, get_trend, get_oi_state,
            get_funding_state, detect_signal, detect_conflict, build_topic_text
        )
        
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"success": False, "msg": "交易对不能为空"})
        
        # 获取基础行情
        ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
        if not ticker:
            return jsonify({"success": False, "msg": "获取基础行情失败"})
        
        # 获取深度数据
        short_k, short_oi_data, long_k, long_oi_data, funding_data = fetch_all_for_symbol(symbol)
        
        # 完整分析
        short_trend = get_trend(short_k)
        long_trend = get_trend(long_k)
        short_oi = get_oi_state(short_oi_data, symbol)
        long_oi = get_oi_state(long_oi_data, symbol)
        funding_st = get_funding_state(funding_data, symbol)
        funding_val = float(funding_data.get("lastFundingRate", 0)) if funding_data else 0.0
        chg = float(ticker["priceChangePercent"])
        sig = detect_signal(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
        conf = detect_conflict(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
        
        # 生成完整文案
        topic_text = build_topic_text(
            ticker, short_trend, long_trend,
            short_oi, long_oi, funding_st,
            funding_val, sig, conf
        )
        
        return jsonify({
            "success": True,
            "topic": topic_text,
            "symbol": symbol
        })
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

@app.route('/api/manual/generate_ai', methods=['POST'])
def manual_generate_ai():
    """生成AI内容（使用账号专属提示词）"""
    data = request.json
    topic = data.get("topic", "")
    account_key = data.get("account_key", "")
    
    if not topic or not account_key:
        return ""
    
    # 获取账号专属提示词
    account = get_account_by_key(account_key)
    custom_prompt = account.get("prompt", "") if account else ""
    
    from ai_core import generate_content
    fake_topic = {"text": topic, "symbol": "", "change": 0}
    content, _ = generate_content(fake_topic, ZHIPU_API_KEY, custom_prompt=custom_prompt)
    
    return content or ""

@app.route('/api/manual/post', methods=['POST'])
def manual_post():
    """手动发文"""
    try:
        data = request.json
        account_key = data.get("account_key", "")
        content = data.get("content", "")
        symbol = data.get("symbol", "手动")
        
        if not account_key or not content:
            return jsonify({"success": False, "msg": "参数缺失"})
        
        # 获取账号信息
        account = get_account_by_key(account_key)
        if not account:
            return jsonify({"success": False, "msg": "账号不存在"})
        
        # 检查今日限额
        today_stats = get_today_stats(account["name"])
        if today_stats["count"] >= today_stats["limit"]:
            return jsonify({"success": False, "msg": f"今日已达发文限额 {today_stats['limit']} 条"})
        
        # 发布内容
        from post_main import post_content
        ok, msg, post_id = post_content(content, account_key)
        
        if ok:
            # 保存记录
            save_post_record("manual", account["name"], symbol, content, post_id)
            return jsonify({
                "success": True,
                "post_id": post_id,
                "msg": "发文成功"
            })
        else:
            return jsonify({
                "success": False,
                "msg": msg
            })
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

# ======================== 账号配置接口 ========================
@app.route('/api/config/load')
def load_config_api():
    """加载账号配置"""
    account_name = request.args.get("account", "")
    account = get_account_by_name(account_name) or {}
    return jsonify({
        "prompt": account.get("prompt", ""),
        "daily_limit": account.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": account.get("auto_interval", DEFAULT_AUTO_INTERVAL)
    })

@app.route('/api/config/save', methods=['POST'])
def save_config_api():
    """保存账号配置"""
    try:
        data = request.json
        account_name = data.get("account", "")
        prompt = data.get("prompt", "")
        daily_limit = data.get("daily_limit", DEFAULT_DAILY_LIMIT)
        auto_interval = data.get("auto_interval", DEFAULT_AUTO_INTERVAL)
        
        if not account_name:
            return jsonify({"success": False, "msg": "账号名称不能为空"})
        
        save_account_prompt(account_name, prompt, daily_limit, auto_interval)
        return jsonify({"success": True, "msg": "配置保存成功"})
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

# ======================== 记录查询接口 ========================
@app.route('/api/records')
def get_records():
    """查询发文记录"""
    account = request.args.get("account", "")
    date = request.args.get("date", "")
    db = load_json(DB_FILE, [])
    
    records = []
    for record in db:
        # 筛选条件
        if account and record.get("account") != account:
            continue
        if date and record.get("date") != date:
            continue
        records.append(record)
    
    # 按时间倒序
    records.sort(key=lambda x: x["time"], reverse=True)
    return jsonify(records)

@app.route('/api/records/export')
def export_records():
    """导出记录为CSV"""
    db = load_json(DB_FILE, [])
    csv = "\ufeff模式,账号,日期,时间,交易对,文章ID,状态,内容\n"
    
    for record in db:
        content = record.get("content", "").replace('"', '""')  # 转义双引号
        csv += (
            f"{record.get('mode','')},"
            f"{record.get('account','')},"
            f"{record.get('date','')},"
            f"{record.get('time','')},"
            f"{record.get('symbol','')},"
            f"{record.get('post_id','')},"
            f"{record.get('status','')},"
            f'"{content}"\n'
        )
    
    return Response(
        csv,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=发文记录_{datetime.date.today()}.csv"}
    )

# ======================== 启动服务 ========================
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=False)
