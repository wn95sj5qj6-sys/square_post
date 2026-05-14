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
                    // 重新加载统计卡片
                    const statsGrid = document.getElementById('today_stats');
                    statsGrid.innerHTML = '';
                    for (const [accName, stat] of Object.entries(data.today_stats)) {
                        const card = document.createElement('div');
                        card.className = 'stat-card';
                        card.id = `stat_${accName}`;
                        card.onclick = () => showAccountConfig(accName);
                        card.innerHTML = `
                            <div class="stat-value">${stat.count}</div>
                            <div class="stat-label">${accName}</div>
                            <div class="stat-label">自动: ${stat.auto_count} | 手动: ${stat.manual_count}</div>
                            <div class="stat-label">剩余: ${stat.remaining}/${stat.limit}</div>
                            ${stat.running ? 
                                '<div class="stat-label" style="color: var(--success);">运行中</div>' : 
                                '<div class="stat-label" style="color: var(--gray);">已停止</div>'
                            }
                        `;
                        statsGrid.appendChild(card);
                    }
                    
                    // 刷新下拉账号列表
                    const selector = document.getElementById('auto_account_selector');
                    const currentValue = selector.value;
                    selector.innerHTML = '<option value="">请选择账号</option>';
                    data.accounts.forEach(acc => {
                        const option = document.createElement('option');
                        option.value = acc.name;
                        option.textContent = acc.name;
                        if (currentValue === acc.name) {
                            option.selected = true;
                        }
                        selector.appendChild(option);
                    });
                    
                    // 如果当前有选中账号，重新加载状态
                    if (currentValue) {
                        loadAccountStatus();
                    }
                });
        }
        
        // ======================== 账号配置 ========================
        function loadAccountConfig() {
            const accountName = document.getElementById('config_account').value;
            if (!accountName) return;
            
            fetch(`/api/config/load?account=${accountName}`)
                .then(res => res.json())
                .then(config => {
                    document.getElementById('config_prompt').value = config.prompt || '';
                    document.getElementById('config_daily_limit').value = config.daily_limit || DEFAULT_DAILY_LIMIT;
                    document.getElementById('config_interval').value = config.auto_interval || DEFAULT_AUTO_INTERVAL;
                    document.getElementById('config_log').textContent = `已加载账号 ${accountName} 的配置`;
                });
        }
        
        function saveAccountConfig() {
            const accountName = document.getElementById('config_account').value;
            if (!accountName) {
                alert('请选择要配置的账号');
                return;
            }
            
            const prompt = document.getElementById('config_prompt').value;
            const dailyLimit = document.getElementById('config_daily_limit').value;
            const interval = document.getElementById('config_interval').value;
            
            if (!dailyLimit || isNaN(dailyLimit) || dailyLimit < 1) {
                alert('每日限额必须是大于0的数字');
                return;
            }
            
            if (!interval || isNaN(interval) || interval < 5) {
                alert('自动间隔必须是大于等于5的数字');
                return;
            }
            
            fetch('/api/config/save', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
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
                    document.getElementById('config_log').textContent = `✅ 账号 ${accountName} 配置保存成功`;
                    // 刷新自动页面统计
                    refreshAutoPage();
                } else {
                    document.getElementById('config_log').textContent = `❌ 保存失败：${data.msg}`;
                }
            });
        }
        
        // ======================== 手动模式 ========================
        function autoSelectSymbol() {
            fetch('/api/topic/auto_symbol')
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('manual_symbol').value = data.symbol;
                        document.getElementById('manual_log').textContent = `已自动选择交易对：${data.symbol}`;
                    } else {
                        document.getElementById('manual_log').textContent = `自动选择失败：${data.msg}`;
                    }
                });
        }
        
        function generateFullTopic() {
            const symbol = document.getElementById('manual_symbol').value.trim();
            if (!symbol) {
                alert('请先输入或自动选择交易对');
                return;
            }
            
            document.getElementById('manual_log').textContent = '正在生成话题分析...';
            
            fetch(`/api/topic/generate?symbol=${symbol}`)
                .then(res => res.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('manual_topic').value = JSON.stringify(data.topic, null, 2);
                        document.getElementById('manual_log').textContent = '✅ 话题分析生成完成';
                    } else {
                        document.getElementById('manual_log').textContent = `❌ 生成失败：${data.msg}`;
                    }
                });
        }
        
        function generateAIContent() {
            const topicStr = document.getElementById('manual_topic').value.trim();
            if (!topicStr) {
                alert('请先生成话题分析');
                return;
            }
            
            let topic;
            try {
                topic = JSON.parse(topicStr);
            } catch (e) {
                alert('话题分析格式错误，请确保是有效的JSON');
                return;
            }
            
            // 获取选中账号的提示词
            const accountKey = document.getElementById('manual_account').value;
            const accountName = document.querySelector(`#manual_account option[value="${accountKey}"]`).dataset.name;
            
            document.getElementById('manual_log').textContent = '正在生成AI发文内容...';
            
            fetch('/api/ai/generate', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    topic: topic,
                    account_name: accountName
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('manual_content').value = data.content;
                    document.getElementById('manual_log').textContent = '✅ AI内容生成完成';
                } else {
                    document.getElementById('manual_log').textContent = `❌ 生成失败：${data.msg}`;
                }
            });
        }
        
        function submitPost() {
            const accountKey = document.getElementById('manual_account').value;
            const content = document.getElementById('manual_content').value.trim();
            const symbol = document.getElementById('manual_symbol').value.trim();
            const accountName = document.querySelector(`#manual_account option[value="${accountKey}"]`).dataset.name;
            
            if (!content) {
                alert('请先生成发文内容');
                return;
            }
            
            // 检查今日限额
            fetch(`/api/stats/today?account=${accountName}`)
                .then(res => res.json())
                .then(stat => {
                    if (stat.count >= stat.limit) {
                        alert(`账号 ${accountName} 今日已达发文限额 ${stat.limit} 条，无法继续发文`);
                        return;
                    }
                    
                    document.getElementById('manual_log').textContent = '正在发布内容...';
                    
                    fetch('/api/post/manual', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            account_key: accountKey,
                            account_name: accountName,
                            symbol: symbol,
                            content: content
                        })
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            document.getElementById('manual_log').textContent = `✅ 发布成功！Post ID：${data.post_id}`;
                            // 刷新统计
                            refreshAutoPage();
                            // 清空内容
                            document.getElementById('manual_content').value = '';
                        } else {
                            document.getElementById('manual_log').textContent = `❌ 发布失败：${data.msg}`;
                        }
                    });
                });
        }
        
        // ======================== 记录管理 ========================
        function loadRecords() {
            const account = document.getElementById('record_account').value;
            const date = document.getElementById('record_date').value;
            
            fetch(`/api/records/load?account=${account}&date=${date}`)
                .then(res => res.json())
                .then(data => {
                    const recordsList = document.getElementById('records_list');
                    recordsList.innerHTML = '';
                    
                    if (data.records.length === 0) {
                        recordsList.innerHTML = '<div style="text-align: center; color: var(--gray); padding: 20px;">暂无记录</div>';
                        return;
                    }
                    
                    data.records.forEach(record => {
                        const recordItem = document.createElement('div');
                        recordItem.className = 'record-item';
                        recordItem.innerHTML = `
                            <div class="record-header">
                                <span class="record-symbol">${record.symbol || '未知'}</span>
                                <span>${record.account}</span>
                                <span class="record-time">${record.time} (${record.mode === 'auto' ? '自动' : '手动'})</span>
                                <span style="color: ${record.status === 'success' ? 'var(--success)' : 'var(--danger)'}">
                                    ${record.status === 'success' ? '成功' : '失败'}
                                </span>
                            </div>
                            <div class="record-content">${record.content}</div>
                            <div style="margin-top: 8px; font-size: 12px; color: var(--gray);">
                                Post ID: ${record.post_id || '未知'}
                            </div>
                        `;
                        recordsList.appendChild(recordItem);
                    });
                });
        }
        
        function exportRecords() {
            const account = document.getElementById('record_account').value;
            const date = document.getElementById('record_date').value;
            
            fetch(`/api/records/export?account=${account}&date=${date}`)
                .then(res => res.blob())
                .then(blob => {
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `发文记录_${account || '所有账号'}_${date || '全部日期'}.json`;
                    document.body.appendChild(a);
                    a.click();
                    window.URL.revokeObjectURL(url);
                    document.body.removeChild(a);
                });
        }
        
        // ======================== 删除记录 ========================
        function deleteSelectedRecords() {
            const account = document.getElementById('delete_account').value;
            const date = document.getElementById('delete_date').value;
            
            if (!account && !date) {
                alert('请选择要删除的账号或日期');
                return;
            }
            
            if (!confirm(`确认删除${account ? '账号[' + account + ']' : '所有账号'}${date ? '日期[' + date + ']' : '所有日期'}的记录？删除后无法恢复！`)) {
                return;
            }
            
            fetch('/api/records/delete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    account: account,
                    date: date,
                    all_records: false
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('delete_log').textContent = `✅ 成功删除 ${data.deleted_count} 条记录`;
                    // 刷新记录列表
                    loadRecords();
                } else {
                    document.getElementById('delete_log').textContent = `❌ 删除失败：${data.msg}`;
                }
            });
        }
        
        function deleteAllRecords() {
            if (!confirm('确认删除所有发文记录？此操作不可逆！')) {
                return;
            }
            
            fetch('/api/records/delete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    all_records: true
                })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    document.getElementById('delete_log').textContent = `✅ 成功删除所有 ${data.deleted_count} 条记录`;
                    // 刷新记录列表
                    loadRecords();
                } else {
                    document.getElementById('delete_log').textContent = `❌ 删除失败：${data.msg}`;
                }
            });
        }
        
        // ======================== API接口 ========================
        // 页面加载完成后初始化
        window.onload = function() {
            //
