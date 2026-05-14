from flask import Flask, render_template_string, request, jsonify, Response, make_response
import os
import json
import datetime
import threading
import time
import urllib.parse

# ===================== 【关键：导入调度模块】 =====================
from schedule_core import can_publish, get_random_interval, inc_published
# ==================================================================

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
            "schedule": acc_config.get("schedule", {}),  # 新增：带回发文计划配置
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

# 【修改：保存配置时支持 schedule】
def save_account_prompt(account_name, prompt, daily_limit, auto_interval, model_type="zhipu", schedule=None):
    prompts = load_json(PROMPT_FILE)
    data = {
        "prompt": prompt,
        "model_type": model_type,
        "daily_limit": int(daily_limit),
        "auto_interval": int(auto_interval)
    }
    if schedule is not None:
        data["schedule"] = schedule
    prompts[account_name] = data
    save_json(PROMPT_FILE, prompts)

# ======================== 发文记录管理 ========================
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

# ======================== 自动发文核心 【已修改：接入调度模块】 ========================
def auto_publisher_worker(account_name):
    while True:
        with status_lock:
            if not account_running_status.get(account_name, False):
                break
        current_acc = get_account_by_name(account_name)
        if not current_acc:
            time.sleep(10)
            continue

        # ===================== 核心修改：调用调度模块判断是否可发文 =====================
        if not can_publish(account_name, current_acc):
            time.sleep(60)
            continue
        # ============================================================================

        try:
            from topic_main import run_topic
            topic = run_topic()
            if not topic:
                time.sleep(10)
                continue
            from ai_core import generate_content
            content, _ = generate_content(
                topic,
                api_key=ZHIPU_API_KEY if current_acc["model_type"] == "zhipu" else DEEPSEEK_API_KEY,
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
                # 【新增：发文成功后计数+1】
                inc_published(account_name)
            # ===================== 修改：用随机间隔代替固定间隔 =====================
            schedule_cfg = current_acc.get("schedule", {})
            sleep_min = get_random_interval(
                schedule_cfg.get("interval_min", 8),
                schedule_cfg.get("interval_max", 25)
            )
            time.sleep(sleep_min * 60)
            # ======================================================================
        except Exception as e:
            print("自动异常：", e)
            time.sleep(10)

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

# ======================== 网页模板【关键：账号配置页已加入发文计划设置】 ========================
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
            --gray: #8e8e93;
            --light-gray: #f2f2f7;
            --border: #e5e5ea;
            --text: #1d1d1f;
            --bg: #ffffff;
        }
        * {
            margin:0;padding:0;box-sizing:border-box;
            font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
        }
        body{background:var(--light-gray);color:var(--text);padding:16px;line-height:1.5;}
        .container{max-width:800px;margin:0 auto;}
        .card{background:var(--bg);border-radius:16px;box-shadow:0 2px 10px rgba(0,0,0,0.05);padding:24px;margin-bottom:16px;}
        .header{display:flex;align-items:center;margin-bottom:20px;}
        .header h1{font-size:22px;font-weight:600;margin-right:12px;}
        .header .badge{background:var(--primary);color:white;font-size:12px;padding:2px 8px;border-radius:10px;}
        .tabs{display:flex;gap:8px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:8px;}
        .tab-btn{background:none;border:none;padding:8px 16px;font-size:15px;font-weight:500;color:var(--gray);border-radius:8px;cursor:pointer;transition:all 0.2s;}
        .tab-btn.active{color:var(--primary);background:rgba(0,122,255,0.1);}
        .tab-content{display:none;}
        .tab-content.active{display:block;}
        .form-group{margin-bottom:16px;}
        .form-label{display:block;font-size:14px;font-weight:500;margin-bottom:8px;color:var(--text);}
        .form-control{width:100%;padding:12px 16px;border:1px solid var(--border);border-radius:12px;font-size:15px;transition:border 0.2s;}
        .form-control:focus{outline:none;border-color:var(--primary);}
        textarea.form-control{min-height:120px;resize:vertical;line-height:1.5;}
        .btn{display:inline-flex;align-items:center;justify-content:center;padding:12px 24px;border:none;border-radius:12px;font-size:15px;font-weight:500;cursor:pointer;transition:all 0.2s;gap:8px;}
        .btn-primary{background:var(--primary);color:white;}
        .btn-primary:hover{background:#0066cc;}
        .btn-success{background:var(--success);color:white;}
        .btn-danger{background:var(--danger);color:white;}
        .btn-secondary{background:var(--light-gray);color:var(--text);}
        .btn-secondary:hover{background:#e5e5ea;}
        .account-selector{width:100%;margin-bottom:16px;}
        .account-actions-wrapper{display:flex;gap:8px;margin-top:8px;}
        .account-action-btn{flex:1;padding:8px 12px;font-size:14px;}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px;}
        .stat-card{background:var(--light-gray);border-radius:12px;padding:16px;text-align:center;cursor:pointer;transition:all 0.2s;}
        .stat-card:hover{transform:scale(1.02);box-shadow:0 2px 8px rgba(0,0,0,0.1);}
        .stat-card.active{border:2px solid var(--primary);background:rgba(0,122,255,0.05);}
        .stat-value{font-size:24px;font-weight:600;margin-bottom:4px;}
        .stat-label{font-size:12px;color:var(--gray);}
        .config-detail{background:rgba(0,122,255,0.05);border-left:4px solid var(--primary);padding:16px;border-radius:0 12px 12px 0;margin-bottom:16px;display:none;}
        .config-detail.active{display:block;}
        .log-box{background:var(--light-gray);border-radius:12px;padding:16px;min-height:80px;font-size:14px;white-space:pre-wrap;margin-top:16px;}
        .records-list{max-height:400px;overflow-y:auto;gap:12px;display:flex;flex-direction:column;}
        .record-item{background:var(--light-gray);border-radius:12px;padding:16px;}
        .record-header{display:flex;justify-content:space-between;margin-bottom:8px;font-size:14px;}
        .record-symbol{font-weight:600;color:var(--primary);}
        .record-time{color:var(--gray);font-size:12px;}
        .record-content{font-size:14px;line-height:1.5;}
        .delete-section{margin-top:16px;padding-top:16px;border-top:1px solid var(--border);}
        .schedule-group{background:rgba(0,122,255,0.05);border-radius:12px;padding:16px;margin-bottom:16px;}
        .schedule-title{font-weight:600;margin-bottom:12px;}
        .schedule-row{display:flex;gap:12px;flex-wrap:wrap;}
        .schedule-col{flex:1;min-width:120px;}
        @media(max-width:480px){.card{padding:16px;}.account-actions-wrapper{flex-direction:column;}.schedule-row{flex-direction:column;}}
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
                <button class="tab-btn active" onclick="switchTab('auto')"><i class="fa fa-robot"></i> 自动模式</button>
                <button class="tab-btn" onclick="switchTab('manual')"><i class="fa fa-hand-pointer-o"></i> 手动模式</button>
                <button class="tab-btn" onclick="switchTab('config')"><i class="fa fa-cog"></i> 账号配置</button>
                <button class="tab-btn" onclick="switchTab('records')"><i class="fa fa-history"></i> 发文记录</button>
            </div>
            <!-- 自动模式 -->
            <div id="auto" class="tab-content active">
                <div class="form-label">选择要操作的账号</div>
                <select id="auto_account_selector" class="form-control account-selector" onchange="loadAccountStatus()">
                    <option value="">请选择账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                <div id="auto_account_actions" style="display: none;">
                    <div style="padding:16px;background:var(--light-gray);border-radius:12px;margin-bottom:16px;">
                        <div id="auto_account_name" style="font-weight:600;margin-bottom:8px;"></div>
                        <div id="auto_account_status"></div>
                    </div>
                    <div class="account-actions-wrapper">
                        <button id="auto_start_btn" class="btn btn-success account-action-btn" onclick="startAuto()"><i class="fa fa-play"></i> 启动自动发文</button>
                        <button id="auto_stop_btn" class="btn btn-danger account-action-btn" onclick="stopAuto()"><i class="fa fa-stop"></i> 停止自动发文</button>
                    </div>
                </div>
                <div class="form-label" style="margin-top:20px;">今日发文统计（点击查看账号配置）</div>
                <div class="stats-grid" id="today_stats">
                    {% for acc_name, stat in today_stats.items() %}
                    <div class="stat-card" id="stat_{{acc_name}}" onclick="showAccountConfig('{{acc_name}}')">
                        <div class="stat-value">{{stat.count}}</div>
                        <div class="stat-label">{{acc_name}}</div>
                        <div class="stat-label">自动: {{stat.auto_count}} | 手动: {{stat.manual_count}}</div>
                        <div class="stat-label">剩余: {{stat.remaining}}/{{stat.limit}}</div>
                        {% if stat.running %}
                        <div class="stat-label" style="color:var(--success);">运行中</div>
                        {% else %}
                        <div class="stat-label" style="color:var(--gray);">已停止</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                <div class="config-detail" id="account_config_detail">
                    <div id="config_detail_content"></div>
                </div>
            </div>
            <!-- 手动模式 -->
            <div id="manual" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择发文账号</label>
                    <select id="manual_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}} (今日剩余:{{today_stats[acc.name].remaining}}/{{today_stats[acc.name].limit}})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">交易对</label>
                    <input type="text" id="manual_symbol" class="form-control" placeholder="如 BTCUSDT">
                </div>
                <div style="display:flex;gap:8px;margin-bottom:16px;">
                    <button class="btn btn-secondary" onclick="autoSelectSymbol()">自动选交易对</button>
                    <button class="btn btn-secondary" onclick="generateFullTopic()">生成完整分析</button>
                </div>
                <div class="form-group">
                    <label class="form-label">话题分析（可编辑）</label>
                    <textarea id="manual_topic" class="form-control"></textarea>
                </div>
                <button class="btn btn-secondary" onclick="generateAIContent()" style="width:100%;margin-bottom:16px;">生成发文内容</button>
                <div class="form-group">
                    <label class="form-label">最终内容（可编辑）</label>
                    <textarea id="manual_content" class="form-control"></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitPost()" style="width:100%">确认发文</button>
                <div class="log-box" id="manual_log">等待操作...</div>
            </div>
            <!-- 账号配置【关键：已新增发文计划设置区域】 -->
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
                    <label class="form-label">每日发文限额</label>
                    <input type="number" id="config_daily_limit" class="form-control" min="1" value="8">
                </div>
                <div class="form-group">
                    <label class="form-label">自动发文间隔（分钟）</label>
                    <input type="number" id="config_interval" class="form-control" min="5" value="60">
                </div>
                <!-- 【新增：发文计划高级设置】 -->
                <div class="schedule-group">
                    <div class="schedule-title">📅 发文计划高级设置（自定义时段/条数/间隔）</div>
                    <div class="schedule-row">
                        <div class="schedule-col">
                            <label class="form-label">每日发文区间（最小）</label>
                            <input type="number" id="cfg_schedule_daily_min" class="form-control" min="1" value="10">
                        </div>
                        <div class="schedule-col">
                            <label class="form-label">每日发文区间（最大）</label>
                            <input type="number" id="cfg_schedule_daily_max" class="form-control" min="1" value="20">
                        </div>
                        <div class="schedule-col">
                            <label class="form-label">间隔区间（最小分钟）</label>
                            <input type="number" id="cfg_schedule_interval_min" class="form-control" min="2" value="8">
                        </div>
                        <div class="schedule-col">
                            <label class="form-label">间隔区间（最大分钟）</label>
                            <input type="number" id="cfg_schedule_interval_max" class="form-control" min="5" value="25">
                        </div>
                    </div>
                    <div class="schedule-row" style="margin-top:12px;">
                        <div class="schedule-col">
                            <label class="form-label">活跃开始时间</label>
                            <input type="time" class="form-control" id="cfg_schedule_active_start" value="08:00">
                        </div>
                        <div class="schedule-col">
                            <label class="form-label">活跃结束时间</label>
                            <input type="time" class="form-control" id="cfg_schedule_active_end" value="22:00">
                        </div>
                    </div>
                    <div class="form-text text-muted" style="margin-top:8px;font-size:12px;color:var(--gray);">
                        说明：设置后，系统会在你指定的时段内、按区间随机发文数量和间隔；支持跨零点时段（如 22:00 - 02:00）
                    </div>
                </div>
                <!-- 新增结束 -->
                <button class="btn btn-primary" onclick="saveAccountConfig()" style="width:100%">保存配置</button>
                <div class="log-box" id="config_log">选择账号后加载配置...</div>
            </div>
            <!-- 发文记录 -->
            <div id="records" class="tab-content">
                <div class="form-group">
                    <label class="form-label">筛选条件</label>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <select id="record_account" class="form-control" style="flex:1;min-width:120px;">
                            <option value="">所有账号</option>
                            {% for acc in accounts %}
                            <option value="{{acc.name}}">{{acc.name}}</option>
                            {% endfor %}
                        </select>
                        <input type="date" id="record_date" class="form-control" value="{{today}}">
                        <button class="btn btn-secondary" onclick="loadRecords()">查询</button>
                        <button class="btn btn-secondary" onclick="exportRecords()">导出</button>
                    </div>
                </div>
                <div class="records-list" id="records_list"></div>
                <div class="delete-section">
                    <div class="form-label">删除记录</div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <select id="delete_account" class="form-control" style="flex:1;min-width:120px;">
                            <option value="">所有账号</option>
                            {% for acc in accounts %}
                            <option value="{{acc.name}}">{{acc.name}}</option>
                            {% endfor %}
                        </select>
                        <input type="date" id="delete_date" class="form-control" placeholder="选择日期">
                        <button class="btn btn-danger" onclick="deleteSelectedRecords()">删除选中记录</button>
                        <button class="btn btn-danger" onclick="deleteAllRecords()">删除所有记录</button>
                    </div>
                    <div class="log-box" id="delete_log"></div>
                </div>
            </div>
        </div>
    </div>
    <script>
        // 修复后的JS代码，确保所有函数都定义
        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick="switchTab('${tabId}')"]`).classList.add('active');
            document.getElementById(tabId).classList.add('active');
            if(tabId === 'auto') refreshAutoPage();
            if(tabId === 'config') loadAccountConfig();
        }
        
        function loadAccountStatus() {
            const acc = document.getElementById('auto_account_selector').value;
            if(!acc) {
                document.getElementById('auto_account_actions').style.display = 'none';
                return;
            }
            fetch(`/api/auto/status?account=${acc}`)
                .then(r => r.json())
                .then(d => {
                    document.getElementById('auto_account_actions').style.display = 'block';
                    document.getElementById('auto_account_name').textContent = acc;
                    fetch(`/api/stats/today?account=${acc}`)
                        .then(r => r.json())
                        .then(s => {
                            const st = d.running ? `<span style="color:var(--success);">运行中</span>` : `<span style="color:var(--gray);">已停止</span>`;
                            document.getElementById('auto_account_status').innerHTML = `${st} | 限额:${s.limit} | 间隔:${d.auto_interval}分钟 | 已发:${s.count}(${s.auto_count}/${s.manual_count})`;
                            document.getElementById('auto_start_btn').disabled = d.running;
                            document.getElementById('auto_stop_btn').disabled = !d.running;
                        });
                });
        }
        
        function startAuto() {
            const acc = document.getElementById('auto_account_selector').value;
            fetch(`/api/auto/start?account=${acc}`)
                .then(r => r.json())
                .then(d => {
                    alert(d.msg);
                    refreshAutoPage();
                });
        }
        
        function stopAuto() {
            const acc = document.getElementById('auto_account_selector').value;
            fetch(`/api/auto/stop?account=${acc}`)
                .then(r => r.json())
                .then(d => {
                    alert(d.msg);
                    refreshAutoPage();
                });
        }
        
        function showAccountConfig(acc) {
            document.querySelectorAll('.stat-card').forEach(c => c.classList.remove('active'));
            document.getElementById('stat_'+acc).classList.add('active');
            fetch(`/api/config/load?account=${acc}`)
                .then(r => r.json())
                .then(c => {
                    fetch(`/api/auto/last_run?account=${acc}`)
                        .then(r => r.json())
                        .then(l => {
                            fetch(`/api/stats/today?account=${acc}`)
                                .then(r => r.json())
                                .then(s => {
                                    let h = `<div><strong>提示词：</strong>${c.prompt||'无'}</div><div><strong>模型：</strong>${c.model_type}</div><div><strong>限额：</strong>${c.daily_limit}</div><div><strong>间隔：</strong>${c.auto_interval}</div><div><strong>今日：</strong>${s.count}/${s.limit}</div>`;
                                    document.getElementById('config_detail_content').innerHTML = h;
                                    document.getElementById('account_config_detail').classList.add('active');
                                });
                        });
                });
        }
        
        function refreshAutoPage() {
            fetch('/api/auto/refresh')
                .then(r => r.json())
                .then(d => {
                    let h = '';
                    for(const [n,s] of Object.entries(d.today_stats)) {
                        h += `<div class="stat-card" onclick="showAccountConfig('${n}')"><div class="stat-value">${s.count}</div><div class="stat-label">${n}</div><div class="stat-label">自动:${s.auto_count} 手动:${s.manual_count}</div><div class="stat-label">剩余:${s.remaining}/${s.limit}</div>${s.running?'<div class="stat-label" style="color:var(--success);">运行中</div>':'<div class="stat-label" style="color:var(--gray);">已停止</div>'}</div>`;
                    }
                    document.getElementById('today_stats').innerHTML = h;
                });
        }
        
        function loadAccountConfig() {
            const a = document.getElementById('config_account').value;
            fetch(`/api/config/load?account=${a}`)
                .then(r => r.json())
                .then(c => {
                    document.getElementById('config_prompt').value = c.prompt || '';
                    document.getElementById('config_model').value = c.model_type || 'zhipu';
                    document.getElementById('config_daily_limit').value = c.daily_limit || 8;
                    document.getElementById('config_interval').value = c.auto_interval || 60;
                    // 【新增：加载发文计划配置】
                    const s = c.schedule || {};
                    document.getElementById('cfg_schedule_daily_min').value = s.daily_min || 10;
                    document.getElementById('cfg_schedule_daily_max').value = s.daily_max || 20;
                    document.getElementById('cfg_schedule_interval_min').value = s.interval_min || 8;
                    document.getElementById('cfg_schedule_interval_max').value = s.interval_max || 25;
                    document.getElementById('cfg_schedule_active_start').value = s.active_start || '08:00';
                    document.getElementById('cfg_schedule_active_end').value = s.active_end || '22:00';
                    document.getElementById('config_log').textContent = '已加载';
                });
        }
        
        function saveAccountConfig() {
            const a = document.getElementById('config_account').value;
            const p = document.getElementById('config_prompt').value;
            const m = document.getElementById('config_model').value;
            const dl = parseInt(document.getElementById('config_daily_limit').value);
            const ai = parseInt(document.getElementById('config_interval').value);
            // 【新增：读取发文计划配置】
            const schedule = {
                daily_min: parseInt(document.getElementById('cfg_schedule_daily_min').value),
                daily_max: parseInt(document.getElementById('cfg_schedule_daily_max').value),
                interval_min: parseInt(document.getElementById('cfg_schedule_interval_min').value),
                interval_max: parseInt(document.getElementById('cfg_schedule_interval_max').value),
                active_start: document.getElementById('cfg_schedule_active_start').value,
                active_end: document.getElementById('cfg_schedule_active_end').value
            };
            fetch('/api/config/save', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({
                    account:a,
                    prompt:p,
                    model_type:m,
                    daily_limit:dl,
                    auto_interval:ai,
                    schedule:schedule
                })
            }).then(r => r.json()).then(d => {
                document.getElementById('config_log').textContent = d.success ? '✅保存成功' : '❌保存失败';
                refreshAutoPage();
            });
        }
        
        function autoSelectSymbol() {
            fetch('/api/manual/auto_symbol')
                .then(r => r.json())
                .then(d => {
                    document.getElementById('manual_symbol').value = d.symbol;
                });
        }
        
        function generateFullTopic() {
            const s = document.getElementById('manual_symbol').value;
            fetch(`/api/manual/full_topic?symbol=${s}`)
                .then(r => r.json())
                .then(d => {
                    document.getElementById('manual_topic').value = d.topic;
                });
        }
        
        function generateAIContent() {
            const t = document.getElementById('manual_topic').value;
            const k = document.getElementById('manual_account').value;
            fetch('/api/manual/generate_ai', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({topic:t,account_key:k})
            }).then(r => r.text()).then(c => {
                document.getElementById('manual_content').value = c;
            });
        }
        
        function submitPost() {
            const k = document.getElementById('manual_account').value;
            const c = document.getElementById('manual_content').value;
            const s = document.getElementById('manual_symbol').value;
            const n = document.querySelector(`#manual_account option[value="${k}"]`).dataset.name;
            fetch('/api/manual/post', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({account_key:k,content:c,symbol:s})
            }).then(r => r.json()).then(d => {
                document.getElementById('manual_log').textContent = d.success ? '✅发文成功' : '❌发文失败';
            });
        }
        
        function loadRecords() {
            const a = document.getElementById('record_account').value;
            const d = document.getElementById('record_date').value;
            fetch(`/api/records?account=${a}&date=${d}`)
                .then(r => r.json())
                .then(rs => {
                    let h = '';
                    rs.forEach(r => {
                        h += `<div class="record-item"><div class="record-header"><span class="record-symbol">${r.symbol}</span><span>${r.account}</span><span class="record-time">${r.time}</span></div><div class="record-content">${r.content}</div></div>`;
                    });
                    document.getElementById('records_list').innerHTML = h;
                });
        }
        
        function exportRecords() {
            const a = document.getElementById('record_account').value;
            const d = document.getElementById('record_date').value;
            window.open(`/api/records/export?account=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`);
        }
        
        function deleteSelectedRecords() {
            const a = document.getElementById('delete_account').value;
            const d = document.getElementById('delete_date').value;
            fetch(`/api/records/delete?account=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`, {method:'POST'})
                .then(r => r.json())
                .then(d => {
                    document.getElementById('delete_log').textContent = '已删除'+d.deleted_count+'条';
                    loadRecords();
                });
        }
        
        function deleteAllRecords() {
            fetch('/api/records/delete?all=true', {method:'POST'})
                .then(r => r.json())
                .then(d => {
                    document.getElementById('delete_log').textContent = '已删除全部';
                    loadRecords();
                });
        }
        
        // 页面加载初始化
        window.onload = function() {
            refreshAutoPage();
            loadRecords();
        };
    </script>
