from flask import Flask, render_template_string, request, jsonify, Response, make_response
import os
import json
import datetime
import threading
import time
import copy
import urllib.parse

app = Flask(__name__)

# ======================== 核心配置 ========================
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
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
        with status_lock:
            running = account_running_status.get(acc_name, False)
        
        accounts.append({
            "name": acc_name,
            "key": acc["key"],
            "prompt": acc_config.get("prompt", ""),
            "model_type": acc_config.get("model_type", "zhipu"),
            "daily_limit": acc_config.get("daily_limit", DEFAULT_DAILY_LIMIT),
            "auto_interval": acc_config.get("auto_interval", DEFAULT_AUTO_INTERVAL),
            "running": running
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

def save_account_prompt(account_name, prompt, daily_limit, auto_interval, model_type="zhipu"):
    prompts = load_json(PROMPT_FILE)
    prompts[account_name] = {
        "prompt": prompt,
        "model_type": model_type,
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
    MAX_RECORDS = 1000
    if len(db) > MAX_RECORDS:
        db = db[-MAX_RECORDS:]
    save_json(DB_FILE, db)

def get_today_stats(account_name=None):
    today = str(datetime.date.today())
    db = load_json(DB_FILE, [])
    
    stats = {}
    accounts = get_all_accounts()
    for acc in accounts:
        stats[acc["name"]] = {
            "count": 0,
            "auto_count": 0,
            "manual_count": 0,
            "limit": acc["daily_limit"],
            "remaining": acc["daily_limit"],
            "running": acc["running"]
        }
    
    for record in db:
        if record.get("date") == today and record.get("status") == "success":
            acc_name = record.get("account", "")
            if acc_name in stats:
                stats[acc_name]["count"] += 1
                if record.get("mode") == "auto":
                    stats[acc_name]["auto_count"] += 1
                else:
                    stats[acc_name]["manual_count"] += 1
                stats[acc_name]["remaining"] = stats[acc_name]["limit"] - stats[acc_name]["count"]
    
    if account_name:
        return stats.get(account_name, {"count": 0, "auto_count":0, "manual_count":0, "limit": DEFAULT_DAILY_LIMIT, "remaining": DEFAULT_DAILY_LIMIT, "running": False})
    
    return stats

def delete_records(account=None, date=None, all_records=False):
    db = load_json(DB_FILE, [])
    if all_records:
        new_db = []
    else:
        new_db = []
        for record in db:
            if account and record.get("account") == account:
                if date and record.get("date") == date:
                    continue
                elif not date:
                    continue
            elif date and record.get("date") == date and not account:
                continue
            new_db.append(record)
    
    save_json(DB_FILE, new_db)
    return len(db) - len(new_db)

# ======================== 多账号自动发文核心逻辑 ========================
def auto_publisher_worker(account_name):
    while True:
        with status_lock:
            if not account_running_status.get(account_name, False):
                break
        
        current_acc = get_account_by_name(account_name)
        if not current_acc:
            time.sleep(10)
            continue
        
        today_stats = get_today_stats(account_name)
        if today_stats["count"] >= today_stats["limit"]:
            print(f"账号 {account_name} 今日已达发文限额")
            with status_lock:
                account_running_status[account_name] = False
            break
        
        try:
            from topic_main import run_topic
            topic = run_topic()
            if not topic:
                time.sleep(10)
                continue
            
            from ai_core import generate_content
            content, _ = generate_content(
                topic,
                model_type=current_acc["model_type"],
                custom_prompt=current_acc["prompt"]
            )
            
            if not content:
                time.sleep(10)
                continue
            
            from post_main import post_content
            ok, msg, post_id = post_content(content, current_acc["key"])
            post_id_str = str(post_id) if post_id else "未知ID"
            
            if ok:
                save_post_record("auto", account_name, topic.get("symbol", ""), content, post_id_str)
                cfg = load_json(CONFIG_FILE)
                cfg[f"{account_name}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cfg[f"{account_name}_last_auto_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_json(CONFIG_FILE, cfg)
            
            time.sleep(current_acc["auto_interval"] * 60)
            
        except Exception as e:
            print(f"自动发文异常: {e}")
            time.sleep(10)
    
    print(f"账号 {account_name} 自动线程停止")

def start_account_auto_publish(account_name):
    with status_lock:
        if account_running_status.get(account_name, False):
            return False
    
    with status_lock:
        account_running_status[account_name] = True
    
    t = threading.Thread(target=auto_publisher_worker, args=(account_name,), daemon=True)
    t.start()
    return True

def stop_account_auto_publish(account_name):
    with status_lock:
        account_running_status[account_name] = False
    return True

# ======================== 网页接口 ========================
@app.route('/')
def index():
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    today = str(datetime.date.today())
    return render_template_string(UI_TEMPLATE, accounts=accounts, today_stats=today_stats, today=today)

@app.route('/api/config/load')
def load_config_api():
    account_name = request.args.get("account", "")
    account = get_account_by_name(account_name) or {}
    return jsonify({
        "prompt": account.get("prompt", ""),
        "model_type": account.get("model_type", "zhipu"),
        "daily_limit": account.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": account.get("auto_interval", DEFAULT_AUTO_INTERVAL)
    })

@app.route('/api/config/save', methods=['POST'])
def save_config_api():
    try:
        data = request.json
        account_name = data.get("account", "")
        prompt = data.get("prompt", "")
        model_type = data.get("model_type", "zhipu")
        daily_limit = data.get("daily_limit", DEFAULT_DAILY_LIMIT)
        auto_interval = data.get("auto_interval", DEFAULT_AUTO_INTERVAL)

        if not account_name:
            return jsonify({"success": False, "msg": "账号不能为空"})

        save_account_prompt(account_name, prompt, daily_limit, auto_interval, model_type)
        return jsonify({"success": True, "msg": "保存成功"})
    except:
        return jsonify({"success": False, "msg": "保存失败"})

@app.route('/api/auto/status')
def auto_status_api():
    account = request.args.get("account")
    acc = get_account_by_name(account)
    return jsonify({
        "running": account_running_status.get(account, False),
        "daily_limit": acc.get("daily_limit", DEFAULT_DAILY_LIMIT) if acc else DEFAULT_DAILY_LIMIT,
        "auto_interval": acc.get("auto_interval", DEFAULT_AUTO_INTERVAL) if acc else DEFAULT_AUTO_INTERVAL
    })

@app.route('/api/auto/start')
def start_auto_api():
    account = request.args.get("account")
    ok = start_account_auto_publish(account)
    return jsonify({"success": ok})

@app.route('/api/auto/stop')
def stop_auto_api():
    account = request.args.get("account")
    ok = stop_account_auto_publish(account)
    return jsonify({"success": ok})

@app.route('/api/stats/today')
def stats_today_api():
    account = request.args.get("account")
    return jsonify(get_today_stats(account))

@app.route('/api/auto/refresh')
def refresh_auto_api():
    return jsonify({
        "accounts": get_all_accounts(),
        "today_stats": get_today_stats()
    })

@app.route('/api/auto/last_run')
def last_run_api():
    account = request.args.get("account")
    cfg = load_json(CONFIG_FILE)
    return jsonify({
        "last_run": cfg.get(f"{account}_last_run", ""),
        "last_auto_run": cfg.get(f"{account}_last_auto_run", ""),
        "last_manual_run": cfg.get(f"{account}_last_manual_run", "")
    })

@app.route('/api/topic/auto_symbol')
def auto_symbol_api():
    try:
        from topic_main import get_top_symbols
        symbols = get_top_symbols()
        return jsonify({"success": True, "symbol": symbols[0] if symbols else "BTCUSDT"})
    except:
        return jsonify({"success": False})

@app.route('/api/topic/generate')
def generate_topic_api():
    try:
        from topic_main import analyze_symbol
        symbol = request.args.get("symbol")
        topic = analyze_symbol(symbol)
        return jsonify({"success": True, "topic": topic})
    except:
        return jsonify({"success": False})

@app.route('/api/ai/generate', methods=['POST'])
def ai_generate_api():
    try:
        data = request.json
        topic = data.get("topic")
        account_name = data.get("account_name")
        acc = get_account_by_name(account_name)
        from ai_core import generate_content
        content, _ = generate_content(
            topic,
            model_type=acc.get("model_type", "zhipu"),
            custom_prompt=acc.get("prompt", "")
        )
        return jsonify({"success": True, "content": content})
    except:
        return jsonify({"success": False})

@app.route('/api/post/manual', methods=['POST'])
def manual_post_api():
    try:
        data = request.json
        key = data.get("account_key")
        name = data.get("account_name")
        content = data.get("content")
        symbol = data.get("symbol")
        from post_main import post_content
        ok, msg, pid = post_content(content, key)
        if ok:
            save_post_record("manual", name, symbol, content, str(pid))
            cfg = load_json(CONFIG_FILE)
            cfg[f"{name}_last_manual_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_json(CONFIG_FILE, cfg)
        return jsonify({"success": ok, "post_id": pid})
    except:
        return jsonify({"success": False})

@app.route('/api/records/load')
def load_records_api():
    acc = request.args.get("account")
    date = request.args.get("date")
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if acc and r.get("account") != acc:
            continue
        if date and r.get("date") != date:
            continue
        res.append(r)
    return jsonify({"records": res[-50:]})

@app.route('/api/records/delete', methods=['POST'])
def delete_records_api():
    data = request.json
    cnt = delete_records(
        account=data.get("account"),
        date=data.get("date"),
        all_records=data.get("all_records")
    )
    return jsonify({"success": True, "deleted_count": cnt})

@app.route('/api/records/export')
def export_records_api():
    acc = request.args.get("account")
    date = request.args.get("date")
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if acc and r.get("account") != acc:
            continue
        if date and r.get("date") != date:
            continue
        res.append(r)
    content = json.dumps(res, ensure_ascii=False, indent=2)
    return Response(content, mimetype="application/json")

# ======================== 前端界面 ========================
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
        }
        
        .form-control {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid var(--border);
            border-radius: 12px;
            font-size: 15px;
        }
        
        textarea.form-control {
            min-height: 120px;
            resize: vertical;
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 12px;
            font-size: 15px;
            cursor: pointer;
        }
        
        .btn-primary {
            background: var(--primary);
            color: white;
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
        }
        
        .account-selector {
            width: 100%;
            margin-bottom: 16px;
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
        }
        
        .stat-value {
            font-size: 24px;
            font-weight: 600;
        }
        
        .log-box {
            background: var(--light-gray);
            border-radius: 12px;
            padding: 16px;
            margin-top: 16px;
            font-size: 14px;
        }
        
        .records-list {
            max-height: 400px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }
        
        .record-item {
            background: var(--light-gray);
            border-radius: 12px;
            padding: 16px;
        }
    </style>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>币安自动发文助手</h1>
            </div>
            
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('auto')"><i class="fa fa-robot"></i> 自动模式</button>
                <button class="tab-btn" onclick="switchTab('manual')"><i class="fa fa-hand-pointer-o"></i> 手动模式</button>
                <button class="tab-btn" onclick="switchTab('config')"><i class="fa fa-cog"></i> 账号配置</button>
                <button class="tab-btn" onclick="switchTab('records')"><i class="fa fa-history"></i> 发文记录</button>
            </div>
            
            <div id="auto" class="tab-content active">
                <div class="form-label">选择账号</div>
                <select id="auto_account_selector" class="form-control account-selector" onchange="loadAccountStatus()">
                    <option value="">请选择账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                
                <div id="auto_account_actions" style="display: none; margin-top:16px">
                    <div style="padding:16px; background:var(--light-gray); border-radius:12px">
                        <div id="auto_account_name"></div>
                        <div id="auto_account_status"></div>
                    </div>
                    <div style="display:flex; gap:8px; margin-top:8px">
                        <button class="btn btn-success" onclick="startAuto()" style="flex:1">启动</button>
                        <button class="btn btn-danger" onclick="stopAuto()" style="flex:1">停止</button>
                    </div>
                </div>
                
                <div class="stats-grid" id="today_stats">
                    {% for acc_name, stat in today_stats.items() %}
                    <div class="stat-card" onclick="showAccountConfig('{{acc_name}}')">
                        <div class="stat-value">{{stat.count}}</div>
                        <div>{{acc_name}}</div>
                        <div>剩余: {{stat.remaining}}/{{stat.limit}}</div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            
            <div id="manual" class="tab-content">
                <div class="form-group">
                    <label class="form-label">账号</label>
                    <select id="manual_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">交易对</label>
                    <input type="text" id="manual_symbol" class="form-control">
                </div>
                <button class="btn btn-secondary" onclick="autoSelectSymbol()">自动选交易对</button>
                <button class="btn btn-secondary" onclick="generateFullTopic()">生成分析</button>
                <div class="form-group">
                    <label class="form-label">话题</label>
                    <textarea id="manual_topic" class="form-control"></textarea>
                </div>
                <button class="btn btn-secondary" onclick="generateAIContent()" style="width:100%">生成内容</button>
                <div class="form-group">
                    <label class="form-label">最终内容</label>
                    <textarea id="manual_content" class="form-control"></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitPost()" style="width:100%">发布</button>
                <div class="log-box" id="manual_log">等待操作</div>
            </div>
            
            <div id="config" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择账号</label>
                    <select id="config_account" class="form-control" onchange="loadAccountConfig()">
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                
                <div class="form-group">
                    <label class="form-label">专属提示词</label>
                    <textarea id="config_prompt" class="form-control"></textarea>
                </div>
                
                <div class="form-group">
                    <label class="form-label">生成模型</label>
                    <select id="config_model" class="form-control">
                        <option value="zhipu">智谱 GLM-4-Flash</option>
                        <option value="deepseek">DeepSeek V4-Flash</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label class="form-label">每日限额</label>
                    <input type="number" id="config_daily_limit" class="form-control" min="1" value="8">
                </div>
                
                <div class="form-group">
                    <label class="form-label">自动间隔（分钟）</label>
                    <input type="number" id="config_interval" class="form-control" min="5" value="60">
                </div>
                
                <button class="btn btn-primary" onclick="saveAccountConfig()" style="width:100%">保存配置</button>
                <div class="log-box" id="config_log">选择账号加载配置</div>
            </div>
            
            <div id="records" class="tab-content">
                <div class="form-group">
                    <select id="record_account" class="form-control">
                        <option value="">所有账号</option>
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                    <input type="date" id="record_date" class="form-control" style="margin-top:8px" value="{{today}}">
                    <button class="btn btn-secondary" onclick="loadRecords()" style="margin-top:8px">查询</button>
                </div>
                <div class="records-list" id="records_list"></div>
            </div>
        </div>
    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick="switchTab('${tabId}')"]`).classList.add('active');
            document.getElementById(tabId).classList.add('active');
        }
        
        function loadAccountStatus() {
            const acc = document.getElementById('auto_account_selector').value;
            if(!acc) return;
            fetch('/api/auto/status?account='+acc)
                .then(r=>r.json())
                .then(d=>{
                    document.getElementById('auto_account_actions').style.display='block';
                    document.getElementById('auto_account_name').textContent=acc;
                });
        }
        
        function startAuto(){
            const acc=document.getElementById('auto_account_selector').value;
            fetch('/api/auto/start?account='+acc).then(r=>r.json()).then(d=>alert('启动成功'));
        }
        
        function stopAuto(){
            const acc=document.getElementById('auto_account_selector').value;
            fetch('/api/auto/stop?account='+acc).then(r=>r.json()).then(d=>alert('已停止'));
        }
        
        function showAccountConfig(acc){}
        
        function loadAccountConfig(){
            const name=document.getElementById('config_account').value;
            fetch('/api/config/load?account='+name)
                .then(r=>r.json())
                .then(c=>{
                    document.getElementById('config_prompt').value=c.prompt||'';
                    document.getElementById('config_model').value=c.model_type||'zhipu';
                    document.getElementById('config_daily_limit').value=c.daily_limit||8;
                    document.getElementById('config_interval').value=c.auto_interval||60;
                });
        }
        
        function saveAccountConfig(){
            const data={
                account:document.getElementById('config_account').value,
                prompt:document.getElementById('config_prompt').value,
                model_type:document.getElementById('config_model').value,
                daily_limit:parseInt(document.getElementById('config_daily_limit').value),
                auto_interval:parseInt(document.getElementById('config_interval').value)
            };
            fetch('/api/config/save',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify(data)
            }).then(r=>r.json()).then(d=>{
                document.getElementById('config_log').textContent='✅ 保存成功';
            });
        }
        
        function autoSelectSymbol(){
            fetch('/api/topic/auto_symbol').then(r=>r.json()).then(d=>{
                document.getElementById('manual_symbol').value=d.symbol;
            });
        }
        
        function generateFullTopic(){
            const s=document.getElementById('manual_symbol').value;
            fetch('/api/topic/generate?symbol='+s).then(r=>r.json()).then(d=>{
                document.getElementById('manual_topic').value=JSON.stringify(d.topic,null,2);
            });
        }
        
        function generateAIContent(){
            const t=document.getElementById('manual_topic').value;
            const accKey=document.getElementById('manual_account').value;
            const accName=document.querySelector('#manual_account option:checked').dataset.name;
            fetch('/api/ai/generate',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({topic:JSON.parse(t),account_name:accName})
            }).then(r=>r.json()).then(d=>{
                document.getElementById('manual_content').value=d.content;
            });
        }
        
        function submitPost(){
            const key=document.getElementById('manual_account').value;
            const name=document.querySelector('#manual_account option:checked').dataset.name;
            const content=document.getElementById('manual_content').value;
            const symbol=document.getElementById('manual_symbol').value;
            fetch('/api/post/manual',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({account_key:key,account_name:name,content:content,symbol:symbol})
            }).then(r=>r.json()).then(d=>{
                document.getElementById('manual_log').textContent='✅ 发布成功';
            });
        }
        
        function loadRecords(){
            const acc=document.getElementById('record_account').value;
            const date=document.getElementById('record_date').value;
            fetch('/api/records/load?account='+acc+'&date='+date)
                .then(r=>r.json())
                .then(d=>{
                    const el=document.getElementById('records_list');
                    el.innerHTML='';
                    d.records.forEach(r=>{
                        const div=document.createElement('div');
                        div.className='record-item';
                        div.innerHTML=`<div>${r.symbol} | ${r.account} | ${r.time}</div><div>${r.content}</div>`;
                        el.appendChild(div);
                    });
                });
        }
        
        window.onload=function(){
            loadAccountConfig();
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
