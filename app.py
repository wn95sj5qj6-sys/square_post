from flask import Flask, render_template_string, request, jsonify, Response, make_response
import os
import json
import datetime
import threading
import time
import copy
import urllib.parse  # 新增：处理文件名编码

app = Flask(__name__)

# ======================== 核心配置 ========================
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

# 数据存储路径
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/records.json"
CONFIG_FILE = f"{DATA_DIR}/config.json"
PROMPT_FILE = f"{DATA_DIR}/prompts.json"
os.makedirs(DATA_DIR, exist_ok=True)

# 多账号运行状态存储（内存中，key: 账号名，value: 是否运行）
account_running_status = {}
# 线程锁，保证多线程安全
status_lock = threading.Lock()

# ======================== 工具函数 ========================
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
    env_accounts = get_accounts_from_env()
    prompts = load_json(PROMPT_FILE)
    
    accounts = []
    for acc in env_accounts:
        acc_name = acc["name"]
        acc_config = prompts.get(acc_name, {})
        # 补充运行状态
        with status_lock:
            running = account_running_status.get(acc_name, False)
        
        accounts.append({
            "name": acc_name,
            "key": acc["key"],
            "prompt": acc_config.get("prompt", ""),
            "daily_limit": acc_config.get("daily_limit", DEFAULT_DAILY_LIMIT),
            "auto_interval": acc_config.get("auto_interval", DEFAULT_AUTO_INTERVAL),
            "running": running  # 当前账号是否运行
        })
    return accounts

def get_account_by_name(name):
    accounts = get_all_accounts()
    for acc in accounts:
        if acc["name"] == name:
            return acc
    return None

def get_account_by_key(key):
    accounts = get_all_accounts()
    for acc in accounts:
        if acc["key"] == key:
            return acc
    return None

def save_account_prompt(account_name, prompt, daily_limit, auto_interval):
    prompts = load_json(PROMPT_FILE)
    prompts[account_name] = {
        "prompt": prompt,
        "daily_limit": int(daily_limit),
        "auto_interval": int(auto_interval)
    }
    save_json(PROMPT_FILE, prompts)