</body>
</html>
"""

# ======================== 接口 【已修改：支持保存 schedule】 ========================
@app.route('/')
def index():
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    today = str(datetime.date.today())
    return render_template_string(UI_TEMPLATE, accounts=accounts, today_stats=today_stats, today=today)

@app.route('/api/auto/start')
def auto_start():
    a = request.args.get('account')
    ok = start_account_auto_publish(a)
    return jsonify({'success': ok, 'msg': '已启动' if ok else '已运行'})

@app.route('/api/auto/stop')
def auto_stop():
    a = request.args.get('account')
    stop_account_auto_publish(a)
    return jsonify({'success': True, 'msg': '已停止'})

@app.route('/api/auto/status')
def auto_status():
    a = request.args.get('account')
    acc = get_account_by_name(a) or {}
    return jsonify({
        'running': account_running_status.get(a, False),
        'daily_limit': acc.get('daily_limit', DEFAULT_DAILY_LIMIT),
        'auto_interval': acc.get('auto_interval', DEFAULT_AUTO_INTERVAL)
    })

@app.route('/api/auto/refresh')
def auto_refresh():
    return jsonify({
        'accounts': get_all_accounts(),
        'today_stats': get_today_stats()
    })

@app.route('/api/auto/last_run')
def auto_last_run():
    a = request.args.get('account')
    cfg = load_json(CONFIG_FILE)
    return jsonify({
        'last_run': cfg.get(f'{a}_last_run', ''),
        'last_auto_run': cfg.get(f'{a}_last_auto_run', ''),
        'last_manual_run': cfg.get(f'{a}_last_manual_run', '')
    })

@app.route('/api/stats/today')
def stats_today():
    a = request.args.get('account')
    return jsonify(get_today_stats(a))

@app.route('/api/config/load')
def config_load():
    a = request.args.get('account')
    acc = get_account_by_name(a) or {}
    return jsonify({
        'prompt': acc.get('prompt', ''),
        'model_type': acc.get('model_type', 'zhipu'),
        'daily_limit': acc.get('daily_limit', DEFAULT_DAILY_LIMIT),
        'auto_interval': acc.get('auto_interval', DEFAULT_AUTO_INTERVAL),
        'schedule': acc.get('schedule', {})  # 【新增：返回 schedule 配置】
    })

@app.route('/api/config/save', methods=['POST'])
def config_save():
    d = request.json
    # 【修改：接收并保存 schedule】
    save_account_prompt(
        d['account'], 
        d['prompt'], 
        d['daily_limit'], 
        d['auto_interval'], 
        d['model_type'],
        d.get('schedule')
    )
    return jsonify({'success': True})

@app.route('/api/manual/auto_symbol')
def manual_auto_symbol():
    try:
        from topic_main import run_topic
        topic = run_topic()
        symbol = topic.get("symbol", "BTCUSDT")
        return jsonify({'success': True, 'symbol': symbol})
    except:
        return jsonify({'success': True, 'symbol': "BTCUSDT"})

@app.route('/api/manual/full_topic')
def manual_full_topic():
    symbol = request.args.get('symbol', '').strip()
    from topic_main import run_topic
    topic = run_topic(target_symbol=symbol)
    return jsonify({'success': True, 'topic': topic.get('text', '')})

@app.route('/api/manual/generate_ai', methods=['POST'])
def manual_generate_ai():
    d = request.json
    t = d['topic']
    k = d['account_key']
    acc = get_account_by_key(k)
    from ai_core import generate_content
    api_key = ZHIPU_API_KEY if acc.get('model_type') == 'zhipu' else DEEPSEEK_API_KEY
    c, _ = generate_content(
        {'text': t},
        api_key=api_key,
        model_type=acc.get('model_type', 'zhipu'),
        custom_prompt=acc.get('prompt', '')
    )
    return c or ''

@app.route('/api/manual/post', methods=['POST'])
def manual_post():
    d = request.json
    k = d['account_key']
    c = d['content']
    s = d['symbol']
    acc = get_account_by_key(k)
    from post_main import post_content
    ok, msg, pid = post_content(c, k)
    pid = str(pid) if pid else '未知'
    if ok:
        save_post_record('manual', acc['name'], s, c, pid)
        cfg = load_json(CONFIG_FILE)
        cfg[f"{acc['name']}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg[f"{acc['name']}_last_manual_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_json(CONFIG_FILE, cfg)
    return jsonify({'success': ok, 'post_id': pid, 'msg': msg})

@app.route('/api/records')
def records():
    a = request.args.get('account')
    d = request.args.get('date')
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if a and r['account'] != a:
            continue
        if d and r['date'] != d:
            continue
        res.append(r)
    return jsonify(res)

@app.route('/api/records/export')
def records_export():
    a = request.args.get('account')
    d = request.args.get('date')
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if a and r['account'] != a:
            continue
        if d and r['date'] != d:
            continue
        res.append(r)
    def csv_escape(s):
        if isinstance(s, str):
            return s.replace('"', '""')
        return s
    csv = '模式,账号,日期,时间,交易对,ID,状态,内容\n'
    for r in res:
        csv += (
            f"{csv_escape(r['mode'])},{csv_escape(r['account'])},{csv_escape(r['date'])},{csv_escape(r['time'])},{csv_escape(r['symbol'])},{csv_escape(r['post_id'])},{csv_escape(r['status'])},\"{csv_escape(r['content'])}\"\n"
        )
    response = make_response(csv)
    response.headers["Content-Type"] = "text/csv;charset=utf-8"
    response.headers["Content-Disposition"] = "attachment;filename=records.csv"
    return response

@app.route('/api/records/delete', methods=['POST'])
def records_delete():
    a = request.args.get('account')
    d = request.args.get('date')
    all_records = request.args.get('all') == 'true'
    cnt = delete_records(a, d, all_records)
    return jsonify({'success': True, 'deleted_count': cnt})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