# ======================== 发文记录管理（增强版） ========================
def save_post_record(mode, account_name, symbol, content, post_id, status="success"):
    record = {
        "mode": mode,
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
    # 新增：限制记录总数，防止文件过大（默认保留最近1000条）
    MAX_RECORDS = 1000
    if len(db) > MAX_RECORDS:
        db = db[-MAX_RECORDS:]  # 只保留最后1000条
    save_json(DB_FILE, db)

def get_today_stats(account_name=None):
    today = str(datetime.date.today())
    db = load_json(DB_FILE, [])
    
    stats = {}
    accounts = get_all_accounts()
    for acc in accounts:
        stats[acc["name"]] = {
            "count": 0,
            "auto_count": 0,  # 新增：自动发文数
            "manual_count": 0, # 新增：手动发文数
            "limit": acc["daily_limit"],
            "remaining": acc["daily_limit"],
            "running": acc["running"]  # 补充运行状态
        }
    
    for record in db:
        if record.get("date") == today and record.get("status") == "success":
            acc_name = record.get("account", "")
            if acc_name in stats:
                stats[acc_name]["count"] += 1
                # 区分自动/手动发文数
                if record.get("mode") == "auto":
                    stats[acc_name]["auto_count"] += 1
                else:
                    stats[acc_name]["manual_count"] += 1
                stats[acc_name]["remaining"] = stats[acc_name]["limit"] - stats[acc_name]["count"]
    
    if account_name:
        return stats.get(account_name, {"count": 0, "auto_count":0, "manual_count":0, "limit": DEFAULT_DAILY_LIMIT, "remaining": DEFAULT_DAILY_LIMIT, "running": False})
    
    return stats

# 新增：删除记录功能
def delete_records(account=None, date=None, all_records=False):
    db = load_json(DB_FILE, [])
    if all_records:
        new_db = []
    else:
        new_db = []
        for record in db:
            # 过滤需要删除的记录
            if account and record.get("account") == account:
                if date and record.get("date") == date:
                    continue
                elif not date:
                    continue
            elif date and record.get("date") == date and not account:
                continue
            new_db.append(record)
    
    save_json(DB_FILE, new_db)
    return len(db) - len(new_db)  # 返回删除的记录数

# ======================== 多账号自动发文核心逻辑 ========================
def auto_publisher_worker(account_name):
    """单个账号的自动发文线程"""
    while True:
        # 检查当前账号是否需要继续运行
        with status_lock:
            if not account_running_status.get(account_name, False):
                break
        
        # 获取账号配置
        current_acc = get_account_by_name(account_name)
        if not current_acc:
            time.sleep(10)
            continue
        
        # 检查今日限额
        today_stats = get_today_stats(account_name)
        if today_stats["count"] >= today_stats["limit"]:
            print(f"账号 {account_name} 今日已达发文限额 {today_stats['limit']}，停止自动发文")
            # 自动停止该账号运行
            with status_lock:
                account_running_status[account_name] = False
            break
        
        try:
            # 1. 获取交易对分析
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
            
            # 修复：确保post_id是字符串，避免Object类型
            post_id_str = str(post_id) if post_id and post_id != "[object Object]" else "未知ID"
            
            # 4. 保存记录
            if ok:
                save_post_record("auto", account_name, topic.get("symbol", ""), content, post_id_str)
                print(f"账号 {account_name} 自动发文成功 | 交易对：{topic.get('symbol', '')} | ID：{post_id_str}")
                # 更新最后运行时间
                cfg = load_json(CONFIG_FILE)
                cfg[f"{account_name}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cfg[f"{account_name}_last_auto_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 新增：自动最后运行时间
                cfg[f"{account_name}_last_manual_run"] = cfg.get(f"{account_name}_last_manual_run", "")  # 新增：手动最后运行时间
                save_json(CONFIG_FILE, cfg)
            else:
                save_post_record("auto", account_name, topic.get("symbol", ""), content, post_id_str, "fail")
                print(f"账号 {account_name} 自动发文失败 | 原因：{msg}")
            
            # 5. 按账号专属间隔休眠
            time.sleep(current_acc["auto_interval"] * 60)
            
        except Exception as e:
            print(f"账号 {account_name} 自动发文异常 | 错误：{str(e)}")
            time.sleep(10)
    
    print(f"账号 {account_name} 自动发文线程已停止")

def start_account_auto_publish(account_name):
    """启动单个账号的自动发文"""
    with status_lock:
        if account_running_status.get(account_name, False):
            return False  # 已在运行中
    
    # 设置运行状态为True
    with status_lock:
        account_running_status[account_name] = True
    
    # 启动独立线程
    t = threading.Thread(target=auto_publisher_worker, args=(account_name,), daemon=True)
    t.start()
    return True

def stop_account_auto_publish(account_name):
    """停止单个账号的自动发文"""
    with status_lock:
        account_running_status[account_name] = False
    return True

# ======================== 全新UI模板（含下拉账号选择+删除功能） ========================
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
        }
        
        .btn-primary:hover {
            background: #0066cc;
        }
        
        .btn-success {
            background: var(--success);
            color: white;
        }
        
        .btn-danger {
            background: var(--danger);
            color: white;
        }
        
        .btn-secondary {
            background: var(--light-gray);
            color: var(--text);
        }
        
        .btn-secondary:hover {
            background: #e5e5ea;
        }
        
        /* 新增：下拉式账号选择样式 */
        .account-selector {
            width: 100%;
            margin-bottom: 16px;
        }
        
        .account-actions-wrapper {
            display: flex;
            gap: 8px;
            margin-top: 8px;
        }
        
        .account-action-btn {
            flex: 1;
            padding: 8px 12px;
            font-size: 14px;
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
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .stat-card:hover {
            transform: scale(1.02);
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }
        
        .stat-card.active {
            border: 2px solid var(--primary);
            background: rgba(0, 122, 255, 0.05);
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
        
        .config-detail {
            background: rgba(0, 122, 255, 0.05);
            border-left: 4px solid var(--primary);
            padding: 16px;
            border-radius: 0 12px 12px 0;
            margin-bottom: 16px;
            display: none;
        }
        
        .config-detail.active {
            display: block;
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
        
        /* 新增：删除功能样式 */
        .delete-section {
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
        }
        
        @media (max-width: 480px) {
            .card {
                padding: 16px;
            }
            
            .account-actions-wrapper {
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
                <span class="badge">v2.2</span>
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
            
            <!-- 自动模式（下拉式账号选择） -->
            <div id="auto" class="tab-content active">
                <div class="form-label">选择要操作的账号</div>
                <!-- 新增：下拉式账号选择 -->
                <select id="auto_account_selector" class="form-control account-selector" onchange="loadAccountStatus()">
                    <option value="">请选择账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                
                <!-- 账号操作区域 -->
                <div id="auto_account_actions" style="display: none;">
                    <div style="padding: 16px; background: var(--light-gray); border-radius: 12px; margin-bottom: 16px;">
                        <div style="font-weight: 600; margin-bottom: 8px;" id="auto_account_name">账号名称</div>
                        <div id="auto_account_status">
                            <span style="color: var(--gray);"><i class="fa fa-circle"></i> 已停止</span>
                            | 今日限额: <span id="auto_daily_limit">8</span>条 
                            | 间隔: <span id="auto_interval">60</span>分钟
                            | 今日已发: <span id="auto_today_count">0</span>条 (自动: <span id="auto_auto_count">0</span> | 手动: <span id="auto_manual_count">0</span>)
                        </div>
                    </div>
                    
                    <div class="account-actions-wrapper">
                        <button id="auto_start_btn" class="btn btn-success account-action-btn" onclick="startAuto()">
                            <i class="fa fa-play"></i> 启动自动发文
                        </button>
                        <button id="auto_stop_btn" class="btn btn-danger account-action-btn" onclick="stopAuto()">
                            <i class="fa fa-stop"></i> 停止自动发文
                        </button>
                    </div>
                </div>
                
                <div class="form-label" style="margin-top: 20px;">今日发文统计（点击查看账号配置）</div>
                <div class="stats-grid" id="today_stats">
                    {% for acc_name, stat in today_stats.items() %}
                    <div class="stat-card" id="stat_{{acc_name}}" onclick="showAccountConfig('{{acc_name}}')">
                        <div class="stat-value">{{stat.count}}</div>
                        <div class="stat-label">{{acc_name}}</div>
                        <div class="stat-label">自动: {{stat.auto_count}} | 手动: {{stat.manual_count}}</div>
                        <div class="stat-label">剩余: {{stat.remaining}}/{{stat.limit}}</div>
                        {% if stat.running %}
                        <div class="stat-label" style="color: var(--success);">运行中</div>
                        {% else %}
                        <div class="stat-label" style="color: var(--gray);">已停止</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                
                <!-- 账号配置详情（区分自动/手动统计） -->
                <div class="config-detail" id="account_config_detail">
                    <div id="config_detail_content">请点击上方统计卡片查看账号配置...</div>
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
                
                <div style="display: flex; gap: 8px; margin-bottom: 16px;">
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
                
                <button class="btn btn-secondary" onclick="generateAIContent()" style="width: 100%; margin-bottom: 16px;">
                    <i class="fa fa-pencil"></i> 生成发文内容
                </button>
                
                <div class="form-group">
                    <label class="form-label">最终内容（可编辑）</label>
                    <textarea id="manual_content" class="form-control" placeholder="AI生成的内容将显示在这里..."></textarea>
                </div>
                
                <button class="btn btn-primary" onclick="submitPost()" style="width: 100%;">
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
                
                <button class="btn btn-primary" onclick="saveAccountConfig()" style="width: 100%;">
                    <i class="fa fa-save"></i> 保存配置
                </button>
                
                <div class="log-box" id="config_log">
                    选择账号后加载配置...
                </div>
            </div>
            
            <!-- 发文记录（新增删除功能） -->
            <div id="records" class="tab-content">
                <div class="form-group">
                    <label class="form-label">筛选条件</label>
                    <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                        <select id="record_account" class="form-control" style="flex: 1; min-width: 120px;">
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
                
                <!-- 新增：删除记录功能区 -->
                <div class="delete-section">
                    <div class="form-label">删除记录</div>
                    <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                        <select id="delete_account" class="form-control" style="flex: 1; min-width: 120px;">
                            <option value="">所有账号</option>
                            {% for acc in accounts %}
                            <option value="{{acc.name}}">{{acc.name}}</option>
                            {% endfor %}
                        </select>
                        <input type="date" id="delete_date" class="form-control" placeholder="选择日期（留空删除该账号所有记录）">
                        <button class="btn btn-danger" onclick="deleteSelectedRecords()">
                            <i class="fa fa-trash"></i> 删除选中记录
                        </button>
                        <button class="btn btn-danger" onclick="deleteAllRecords()" style="background: #d92d20;">
                            <i class="fa fa-trash-o"></i> 删除所有记录
                        </button>
                    </div>
                    <div class="log-box" id="delete_log" style="margin-top: 8px; min-height: 40px;">
                        谨慎操作！删除后无法恢复
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        // 切换标签
        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll(`.tab-btn[onclick="switchTab('${tabId}')"]`).forEach(btn => btn.classList.add('active'));
            
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            
            if (tabId === 'auto') refreshAutoPage();
            if (tabId === 'config') loadAccountConfig();
        }
        
        // ======================== 自动模式 - 下拉式账号操作 ========================
        function loadAccountStatus() {
            const accountName = document.getElementById('auto_account_selector').value;
            if (!accountName) {
                document.getElementById('auto_account_actions').style.display = 'none';
                return;
            }
            
            // 加载账号状态
            fetch(`/api/auto/status?account=${accountName}`)
                .then(res => res.json())
                .then(data => {
                    // 显示操作区域
                    document.getElementById('auto_account_actions').style.display = 'block';
                    
                    // 更新账号信息
                    document.getElementById('auto_account_name').textContent = accountName;
                    document.getElementById('auto_daily_limit').textContent = data.daily_limit;
                    document.getElementById('auto_interval').textContent = data.auto_interval;
                    
                    // 更新状态显示
                    const statusEl = document.getElementById('auto_account_status');
                    const statusText = data.running ? 
                        `<span style="color: var(--success);"><i class="fa fa-circle"></i> 运行中</span>` : 
                        `<span style="color: var(--gray);"><i class="fa fa-circle"></i> 已停止</span>`;
                    
                    // 更新今日统计
                    fetch(`/api/stats/today?account=${accountName}`)
                        .then(res => res.json())
                        .then(stat => {
                            statusEl.innerHTML = `${statusText}
                            | 今日限额: <span id="auto_daily_limit">${stat.limit}</span>条 
                            | 间隔: <span id="auto_interval">${data.auto_interval}</span>分钟
                            | 今日已发: <span id="auto_today_count">${stat.count}</span>条 (自动: <span id="auto_auto_count">${stat.auto_count}</span> | 手动: <span id="auto_manual_count">${stat.manual_count}</span>)`;
                            
                            // 更新按钮状态
                            document.getElementById('auto_start_btn').disabled = data.running;
                            document.getElementById('auto_stop_btn').disabled = !data.running;
                        });
                });
        }
        
        function startAuto() {
            const accountName = document.getElementById('auto_account_selector').value;
            if (!accountName) {
                alert('请先选择账号');
                return;
            }
            
            fetch(`/api/auto/start?account=${accountName}`)
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        alert(`账号 ${accountName} 启动成功！`);
                        loadAccountStatus();
                        refreshAutoPage();
                    } else {
                        alert(`启动失败：${data.msg}`);
                    }
                });
        }
        
        function stopAuto() {
            const accountName = document.getElementById('auto_account_selector').value;
            if (!accountName) {
                alert('请先选择账号');
                return;
            }
            
            fetch(`/api/auto/stop?account=${accountName}`)
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        alert(`账号 ${accountName} 已停止！`);
                        loadAccountStatus();
                        refreshAutoPage();
                    } else {
                        alert(`停止失败：${data.msg}`);
                    }
                });
        }
        
        // ======================== 统计卡片 - 展示区分自动/手动的配置 ========================
        function showAccountConfig(accountName) {
            // 移除所有统计卡片的激活状态
            document.querySelectorAll('.stat-card').forEach(card => card.classList.remove('active'));
            // 激活当前卡片
            document.getElementById(`stat_${accountName}`).classList.add('active');
            
            // 加载并显示账号配置
            fetch(`/api/config/load?account=${accountName}`)
                .then(res => res.json())
                .then(config => {
                    // 获取最后运行时间（区分自动/手动）
                    fetch(`/api/auto/last_run?account=${accountName}`)
                        .then(res => res.json())
                        .then(lastRunData => {
                            const lastAutoRun = lastRunData.last_auto_run || '从未运行';
                            const lastManualRun = lastRunData.last_manual_run || '从未运行';
                            const lastRun = lastRunData.last_run || '从未运行';
                            
                            // 获取今日统计
                            fetch(`/api/stats/today?account=${accountName}`)
                                .then(res => res.json())
                                .then(stat => {
                                    // 拼接配置详情
                                    let html = `
                                    <div style="font-weight: 600; margin-bottom: 8px;">${accountName} - 配置详情</div>
                                    <div><strong>专属提示词：</strong>${config.prompt || '使用默认提示词'}</div>
                                    <div><strong>自动发文间隔：</strong>${config.auto_interval} 分钟</div>
                                    <div><strong>今日发文限额：</strong>${config.daily_limit} 条</div>
                                    <div><strong>今日发文统计：</strong>总计 ${stat.count} 条（自动：${stat.auto_count} | 手动：${stat.manual_count}）</div>
                                    <div><strong>最后运行时间：</strong>${lastRun}</div>
                                    <div><strong>最后自动发文：</strong>${lastAutoRun}</div>
                                    <div><strong>最后手动发文：</strong>${lastManualRun}</div>
                                    `;
                                    
                                    // 显示配置详情
                                    document.getElementById('config_detail_content').innerHTML = html;
                                    document.getElementById('account_config_detail').classList.add('active');
                                });
                        });
                });
        }
        
        // ======================== 页面刷新 ========================
        function refreshAutoPage() {
            // 刷新账号列表和统计
            fetch('/api/auto/refresh')
                .then(res => res.json())
                .then(data => {
                    // 更新统计卡片
                    let statsHtml = '';
                    for (const [accName, stat] of Object.entries(data.today_stats)) {
                        statsHtml += `
                        <div class="stat-card" id="stat_${accName}" onclick="showAccountConfig('${accName}')">
                            <div class="stat-value">${stat.count}</div>
                            <div class="stat-label">${accName}</div>
                            <div class="stat-label">自动: ${stat.auto_count} | 手动: ${stat.manual_count}</div>
                            <div class="stat-label">剩余: ${stat.remaining}/${stat.limit}</div>
                            ${stat.running ? 
                                '<div class="stat-label" style="color: var(--success);">运行中</div>' : 
                                '<div class="stat-label" style="color: var(--gray);">已停止</div>'
                            }
                        </div>
                        `;
                    }
                    document.getElementById('today_stats').innerHTML = statsHtml;
                    
                    // 更新手动模式账号选项
                    document.querySelectorAll('#manual_account option').forEach(option => {
                        const accName = option.dataset.name;
                        if (accName && data.today_stats[accName]) {
                            option.textContent = `${accName} (今日剩余: ${data.today_stats[accName].remaining}/${data.today_stats[accName].limit})`;
                        }
                    });
                });
        }
        
        // ======================== 手动模式相关（修复返回值） ========================
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
                            // 修复：显示正常的post_id，而非[object Object]
                            logEl.textContent = `✅ 发文成功！ID：${data.post_id || '未知'}`;
                            document.getElementById('manual_content').value = '';
                            refreshAutoPage();
                            
                            // 更新最后手动运行时间
                            fetch(`/api/auto/update_last_manual?account=${accountName}`);
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
                    refreshAutoPage();
                } else {
                    logEl.textContent = `❌ 保存失败：${data.msg}`;
                }
            });
        }
        
        // ======================== 记录查询&导出&删除 ========================
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
                            <div style="font-size: 12px; color: var(--gray); margin-top: 4px;">ID：${record.post_id || '未知'}</div>
                        </div>
                        `;
                    });
                    listEl.innerHTML = html;
                });
        }
        
        // 修复：导出功能（解决文件名编码问题）
        function exportRecords() {
            const account = document.getElementById('record_account').value;
            const date = document.getElementById('record_date').value;
            const url = `/api/records/export?account=${encodeURIComponent(account)}&date=${encodeURIComponent(date)}`;
            window.open(url);
        }
        
        // 新增：删除选中记录
        function deleteSelectedRecords() {
            const account = document.getElementById('delete_account').value;
            const date = document.getElementById('delete_date').value;
            
            if (!account && !date) {
                document.getElementById('delete_log').textContent = '❌ 请选择要删除的账号或日期';
                return;
            }
            
            if (!confirm('确定要删除选中的记录吗？删除后无法恢复！')) {
                return;
            }
            
            fetch(`/api/records/delete?account=${encodeURIComponent(account)}&date=${encodeURIComponent(date)}`, {
                method: 'POST'
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('delete_log').textContent = `✅ 成功删除 ${data.deleted_count} 条记录`;
                loadRecords(); // 重新加载记录
                refreshAutoPage(); // 刷新统计
            });
        }
        
        // 新增：删除所有记录
        function deleteAllRecords() {
            if (!confirm('确定要删除所有记录吗？此操作不可恢复！')) {
                return;
            }
            
            fetch('/api/records/delete?all=true', {
                method: 'POST'
            })
            .then(res => res.json())
            .then(data => {
                document.getElementById('delete_log').textContent = `✅ 成功删除 ${data.deleted_count} 条记录`;
                loadRecords(); // 重新加载记录
                refreshAutoPage(); // 刷新统计
            });
        }
        
        // 页面加载初始化
        window.onload = function() {
            refreshAutoPage();
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('record_date').value = today;
            document.getElementById('delete_date').value = today;
        };
    </script>
</body>
</html>
"""

# ======================== 接口修复&新增 ========================
@app.route('/')
def index():
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    today = str(datetime.date.today())
    
    return render_template_string(
        UI_TEMPLATE,
        accounts=accounts,
        today_stats=today_stats,
        today=today
    )

# 多账号启停接口
@app.route('/api/auto/start')
def auto_start():
    account_name = request.args.get("account", "")
    if not account_name:
        return jsonify({"success": False, "msg": "请指定账号名称"})
    
    if not get_account_by_name(account_name):
        return jsonify({"success": False, "msg": "账号不存在"})
    
    # 启动账号自动发文
    success = start_account_auto_publish(account_name)
    if success:
        return jsonify({"success": True, "msg": f"账号 {account_name} 启动成功"})
    else:
        return jsonify({"success": False, "msg": f"账号 {account_name} 已在运行中"})

@app.route('/api/auto/stop')
def auto_stop():
    account_name = request.args.get("account", "")
    if not account_name:
        return jsonify({"success": False, "msg": "请指定账号名称"})
    
    # 停止账号自动发文
    stop_account_auto_publish(account_name)
    return jsonify({"success": True, "msg": f"账号 {account_name} 已停止"})

# 修复：获取账号最后运行时间（区分自动/手动）
@app.route('/api/auto/last_run')
def auto_last_run():
    account_name = request.args.get("account", "")
    cfg = load_json(CONFIG_FILE)
    return jsonify({
        "last_run": cfg.get(f"{account_name}_last_run", ""),
        "last_auto_run": cfg.get(f"{account_name}_last_auto_run", ""),
        "last_manual_run": cfg.get(f"{account_name}_last_manual_run", "")
    })

# 新增：更新最后手动运行时间
@app.route('/api/auto/update_last_manual')
def update_last_manual():
    account_name = request.args.get("account", "")
    if account_name:
        cfg = load_json(CONFIG_FILE)
        cfg[f"{account_name}_last_manual_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg[f"{account_name}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_json(CONFIG_FILE, cfg)
    return jsonify({"success": True})

# 刷新自动模式页面数据
@app.route('/api/auto/refresh')
def auto_refresh():
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    return jsonify({
        "accounts": accounts,
        "today_stats": today_stats
    })

@app.route('/api/auto/status')
def auto_status():
    account_name = request.args.get("account", "")
    current_acc = get_account_by_name(account_name) or {}
    cfg = load_json(CONFIG_FILE)
    
    return jsonify({
        "running": current_acc.get("running", False),
        "auto_interval": current_acc.get("auto_interval", DEFAULT_AUTO_INTERVAL),
        "daily_limit": current_acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "last_run_time": cfg.get(f"{account_name}_last_run", "")
    })

@app.route('/api/stats/today')
def today_stats_api():
    account = request.args.get("account", "")
    if account:
        return jsonify(get_today_stats(account))
    return jsonify(get_today_stats())

@app.route('/api/manual/auto_symbol')
def manual_auto_symbol():
    try:
        from topic_main import run_topic
        topic = run_topic()
        if not topic:
            return jsonify({"success": False, "msg": "未筛选到合适的交易对"})
        return jsonify({"success": True, "symbol": topic.get("symbol", "")})
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

@app.route('/api/manual/full_topic')
def manual_full_topic():
    try:
        from topic_main import fetch_url, fetch_all_for_symbol, get_trend, get_oi_state, get_funding_state, detect_signal, detect_conflict, build_topic_text
        symbol = request.args.get("symbol", "").strip().upper()
        if not symbol:
            return jsonify({"success": False, "msg": "交易对不能为空"})
        
        ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={symbol}")
        if not ticker:
            return jsonify({"success": False, "msg": "获取基础行情失败"})
        
        short_k, short_oi_data, long_k, long_oi_data, funding_data = fetch_all_for_symbol(symbol)
        short_trend = get_trend(short_k)
        long_trend = get_trend(long_k)
        short_oi = get_oi_state(short_oi_data, symbol)
        long_oi = get_oi_state(long_oi_data, symbol)
        funding_st = get_funding_state(funding_data, symbol)
        funding_val = float(funding_data.get("lastFundingRate", 0)) if funding_data else 0.0
        chg = float(ticker["priceChangePercent"])
        sig = detect_signal(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
        conf = detect_conflict(short_trend, long_trend, short_oi, long_oi, funding_st, chg)
        
        topic_text = build_topic_text(ticker, short_trend, long_trend, short_oi, long_oi, funding_st, funding_val, sig, conf)
        return jsonify({"success": True, "topic": topic_text, "symbol": symbol})
    except Exception as e:
        return jsonify({"success": False, "msg": str(e)})

@app.route('/api/manual/generate_ai', methods=['POST'])
def manual_generate_ai():
    data = request.json
    topic = data.get("topic", "")
    account_key = data.get("account_key", "")
    
    if not topic or not account_key:
        return ""
    
    account = get_account_by_key(account_key)
    custom_prompt = account.get("prompt", "") if account else ""
    
    from ai_core import generate_content
    fake_topic = {"text": topic, "symbol": "", "change": 0}
    content, _ = generate_content(fake_topic, ZHIPU_API_KEY, custom_prompt=custom_prompt)
    
    return content or ""

# 修复：手动发文返回值异常
@app.route('/api/manual/post', methods=['POST'])
def manual_post():
    try:
        data = request.json
        account_key = data.get("account_key", "")
        content = data.get("content", "")
        symbol = data.get("symbol", "手动")
        
        if not account_key or not content:
            return jsonify({"success": False, "msg": "参数缺失"})
        
        account = get_account_by_key(account_key)
        if not account:
            return jsonify({"success": False, "msg": "账号不存在"})
        
        today_stats = get_today_stats(account["name"])
        if today_stats["count"] >= today_stats["limit"]:
            return jsonify({"success": False, "msg": f"今日已达发文限额 {today_stats['limit']} 条"})
        
        from post_main import post_content
        ok, msg, post_id = post_content(content, account_key)
        
        # 修复：确保post_id是字符串，避免[object Object]
        post_id_str = str(post_id) if post_id and post_id != "[object Object]" else "未知ID"
        
        if ok:
            save_post_record("manual", account["name"], symbol, content, post_id_str)
            # 更新最后手动运行时间
            cfg = load_json(CONFIG_FILE)
            cfg[f"{account['name']}_last_manual_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cfg[f"{account['name']}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_json(CONFIG_FILE, cfg)
            
            return jsonify({"success": True, "post_id": post_id_str, "msg": "发文成功"})
        else:
            return jsonify({"success": False, "msg": msg, "post_id": post_id_str})
    except Exception as e:
        return jsonify({"success": False, "msg": str(e), "post_id": "未知ID"})

@app.route('/api/config/load')
def load_config_api():
    account_name = request.args.get("account", "")
    account = get_account_by_name(account_name) or {}
    return jsonify({
        "prompt": account.get("prompt", ""),
        "daily_limit": account.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": account.get("auto_interval", DEFAULT_AUTO_INTERVAL)
    })

@app.route('/api/config/save', methods=['POST'])
def save_config_api():
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

@app.route('/api/records')
def get_records():
    account = request.args.get("account", "")
    date = request.args.get("date", "")
    db = load_json(DB_FILE, [])
    
    records = []
    for record in db:
        if account and record.get("account") != account:
            continue
        if date and record.get("date") != date:
            continue
        records.append(record)
    
    records.sort(key=lambda x: x["time"], reverse=True)
    return jsonify(records)

# 修复：导出功能（解决HTTP Header错误+文件名编码）
@app.route('/api/records/export')
def export_records():
    account = request.args.get("account", "")
    date = request.args.get("date", "")
    db = load_json(DB_FILE, [])
    
    # 筛选需要导出的记录
    export_records = []
    for record in db:
        if account and record.get("account") != account:
            continue
        if date and record.get("date") != date:
            continue
        export_records.append(record)
    
    # 生成CSV内容
    csv = "\ufeff模式,账号,日期,时间,交易对,文章ID,状态,内容\n"
    for record in export_records:
        content = record.get("content", "").replace('"', '""')
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
    
    # 修复：正确设置Header，解决Invalid HTTP Header错误
    filename = f"发文记录_{datetime.date.today()}.csv"
    # 编码文件名，避免中文乱码和Header错误
    encoded_filename = urllib.parse.quote(filename)
    
    response = make_response(csv)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{encoded_filename}"
    
    return response

# 新增：删除记录接口
@app.route('/api/records/delete', methods=['POST'])
def delete_records_api():
    try:
        account = request.args.get("account", "")
        date = request.args.get("date", "")
        all_records = request.args.get("all", "").lower() == "true"
        
        deleted_count = delete_records(account=account, date=date, all_records=all_records)
        return jsonify({"success": True, "deleted_count": deleted_count})
    except Exception as e:
        return jsonify({"success": False, "msg": str(e), "deleted_count": 0})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=False)