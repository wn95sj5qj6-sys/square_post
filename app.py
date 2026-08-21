import os
import sys
import time
import json
import random
import threading
import datetime
import traceback
from flask import Flask, request, jsonify, render_template_string, make_response
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

# 强制终端实时刷新输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# ======================== 自动加载本地 .env ========================
def load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(env_path):
        try:
            with open(env_path, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip().strip("'\"")
        except Exception as e:
            print(f"⚠️ 读取 .env 失败: {e}")

load_local_env()

# 导入调度核心
from schedule_core import (
    can_publish,
    inc_auto_published,
    inc_manual_published,
    get_daily_stats,
    set_daily_stats,
    get_random_interval
)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# ======================== 基础配置与文件定义 ========================
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

DATA_DIR = "data"
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
DB_FILE = os.path.join(DATA_DIR, "records.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
PROMPT_FILE = os.path.join(DATA_DIR, "prompts.json")
GLOBAL_CONFIG_FILE = os.path.join(DATA_DIR, "global_config.json")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

account_running_status = {}
status_lock = threading.Lock()

def get_api_key_by_model(model_type):
    load_local_env()
    if model_type == "zhipu":
        return os.getenv("ZHIPU_API_KEY", "").strip()
    return os.getenv("DEEPSEEK_API_KEY", "").strip()

def load_json(file_path, default=None):
    if default is None:
        default = {}
    try:
        if not os.path.exists(file_path):
            return default
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def save_json(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存 JSON 异常 ({file_path}): {e}")

def recover_counts_from_records():
    today = str(datetime.date.today())
    db = load_json(DB_FILE, [])
    auto_counts = {}
    manual_counts = {}
    for record in db:
        if record.get("date") == today and record.get("status") == "success":
            acc = record.get("account")
            mode = record.get("mode")
            if not acc:
                continue
            if mode == "auto":
                auto_counts[acc] = auto_counts.get(acc, 0) + 1
            elif mode in ["manual", "article", "dynamic"]:
                manual_counts[acc] = manual_counts.get(acc, 0) + 1
    accounts = get_all_accounts()
    for acc in accounts:
        acc_name = acc["name"]
        auto_pub = auto_counts.get(acc_name, 0)
        manual_pub = manual_counts.get(acc_name, 0)
        try:
            set_daily_stats(acc_name, acc, auto_published=auto_pub, manual_published=manual_pub)
        except Exception:
            pass
    print(f"[启动恢复] 已恢复今日发文计数: auto={auto_counts}, manual={manual_counts}")

def get_accounts_from_env():
    load_local_env()
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
            "schedule": acc_config.get("schedule", {}),
            "running": running
        })
    return accounts

def get_account_by_name(name):
    for acc in get_all_accounts():
        if acc["name"] == name:
            return acc
    return None

def get_account_by_key(key):
    for acc in get_all_accounts():
        if acc["key"] == key:
            return acc
    return None

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
    if len(db) > 1000:
        db = db[-1000:]
    save_json(DB_FILE, db)

def get_today_stats(account_name=None):
    accounts = get_all_accounts()
    stats = {}
    for acc in accounts:
        acc_name = acc["name"]
        try:
            auto_target, auto_pub, manual_pub = get_daily_stats(acc_name, acc)
        except Exception:
            auto_target, auto_pub, manual_pub = 10, 0, 0
        stats[acc_name] = {
            "auto_target": auto_target,
            "auto_count": auto_pub,
            "manual_count": manual_pub,
            "running": acc["running"]
        }
    if account_name:
        return stats.get(account_name, {"auto_target": 0, "auto_count": 0, "manual_count": 0, "running": False})
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

def auto_publisher_worker(account_name):
    while True:
        with status_lock:
            if not account_running_status.get(account_name, False):
                break
        current_acc = get_account_by_name(account_name)
        if not current_acc or not can_publish(account_name, current_acc):
            time.sleep(30)
            continue
        
        try:
            from topic_main import run_topic
            topic = run_topic()
            if not topic:
                time.sleep(10)
                continue
            from ai_core import generate_content
            api_key = get_api_key_by_model(current_acc["model_type"])
            content, _ = generate_content(
                topic,
                api_key=api_key,
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
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cfg[f"{account_name}_last_run"] = now_str
                cfg[f"{account_name}_last_auto_run"] = now_str
                save_json(CONFIG_FILE, cfg)
                inc_auto_published(account_name)
            
            schedule_cfg = current_acc.get("schedule", {})
            sleep_min = get_random_interval(
                schedule_cfg.get("interval_min", 8),
                schedule_cfg.get("interval_max", 25)
            )
            time.sleep(sleep_min * 60)
        except Exception as e:
            print("自动发文异常：", e)
            time.sleep(10)

def start_account_auto_publish(account_name):
    with status_lock:
        if account_running_status.get(account_name, False):
            return False
        account_running_status[account_name] = True
    t = threading.Thread(target=auto_publisher_worker, args=(account_name,), daemon=True)
    t.start()
    return True

def stop_account_auto_publish(account_name):
    with status_lock:
        account_running_status[account_name] = False
    return True

# ======================== 亮色科技侧边栏 UI 模板 ========================
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Binance Square Hub</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
    <style>
        :root {
            --bg-body: #F4F6F9;
            --bg-card: #FFFFFF;
            --bg-subtle: #F8FAFC;
            --border-color: #E2E8F0;
            --border-focus: #F0B90B;
            --primary: #F0B90B;
            --primary-hover: #E0A800;
            --primary-light: #FFF9E6;
            --text-title: #0F172A;
            --text-body: #334155;
            --text-muted: #64748B;
            --text-light: #94A3B8;
            --success: #10B981;
            --success-bg: #ECFDF5;
            --danger: #EF4444;
            --danger-bg: #FEF2F2;
            --shadow-sm: 0 1px 3px rgba(0,0,0,0.05), 0 1px 2px rgba(0,0,0,0.03);
            --shadow-md: 0 4px 6px -1px rgba(0,0,0,0.05), 0 2px 4px -1px rgba(0,0,0,0.03);
            --shadow-lg: 0 10px 15px -3px rgba(0,0,0,0.05), 0 4px 6px -2px rgba(0,0,0,0.02);
        }

        * { margin:0; padding:0; box-sizing:border-box; font-family:'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,sans-serif; }
        
        body {
            background-color: var(--bg-body);
            color: var(--text-body);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            padding: 24px 16px;
        }

        .layout-wrapper {
            display: flex;
            width: 100%;
            max-width: 1100px;
            gap: 20px;
            align-items: flex-start;
        }

        /* 左侧固定侧边栏导航 */
        .sidebar {
            width: 230px;
            flex-shrink: 0;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 18px;
            padding: 20px 14px;
            box-shadow: var(--shadow-sm);
            position: sticky;
            top: 24px;
        }

        .brand-header {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
        }
        .brand-logo {
            width: 32px;
            height: 32px;
            background: var(--primary);
            color: #000;
            border-radius: 8px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 16px;
            font-weight: bold;
            box-shadow: 0 2px 8px rgba(240, 185, 11, 0.35);
        }
        .brand-name {
            font-size: 16px;
            font-weight: 700;
            color: var(--text-title);
            letter-spacing: -0.3px;
        }

        .nav-list {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .nav-btn {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 10px 14px;
            border-radius: 10px;
            border: none;
            background: transparent;
            color: var(--text-muted);
            font-size: 13.5px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            text-align: left;
            width: 100%;
        }
        .nav-btn i { font-size: 15px; width: 18px; text-align: center; }
        .nav-btn:hover {
            color: var(--text-title);
            background: var(--bg-subtle);
        }
        .nav-btn.active {
            color: #000;
            background: var(--primary);
            font-weight: 700;
            box-shadow: 0 2px 8px rgba(240, 185, 11, 0.28);
        }

        .status-pill {
            margin-top: 20px;
            padding: 8px 12px;
            background: var(--success-bg);
            border: 1px solid rgba(16, 185, 129, 0.2);
            border-radius: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            font-weight: 600;
            color: var(--success);
        }
        .status-dot {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--success);
            box-shadow: 0 0 6px var(--success);
            animation: pulse 2s infinite;
        }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

        /* 右侧主体卡片内容 */
        .main-panel {
            flex: 1;
            min-width: 0;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 18px;
            padding: 28px;
            box-shadow: var(--shadow-sm);
        }

        .tab-pane { display: none; }
        .tab-pane.active { display: block; animation: fadeIn 0.25s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }

        /* 表单控件 */
        .form-group { margin-bottom: 18px; }
        .form-label {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-title);
            margin-bottom: 6px;
        }
        .form-control {
            width: 100%;
            background: var(--bg-subtle);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 10px 14px;
            color: var(--text-title);
            font-size: 13.5px;
            transition: all 0.2s;
        }
        .form-control:focus {
            outline: none;
            border-color: var(--border-focus);
            background: #FFFFFF;
            box-shadow: 0 0 0 3px rgba(240, 185, 11, 0.15);
        }
        textarea.form-control {
            min-height: 120px;
            resize: vertical;
            line-height: 1.55;
        }

        /* 按钮系统 */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 7px;
            padding: 10px 18px;
            font-size: 13.5px;
            font-weight: 600;
            border-radius: 10px;
            border: none;
            cursor: pointer;
            transition: all 0.18s ease;
        }
        .btn:disabled { opacity: 0.55; cursor: not-allowed; }
        .btn-primary {
            background: var(--primary);
            color: #000;
            font-weight: 700;
        }
        .btn-primary:hover:not(:disabled) {
            background: var(--primary-hover);
            box-shadow: 0 3px 10px rgba(240, 185, 11, 0.25);
        }
        .btn-secondary {
            background: var(--bg-subtle);
            color: var(--text-body);
            border: 1px solid var(--border-color);
        }
        .btn-secondary:hover:not(:disabled) {
            background: #EDEFEF;
            color: var(--text-title);
        }
        .btn-success { background: var(--success); color: #FFF; }
        .btn-danger { background: var(--danger-bg); color: var(--danger); border: 1px solid rgba(239, 68, 68, 0.2); }
        .btn-danger:hover { background: var(--danger); color: #FFF; }

        /* 统计卡片 */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: var(--bg-subtle);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 16px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .stat-card:hover {
            border-color: var(--primary);
            box-shadow: var(--shadow-sm);
        }
        .stat-card.active {
            border-color: var(--primary);
            background: var(--primary-light);
        }
        .stat-value {
            font-family: 'JetBrains Mono', monospace;
            font-size: 24px;
            font-weight: 700;
            color: var(--text-title);
            margin-bottom: 2px;
        }
        .stat-label { font-size: 12px; font-weight: 600; color: var(--text-title); }
        .stat-meta { font-size: 11px; color: var(--text-muted); margin-top: 3px; }

        /* 现代工具栏（布满整行） */
        .toolbar-bar {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            background: var(--bg-subtle);
            border: 1px solid var(--border-color);
            padding: 7px;
            border-radius: 10px;
            align-items: center;
            margin-bottom: 8px;
        }
        .emoji-btn {
            background: #FFFFFF;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            color: var(--text-body);
            padding: 4px 7px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.15s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .emoji-btn:hover {
            background: var(--bg-body);
            transform: scale(1.08);
            border-color: #CBD5E1;
        }
        .symbol-btn {
            color: #000;
            background: #F1F5F9;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
            padding: 4px 8px;
        }

        /* 上传组件 */
        .uploader-zone {
            border: 1.5px dashed #CBD5E1;
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            background: var(--bg-subtle);
            cursor: pointer;
            transition: all 0.2s;
        }
        .uploader-zone:hover {
            border-color: var(--primary);
            background: var(--primary-light);
        }
        .uploader-icon {
            font-size: 24px;
            color: var(--primary);
            margin-bottom: 6px;
        }

        .preview-box-169 {
            margin-top: 10px;
            width: 100%;
            aspect-ratio: 16/9;
            max-height: 200px;
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid var(--border-color);
            position: relative;
            background: #000;
            display: none;
        }
        .preview-box-169 img { width: 100%; height: 100%; object-fit: cover; }
        .crop-badge {
            position: absolute;
            bottom: 6px;
            right: 6px;
            background: rgba(0, 0, 0, 0.7);
            color: #FFF;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
        }

        .multi-preview-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 8px;
            margin-top: 10px;
        }
        .multi-preview-item {
            width: 100%;
            aspect-ratio: 1;
            border-radius: 8px;
            object-fit: cover;
            border: 1px solid var(--border-color);
            background: #F1F5F9;
        }

        /* 终端风格日志输出 */
        .terminal-box {
            background: #0F172A;
            border-radius: 10px;
            padding: 12px 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11.5px;
            color: #E2E8F0;
            min-height: 48px;
            max-height: 160px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            margin-top: 14px;
            line-height: 1.5;
        }

        /* 发文记录卡片 */
        .records-container {
            max-height: 380px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-top: 14px;
        }
        .record-card {
            background: var(--bg-subtle);
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 12px 14px;
        }
        .record-head {
            display: flex;
            justify-content: space-between;
            font-size: 12px;
            margin-bottom: 4px;
        }
        .record-tag {
            color: var(--text-title);
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
        }
        .record-time { color: var(--text-muted); font-size: 11px; }
        .record-body { font-size: 12.5px; color: var(--text-body); }

        .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }

        @media(max-width: 820px) {
            .layout-wrapper { flex-direction: column; }
            .sidebar { width: 100%; position: static; }
            .nav-list { display: grid; grid-template-columns: repeat(3, 1fr); }
            .grid-2 { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <div class="layout-wrapper">
        <!-- 左侧功能导航 -->
        <div class="sidebar">
            <div class="brand-header">
                <div class="brand-logo"><i class="fa fa-cube"></i></div>
                <div class="brand-name">Square Hub</div>
            </div>
            
            <div class="nav-list">
                <button class="nav-btn active" onclick="switchTab('auto')"><i class="fa fa-dashboard"></i> 自动调度</button>
                <button class="nav-btn" onclick="switchTab('manual')"><i class="fa fa-bolt"></i> 动态短帖</button>
                <button class="nav-btn" onclick="switchTab('dynamic')"><i class="fa fa-image"></i> 图文动态</button>
                <button class="nav-btn" onclick="switchTab('article')"><i class="fa fa-file-text-o"></i> 广场长文</button>
                <button class="nav-btn" onclick="switchTab('config')"><i class="fa fa-sliders"></i> 账号配置</button>
                <button class="nav-btn" onclick="switchTab('records')"><i class="fa fa-history"></i> 发文记录</button>
            </div>

            <div class="status-pill">
                <div class="status-dot"></div>
                SYSTEM ONLINE
            </div>
        </div>

        <!-- 右侧主操作区 -->
        <div class="main-panel">
            <!-- 1. 自动调度 -->
            <div id="auto" class="tab-pane active">
                <div class="form-group">
                    <label class="form-label"><span>选择运行账号</span></label>
                    <select id="auto_account_selector" class="form-control" onchange="loadAccountStatus()">
                        <option value="">请选择目标账号...</option>
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                <div id="auto_account_actions" style="display:none; margin-bottom: 20px;">
                    <div style="padding:12px; background:var(--bg-subtle); border:1px solid var(--border-color); border-radius:10px; margin-bottom:10px;">
                        <div id="auto_account_name" style="font-weight:700; color:var(--text-title); margin-bottom:3px;"></div>
                        <div id="auto_account_status" style="font-size:12.5px; color:var(--text-muted);"></div>
                    </div>
                    <div style="display:flex; gap:10px;">
                        <button id="auto_start_btn" class="btn btn-success" style="flex:1;" onclick="startAuto()"><i class="fa fa-play"></i> 启动自动排期</button>
                        <button id="auto_stop_btn" class="btn btn-danger" style="flex:1;" onclick="stopAuto()"><i class="fa fa-stop"></i> 停止运行</button>
                    </div>
                </div>
                <div class="form-label" style="margin-top:20px;"><span>今日发文指标与活跃度</span></div>
                <div class="stats-grid" id="today_stats">
                    {% for acc_name, stat in today_stats.items() %}
                    <div class="stat-card" id="stat_{{acc_name}}" onclick="showAccountConfig('{{acc_name}}')">
                        <div class="stat-value">{{ stat.auto_count + stat.manual_count }}</div>
                        <div class="stat-label">{{acc_name}}</div>
                        <div class="stat-meta">自动 {{stat.auto_count}}/{{stat.auto_target}} · 手动/长文 {{stat.manual_count}}</div>
                    </div>
                    {% endfor %}
                </div>
                <div id="account_config_detail" style="display:none; padding:12px; background:var(--primary-light); border:1px solid rgba(240,185,11,0.3); border-radius:10px;">
                    <div id="config_detail_content" style="font-size:12.5px; color:var(--text-body);"></div>
                </div>
            </div>

            <!-- 2. 动态短帖 -->
            <div id="manual" class="tab-pane">
                <div class="form-group">
                    <label class="form-label"><span>发布账号</span></label>
                    <select id="manual_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}} (今日已发:{{today_stats[acc.name].manual_count}})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label"><span>行情标的 (Symbol)</span></label>
                    <div style="display:flex; gap:8px;">
                        <input type="text" id="manual_symbol" class="form-control" placeholder="例如 BTCUSDT" style="flex:1;">
                        <button class="btn btn-secondary" onclick="autoSelectSymbol()"><i class="fa fa-magic"></i> 自动优选</button>
                        <button class="btn btn-secondary" onclick="generateFullTopic()"><i class="fa fa-line-chart"></i> 生成分析</button>
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label"><span>深度话题分析数据</span></label>
                    <textarea id="manual_topic" class="form-control" placeholder="等待提取行情数据..."></textarea>
                </div>
                <button class="btn btn-secondary" onclick="generateAIContent()" style="width:100%; margin-bottom:14px;">
                    <i class="fa fa-microchip"></i> 调用 AI 模型生成发文文案
                </button>
                
                <!-- 快捷工具栏（布满整行） -->
                <div class="toolbar-bar">
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('manual_content', '#')"># 话题</button>
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('manual_content', '$')">$ 标的</button>
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('manual_content', '@')">@ 用户</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🔥')">🔥</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🚀')">🚀</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '📈')">📈</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '📉')">📉</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '💰')">💰</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🎯')">🎯</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '⚡')">⚡</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '⚠️')">⚠️</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '📊')">📊</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '💎')">💎</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🔔')">🔔</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🧠')">🧠</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🟢')">🟢</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('manual_content', '🔴')">🔴</button>
                </div>

                <div class="form-group">
                    <label class="form-label"><span>短动态正文</span></label>
                    <textarea id="manual_content" class="form-control" placeholder="在此输入或等待 AI 生成短动态..."></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitPost()" style="width:100%" id="manual_submit_btn">
                    <i class="fa fa-paper-plane"></i> 立即发布短动态
                </button>
                <div class="terminal-box" id="manual_log">System ready. Waiting for input...</div>
            </div>

            <!-- 3. 图文动态 -->
            <div id="dynamic" class="tab-pane">
                <div class="form-group">
                    <label class="form-label"><span>发布账号</span></label>
                    <select id="dynamic_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}} (今日已发:{{today_stats[acc.name].manual_count}})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label"><span>动态配图 (支持 1~4 张高清多图)</span></label>
                    <div class="uploader-zone" onclick="document.getElementById('dynamic_images_input').click()">
                        <input type="file" id="dynamic_images_input" accept="image/*" multiple style="display:none;" onchange="handleDynamicImagesSelected(event)">
                        <i class="fa fa-camera-retro uploader-icon"></i>
                        <div style="font-size:13.5px; font-weight:600; color:var(--text-title);">点击上传配图 (最多 4 张)</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">支持 PNG, JPG, WEBP，直传官方 CDN 多图画廊</div>
                    </div>
                    <div id="dynamic_images_preview" class="multi-preview-grid"></div>
                </div>

                <div class="toolbar-bar">
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('dynamic_content', '#')"># 话题</button>
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('dynamic_content', '$')">$ 标的</button>
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('dynamic_content', '@')">@ 用户</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '🔥')">🔥</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '🚀')">🚀</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '📈')">📈</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '📉')">📉</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '💰')">💰</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '🎯')">🎯</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '⚡')">⚡</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '📊')">📊</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '💎')">💎</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '🔔')">🔔</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '🟢')">🟢</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('dynamic_content', '🔴')">🔴</button>
                </div>

                <div class="form-group">
                    <label class="form-label"><span>图文动态正文</span></label>
                    <textarea id="dynamic_content" class="form-control" placeholder="在此输入多图动态文本..."></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitDynamicPost()" style="width:100%" id="dynamic_submit_btn">
                    <i class="fa fa-paper-plane"></i> 立即发布图文动态
                </button>
                <div class="terminal-box" id="dynamic_log">System ready. Waiting for upload...</div>
            </div>

            <!-- 4. 广场长文 -->
            <div id="article" class="tab-pane">
                <div class="form-group">
                    <label class="form-label"><span>发布账号</span></label>
                    <select id="article_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}} (今日已发:{{today_stats[acc.name].manual_count}})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label"><span>文章标题</span></label>
                    <input type="text" id="article_title" class="form-control" placeholder="请输入结构严谨的长文标题...">
                </div>
                <div class="form-group">
                    <label class="form-label"><span>16:9 封面卡片 (选填)</span></label>
                    <div class="uploader-zone" onclick="document.getElementById('article_cover_file').click()">
                        <input type="file" id="article_cover_file" accept="image/*" style="display:none;" onchange="handleCoverSelection(event)">
                        <i class="fa fa-cloud-upload uploader-icon"></i>
                        <div style="font-size:13.5px; font-weight:600; color:var(--text-title);">点击上传封面图片</div>
                        <div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">智能裁切为标准 16:9 (1200×675) 信息流封面</div>
                    </div>
                    <div id="cover_preview_container" class="preview-box-169">
                        <img id="cover_preview_img" src="" alt="Cover Preview">
                        <div class="crop-badge" id="cover_size_tag">16:9 CROPPED</div>
                    </div>
                </div>

                <div class="toolbar-bar">
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('article_content', '#')"># 话题</button>
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('article_content', '$')">$ 标的</button>
                    <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToTextarea('article_content', '@')">@ 用户</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '🔥')">🔥</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '🚀')">🚀</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '📈')">📈</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '📉')">📉</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '💡')">💡</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '💰')">💰</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '🎯')">🎯</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '⚡')">⚡</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '📊')">📊</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '💎')">💎</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '🔔')">🔔</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '🟢')">🟢</button>
                    <button type="button" class="emoji-btn" onclick="insertSymbolToTextarea('article_content', '🔴')">🔴</button>
                </div>

                <div class="form-group">
                    <label class="form-label"><span>文章正文 (Markdown 排版)</span></label>
                    <textarea id="article_content" class="form-control" style="min-height:220px;" placeholder="在此输入长文正文内容..."></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitArticlePost()" style="width:100%" id="article_submit_btn">
                    <i class="fa fa-paper-plane"></i> 立即发布广场长文
                </button>
                <div class="terminal-box" id="article_log">System ready. Waiting for article...</div>
            </div>

            <!-- 5. 账号配置 -->
            <div id="config" class="tab-pane">
                <div class="form-group">
                    <label class="form-label"><span>选择配置账号</span></label>
                    <select id="config_account" class="form-control" onchange="loadAccountConfig()">
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label"><span>专属生成提示词 (System Prompt)</span></label>
                    <textarea id="config_prompt" class="form-control"></textarea>
                </div>
                <div class="form-group">
                    <label class="form-label"><span>接入 AI 大模型</span></label>
                    <select id="config_model" class="form-control">
                        <option value="zhipu">智谱 GLM-4-Flash</option>
                        <option value="deepseek">DeepSeek V4-Flash</option>
                    </select>
                </div>
                <div class="grid-2" style="margin-top:16px;">
                    <div>
                        <label class="form-label"><span>每日最小发文数</span></label>
                        <input type="number" id="cfg_schedule_daily_min" class="form-control" min="1" value="10">
                    </div>
                    <div>
                        <label class="form-label"><span>每日最大发文数</span></label>
                        <input type="number" id="cfg_schedule_daily_max" class="form-control" min="1" value="20">
                    </div>
                </div>
                <div class="grid-2" style="margin-top:12px;">
                    <div>
                        <label class="form-label"><span>最小间隔 (分钟)</span></label>
                        <input type="number" id="cfg_schedule_interval_min" class="form-control" min="2" value="60">
                    </div>
                    <div>
                        <label class="form-label"><span>最大间隔 (分钟)</span></label>
                        <input type="number" id="cfg_schedule_interval_max" class="form-control" min="5" value="90">
                    </div>
                </div>
                <div class="grid-2" style="margin-top:12px; margin-bottom: 20px;">
                    <div>
                        <label class="form-label"><span>活跃开始时间</span></label>
                        <input type="time" class="form-control" id="cfg_schedule_active_start" value="07:00">
                    </div>
                    <div>
                        <label class="form-label"><span>活跃结束时间</span></label>
                        <input type="time" class="form-control" id="cfg_schedule_active_end" value="23:59">
                    </div>
                </div>
                <button class="btn btn-primary" onclick="saveAccountConfig()" style="width:100%;">
                    <i class="fa fa-save"></i> 保存账号排期配置
                </button>
                <div class="terminal-box" id="config_log">Configuration ready.</div>
            </div>

            <!-- 6. 发文记录 -->
            <div id="records" class="tab-pane">
                <div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px;">
                    <select id="record_account" class="form-control" style="flex:1; min-width:140px;">
                        <option value="">全部账号</option>
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                    <input type="date" id="record_date" class="form-control" style="flex:1; min-width:140px;" value="{{today}}">
                    <button class="btn btn-secondary" onclick="loadRecords()"><i class="fa fa-search"></i> 查询</button>
                    <button class="btn btn-secondary" onclick="exportRecords()"><i class="fa fa-download"></i> 导出 CSV</button>
                </div>
                <div class="records-container" id="records_list"></div>
                <div style="margin-top:18px; padding-top:14px; border-top:1px solid var(--border-color); display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
                    <select id="delete_account" class="form-control" style="flex:1; min-width:120px;">
                        <option value="">指定账号</option>
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                    <input type="date" id="delete_date" class="form-control" style="flex:1; min-width:120px;">
                    <button class="btn btn-danger" onclick="deleteSelectedRecords()">删除筛选记录</button>
                    <button class="btn btn-danger" onclick="deleteAllRecords()">清空所有记录</button>
                </div>
                <div class="terminal-box" id="delete_log">Ready.</div>
            </div>
        </div>
    </div>

    <script>
        let currentProcessedCoverBlob = null;
        let selectedDynamicFiles = [];

        function switchTab(tabId) {
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-pane').forEach(c => c.classList.remove('active'));
            const activeBtn = document.querySelector(`.nav-btn[onclick="switchTab('${tabId}')"]`);
            if(activeBtn) activeBtn.classList.add('active');
            const targetContent = document.getElementById(tabId);
            if(targetContent) targetContent.classList.add('active');
            if(tabId === 'auto') refreshAutoPage();
            if(tabId === 'config') loadAccountConfig();
            if(tabId === 'records') loadRecords();
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
                            const st = d.running ? `<span style="color:var(--success);font-weight:700;">● 自动排期运行中</span>` : `<span style="color:var(--text-muted);">○ 已停止</span>`;
                            const total = (s.auto_count||0) + (s.manual_count||0);
                            document.getElementById('auto_account_status').innerHTML = `${st} ｜ 今日发布总量: ${total} (自动: ${s.auto_count}/${s.auto_target} · 手动/长文/图文: ${s.manual_count})`;
                            document.getElementById('auto_start_btn').disabled = d.running;
                            document.getElementById('auto_stop_btn').disabled = !d.running;
                        });
                });
        }
        
        function startAuto() {
            const acc = document.getElementById('auto_account_selector').value;
            fetch(`/api/auto/start?account=${acc}`).then(r => r.json()).then(d => {
                alert(d.msg);
                refreshAutoPage();
            });
        }
        
        function stopAuto() {
            const acc = document.getElementById('auto_account_selector').value;
            fetch(`/api/auto/stop?account=${acc}`).then(r => r.json()).then(d => {
                alert(d.msg);
                refreshAutoPage();
            });
        }
        
        function showAccountConfig(acc) {
            document.querySelectorAll('.stat-card').forEach(c => c.classList.remove('active'));
            const card = document.getElementById('stat_'+acc);
            if(card) card.classList.add('active');
            fetch(`/api/config/load?account=${acc}`).then(r => r.json()).then(c => {
                let h = `<div><strong>提示词：</strong>${c.prompt||'默认'}</div><div style="margin-top:4px;"><strong>模型：</strong>${c.model_type}</div>`;
                document.getElementById('config_detail_content').innerHTML = h;
                document.getElementById('account_config_detail').style.display = 'block';
            });
        }
        
        function refreshAutoPage() {
            fetch('/api/auto/refresh').then(r => r.json()).then(d => {
                let h = '';
                for(const acc of d.accounts) {
                    const s = d.today_stats[acc.name];
                    if(!s) continue;
                    const total = (s.auto_count||0) + (s.manual_count||0);
                    h += `<div class="stat-card" id="stat_${acc.name}" onclick="showAccountConfig('${acc.name}')">
                        <div class="stat-value">${total}</div>
                        <div class="stat-label">${acc.name}</div>
                        <div class="stat-meta">自动 ${s.auto_count}/${s.auto_target} · 手动/长文 ${s.manual_count}</div>
                    </div>`;
                }
                document.getElementById('today_stats').innerHTML = h;
                updateSelectOptions('manual_account', d.today_stats);
                updateSelectOptions('dynamic_account', d.today_stats);
                updateSelectOptions('article_account', d.today_stats);
            });
        }

        function updateSelectOptions(elementId, stats) {
            const selectEl = document.getElementById(elementId);
            if(!selectEl) return;
            for(let i=0; i<selectEl.options.length; i++) {
                const opt = selectEl.options[i];
                const name = opt.getAttribute('data-name');
                if(name && stats[name]) {
                    opt.text = `${name} (今日已发:${stats[name].manual_count})`;
                }
            }
        }
        
        function loadAccountConfig() {
            const a = document.getElementById('config_account').value;
            fetch(`/api/config/load?account=${a}`).then(r => r.json()).then(c => {
                document.getElementById('config_prompt').value = c.prompt || '';
                document.getElementById('config_model').value = c.model_type || 'zhipu';
                const s = c.schedule || {};
                document.getElementById('cfg_schedule_daily_min').value = s.daily_min || 10;
                document.getElementById('cfg_schedule_daily_max').value = s.daily_max || 20;
                document.getElementById('cfg_schedule_interval_min').value = s.interval_min || 8;
                document.getElementById('cfg_schedule_interval_max').value = s.interval_max || 25;
                document.getElementById('cfg_schedule_active_start').value = s.active_start || '08:00';
                document.getElementById('cfg_schedule_active_end').value = s.active_end || '22:00';
                document.getElementById('config_log').textContent = '✅ 配置已载入';
            });
        }
        
        function saveAccountConfig() {
            const a = document.getElementById('config_account').value;
            const p = document.getElementById('config_prompt').value;
            const m = document.getElementById('config_model').value;
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
                body: JSON.stringify({ account:a, prompt:p, model_type:m, daily_limit:8, auto_interval:60, schedule:schedule })
            }).then(r => r.json()).then(d => {
                document.getElementById('config_log').textContent = '✅ 保存排期配置成功';
                refreshAutoPage();
            });
        }

        function insertSymbolToTextarea(textareaId, symbol) {
            const textarea = document.getElementById(textareaId);
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const val = textarea.value;
            textarea.value = val.substring(0, start) + symbol + val.substring(end);
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = start + symbol.length;
        }

        // ================= 动态短帖 =================
        function autoSelectSymbol() {
            fetch('/api/manual/auto_symbol').then(r => r.json()).then(d => {
                document.getElementById('manual_symbol').value = d.symbol;
            });
        }
        
        function generateFullTopic() {
            const s = document.getElementById('manual_symbol').value;
            if(!s) { alert('请先输入或自动选择交易对'); return; }
            document.getElementById('manual_log').textContent = '⏳ 正在拉取行情深度分析...';
            fetch(`/api/manual/full_topic?symbol=${s}`).then(r => r.json()).then(d => {
                document.getElementById('manual_topic').value = d.topic;
                document.getElementById('manual_log').textContent = '✅ 行情数据分析完成';
            });
        }
        
        function generateAIContent() {
            const t = document.getElementById('manual_topic').value;
            const k = document.getElementById('manual_account').value;
            const logBox = document.getElementById('manual_log');
            if(!t.trim()) { alert('请先生成或输入话题分析内容'); return; }
            logBox.textContent = '⏳ 正在调用 AI 模型生成短动态...';
            fetch('/api/manual/generate_ai', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({topic:t, account_key:k})
            }).then(r => r.json()).then(d => {
                if (d.success && d.content) {
                    document.getElementById('manual_content').value = d.content;
                    logBox.textContent = '✅ AI 文案生成就绪！请核对后发布';
                } else {
                    logBox.textContent = '❌ 生成异常: ' + (d.msg || '未知错误');
                }
            }).catch(err => {
                logBox.textContent = `❌ 请求异常: ${err}`;
            });
        }
        
        function submitPost() {
            const k = document.getElementById('manual_account').value;
            const c = document.getElementById('manual_content').value;
            const s = document.getElementById('manual_symbol').value;
            if(!c.trim()) { alert('短动态正文不能为空'); return; }
            const submitBtn = document.getElementById('manual_submit_btn');
            submitBtn.disabled = true;
            document.getElementById('manual_log').textContent = '⏳ 正在推送到币安广场...';
            fetch('/api/manual/post', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({account_key:k, content:c, symbol:s})
            }).then(r => r.json()).then(d => {
                submitBtn.disabled = false;
                document.getElementById('manual_log').textContent = d.success ? `✅ 发布成功！Post ID: ${d.post_id}` : `❌ 发布失败: ${d.msg}`;
                if (d.success) {
                    document.getElementById('manual_content').value = '';
                    refreshAutoPage();
                    loadRecords();
                }
            }).catch(err => {
                submitBtn.disabled = false;
                document.getElementById('manual_log').textContent = `❌ 请求异常: ${err}`;
            });
        }

        // ================= 图文动态 =================
        function handleDynamicImagesSelected(event) {
            const files = event.target.files;
            selectedDynamicFiles = Array.from(files).slice(0, 4);
            const container = document.getElementById('dynamic_images_preview');
            container.innerHTML = '';
            selectedDynamicFiles.forEach(file => {
                const img = document.createElement('img');
                img.src = URL.createObjectURL(file);
                img.className = 'multi-preview-item';
                container.appendChild(img);
            });
        }

        function submitDynamicPost() {
            const k = document.getElementById('dynamic_account').value;
            const c = document.getElementById('dynamic_content').value.trim();
            const logBox = document.getElementById('dynamic_log');
            const submitBtn = document.getElementById('dynamic_submit_btn');

            if (!c) { alert('动态正文不能为空！'); return; }

            submitBtn.disabled = true;
            logBox.textContent = '⏳ 正在直传图片并推送到动态流...';

            const formData = new FormData();
            formData.append('account_key', k);
            formData.append('content', c);
            selectedDynamicFiles.forEach(file => {
                formData.append('images', file);
            });

            fetch('/api/dynamic/post', {
                method: 'POST',
                body: formData
            }).then(r => r.json()).then(d => {
                submitBtn.disabled = false;
                if (d.success) {
                    logBox.textContent = `✅ 图文动态发布成功！Post ID: ${d.post_id}`;
                    document.getElementById('dynamic_content').value = '';
                    document.getElementById('dynamic_images_preview').innerHTML = '';
                    selectedDynamicFiles = [];
                    refreshAutoPage();
                    loadRecords();
                } else {
                    logBox.textContent = `❌ 发布失败: ${d.msg}`;
                }
            }).catch(err => {
                submitBtn.disabled = false;
                logBox.textContent = `❌ 请求异常: ${err}`;
            });
        }

        // ================= 广场长文 =================
        function handleCoverSelection(event) {
            const file = event.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = function(e) {
                const img = new Image();
                img.onload = function() {
                    const TARGET_W = 1200;
                    const TARGET_H = 675;
                    const TARGET_RATIO = TARGET_W / TARGET_H;

                    const canvas = document.createElement('canvas');
                    canvas.width = TARGET_W;
                    canvas.height = TARGET_H;
                    const ctx = canvas.getContext('2d');

                    let srcX = 0, srcY = 0, srcW = img.width, srcH = img.height;
                    const currentRatio = img.width / img.height;

                    if (currentRatio > TARGET_RATIO) {
                        srcW = img.height * TARGET_RATIO;
                        srcX = (img.width - srcW) / 2;
                    } else {
                        srcH = img.width / TARGET_RATIO;
                        srcY = (img.height - srcH) / 2;
                    }

                    ctx.drawImage(img, srcX, srcY, srcW, srcH, 0, 0, TARGET_W, TARGET_H);

                    canvas.toBlob(function(blob) {
                        currentProcessedCoverBlob = blob;
                        const previewUrl = URL.createObjectURL(blob);
                        document.getElementById('cover_preview_img').src = previewUrl;
                        document.getElementById('cover_preview_container').style.display = 'block';
                        document.getElementById('cover_size_tag').textContent = `16:9 CROPPED · ${(blob.size / 1024).toFixed(0)}KB`;
                    }, 'image/jpeg', 0.88);
                };
                img.src = e.target.result;
            };
            reader.readAsDataURL(file);
        }

        function submitArticlePost() {
            const k = document.getElementById('article_account').value;
            const title = document.getElementById('article_title').value.trim();
            const content = document.getElementById('article_content').value.trim();
            const logBox = document.getElementById('article_log');
            const submitBtn = document.getElementById('article_submit_btn');

            if (!title) { alert('文章标题不能为空！'); return; }
            if (!content) { alert('文章正文不能为空！'); return; }

            submitBtn.disabled = true;
            logBox.textContent = '⏳ 正在发布长文至币安广场...';

            const formData = new FormData();
            formData.append('account_key', k);
            formData.append('title', title);
            formData.append('content', content);
            if (currentProcessedCoverBlob) {
                formData.append('cover_file', currentProcessedCoverBlob, 'cover.jpg');
            }

            fetch('/api/article/post', {
                method: 'POST',
                body: formData
            }).then(r => r.json()).then(d => {
                submitBtn.disabled = false;
                if (d.success) {
                    logBox.textContent = `✅ 广场长文发布成功！Post ID: ${d.post_id}`;
                    document.getElementById('article_title').value = '';
                    document.getElementById('article_content').value = '';
                    document.getElementById('article_cover_file').value = '';
                    document.getElementById('cover_preview_container').style.display = 'none';
                    currentProcessedCoverBlob = null;
                    refreshAutoPage();
                    loadRecords();
                } else {
                    logBox.textContent = `❌ 发布失败: ${d.msg}`;
                }
            }).catch(err => {
                submitBtn.disabled = false;
                logBox.textContent = `❌ 请求异常: ${err}`;
            });
        }
        
        function loadRecords() {
            const a = document.getElementById('record_account').value;
            const d = document.getElementById('record_date').value;
            fetch(`/api/records?account=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`).then(r => r.json()).then(rs => {
                let h = '';
                rs.forEach(r => {
                    h += `<div class="record-card">
                        <div class="record-head">
                            <span class="record-tag">[${r.mode.toUpperCase()}] ${r.symbol} · ${r.account}</span>
                            <span class="record-time">${r.time}</span>
                        </div>
                        <div class="record-body">${r.content}</div>
                    </div>`;
                });
                document.getElementById('records_list').innerHTML = h || '<div style="text-align:center;color:var(--text-muted);padding:20px;">暂无发文记录</div>';
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
            fetch(`/api/records/delete?account=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`, {method:'POST'}).then(r => r.json()).then(d => {
                document.getElementById('delete_log').textContent = '✅ 已删除 '+d.deleted_count+' 条记录';
                loadRecords();
            });
        }
        
        function deleteAllRecords() {
            if(!confirm('确定要清空所有发文记录吗？')) return;
            fetch('/api/records/delete?all=true', {method:'POST'}).then(r => r.json()).then(d => {
                document.getElementById('delete_log').textContent = '✅ 全部发文记录已清空';
                loadRecords();
            });
        }
        
        window.onload = function() {
            refreshAutoPage();
            loadRecords();
        };
    </script>
</body>
</html>
"""

# ======================== 路由接口 ========================
@app.route("/")
def index():
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    today = str(datetime.date.today())
    return render_template_string(UI_TEMPLATE, accounts=accounts, today_stats=today_stats, today=today)

# --- 1. 动态短帖接口 ---
@app.route("/api/manual/auto_symbol")
def manual_auto_symbol():
    try:
        from topic_main import run_topic
        topic = run_topic()
        symbol = topic.get("symbol", "BTCUSDT")
        return jsonify({"success": True, "symbol": symbol})
    except Exception:
        return jsonify({"success": True, "symbol": "BTCUSDT"})

@app.route("/api/manual/full_topic")
def manual_full_topic():
    symbol = request.args.get("symbol", "").strip()
    try:
        from topic_main import run_topic
        topic = run_topic(target_symbol=symbol, verbose=True)
        return jsonify({"success": True, "topic": topic.get("text", "")})
    except Exception as e:
        return jsonify({"success": False, "topic": f"行情拉取异常: {str(e)}"})

@app.route("/api/manual/generate_ai", methods=["POST"])
def manual_generate_ai():
    try:
        d = request.json or {}
        t = d.get("topic", "").strip()
        k = d.get("account_key", "").strip()

        if not t:
            return jsonify({"success": False, "msg": "话题分析内容为空，请先生成或输入话题"}), 400
        if not k:
            return jsonify({"success": False, "msg": "请选择发文账号"}), 400

        acc = get_account_by_key(k)
        if not acc:
            return jsonify({"success": False, "msg": "指定发文账号不存在"}), 400

        model_type = acc.get("model_type", "zhipu")
        api_key = get_api_key_by_model(model_type)

        if not api_key:
            key_name = "ZHIPU_API_KEY" if model_type == "zhipu" else "DEEPSEEK_API_KEY"
            model_name = "智谱 GLM-4" if model_type == "zhipu" else "DeepSeek"
            err_msg = f"未检测到 {model_name} 的 API Key，请在 .env 文件中配置 {key_name}"
            print(f"❌ [AI 生成失败]: {err_msg}")
            return jsonify({"success": False, "msg": err_msg})

        from ai_core import generate_content
        content = ""
        try:
            content, _ = generate_content(
                {"text": t},
                api_key=api_key,
                model_type=model_type,
                custom_prompt=acc.get("prompt", "")
            )
        except Exception:
            content, _ = generate_content(
                t,
                api_key=api_key,
                model_type=model_type,
                custom_prompt=acc.get("prompt", "")
            )

        if content and content.strip():
            return jsonify({"success": True, "content": content.strip()})
        else:
            return jsonify({"success": False, "msg": "AI 模型返回内容为空，请检查 Key 余额或网络"})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "msg": f"调用 AI 模型异常: {str(e)}"})

@app.route("/api/manual/post", methods=["POST"])
def manual_post():
    d = request.json or {}
    k = d.get("account_key", "")
    c = d.get("content", "")
    s = d.get("symbol", "")
    acc = get_account_by_key(k)
    from post_main import post_content
    ok, msg, pid = post_content(c, k)
    pid = str(pid) if pid else "未知"
    if ok:
        save_post_record("manual", acc["name"], s, c, pid)
        inc_manual_published(acc["name"])
        cfg = load_json(CONFIG_FILE)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg[f"{acc['name']}_last_run"] = now_str
        cfg[f"{acc['name']}_last_manual_run"] = now_str
        save_json(CONFIG_FILE, cfg)
    return jsonify({"success": ok, "post_id": pid, "msg": msg})

# --- 2. 原生图文动态接口 ---
@app.route("/api/dynamic/post", methods=["POST"])
def dynamic_post():
    account_key = request.form.get("account_key", "").strip()
    content = request.form.get("content", "").strip()
    files = request.files.getlist("images")

    if not account_key:
        return jsonify({"success": False, "msg": "请选择发布账号"})
    if not content:
        return jsonify({"success": False, "msg": "动态正文不能为空"})

    acc = get_account_by_key(account_key)
    if not acc:
        return jsonify({"success": False, "msg": "指定账号不存在"})

    saved_paths = []
    for f in files[:4]:
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1].lower() or ".jpg"
            unique_filename = f"dynamic_{int(time.time())}_{random.randint(1000, 9999)}{ext}"
            save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
            f.save(save_path)
            saved_paths.append(save_path)

    from post_main import post_content
    ok, msg, pid = post_content(content, account_key, image_paths=saved_paths)
    pid = str(pid) if pid else "未知"
    if ok:
        save_post_record("dynamic", acc["name"], "图文动态", content, pid)
        inc_manual_published(acc["name"])
        cfg = load_json(CONFIG_FILE)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg[f"{acc['name']}_last_run"] = now_str
        cfg[f"{acc['name']}_last_manual_run"] = now_str
        save_json(CONFIG_FILE, cfg)

    return jsonify({"success": ok, "post_id": pid, "msg": msg})

# --- 3. 广场长文接口 ---
@app.route("/api/article/post", methods=["POST"])
def article_post():
    account_key = request.form.get("account_key", "").strip()
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    cover_file = request.files.get("cover_file")

    if not account_key:
        return jsonify({"success": False, "msg": "请选择发布账号"})
    if not title:
        return jsonify({"success": False, "msg": "文章标题不能为空"})
    if not content:
        return jsonify({"success": False, "msg": "文章正文不能为空"})

    acc = get_account_by_key(account_key)
    if not acc:
        return jsonify({"success": False, "msg": "指定账号不存在"})

    save_path = ""
    if cover_file and cover_file.filename:
        ext = os.path.splitext(cover_file.filename)[1].lower() or ".jpg"
        unique_filename = f"cover_{int(time.time())}_{random.randint(1000, 9999)}{ext}"
        save_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        cover_file.save(save_path)

    from post_main import post_article
    ok, msg, pid = post_article(title, content, account_key, cover_path=save_path)
    pid = str(pid) if pid else "未知"
    if ok:
        save_post_record("article", acc["name"], title[:20], content, pid)
        inc_manual_published(acc["name"])
        cfg = load_json(CONFIG_FILE)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg[f"{acc['name']}_last_run"] = now_str
        cfg[f"{acc['name']}_last_article_run"] = now_str
        save_json(CONFIG_FILE, cfg)

    return jsonify({"success": ok, "post_id": pid, "msg": msg})

# --- 4. 自动模式与配置接口 ---
@app.route("/api/auto/start")
def auto_start():
    a = request.args.get("account")
    ok = start_account_auto_publish(a)
    return jsonify({"success": ok, "msg": "已启动" if ok else "已运行"})

@app.route("/api/auto/stop")
def auto_stop():
    a = request.args.get("account")
    stop_account_auto_publish(a)
    return jsonify({"success": True, "msg": "已停止"})

@app.route("/api/auto/status")
def auto_status():
    a = request.args.get("account")
    acc = get_account_by_name(a) or {}
    return jsonify({
        "running": account_running_status.get(a, False),
        "daily_limit": acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": acc.get("auto_interval", DEFAULT_AUTO_INTERVAL)
    })

@app.route("/api/auto/refresh")
def auto_refresh():
    return jsonify({"accounts": get_all_accounts(), "today_stats": get_today_stats()})

@app.route("/api/config/load")
def config_load():
    a = request.args.get("account")
    acc = get_account_by_name(a) or {}
    return jsonify({
        "prompt": acc.get("prompt", ""),
        "model_type": acc.get("model_type", "zhipu"),
        "daily_limit": acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": acc.get("auto_interval", DEFAULT_AUTO_INTERVAL),
        "schedule": acc.get("schedule", {})
    })

@app.route("/api/config/save", methods=["POST"])
def config_save():
    d = request.json or {}
    save_account_prompt(d["account"], d["prompt"], d["daily_limit"], d["auto_interval"], d["model_type"], d.get("schedule"))
    return jsonify({"success": True})

# --- 5. 发文记录与导出 ---
@app.route("/api/records")
def records():
    a = request.args.get("account")
    d = request.args.get("date")
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if a and r.get("account") != a:
            continue
        if d and r.get("date") != d:
            continue
        res.append(r)
    return jsonify(res)

@app.route("/api/records/export")
def records_export():
    a = request.args.get("account")
    d = request.args.get("date")
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if a and r.get("account") != a:
            continue
        if d and r.get("date") != d:
            continue
        res.append(r)
    
    def csv_escape(s):
        return s.replace('"', '""') if isinstance(s, str) else s

    csv = "模式,账号,日期,时间,标题/币种,ID,状态,内容\n"
    for r in res:
        csv += f"{csv_escape(r.get('mode',''))},{csv_escape(r.get('account',''))},{csv_escape(r.get('date',''))},{csv_escape(r.get('time',''))},{csv_escape(r.get('symbol',''))},{csv_escape(r.get('post_id',''))},{csv_escape(r.get('status',''))},\"{csv_escape(r.get('content',''))}\"\n"
    response = make_response(csv)
    response.headers["Content-Type"] = "text/csv;charset=utf-8"
    response.headers["Content-Disposition"] = "attachment;filename=records.csv"
    return response

@app.route("/api/records/delete", methods=["POST"])
def records_delete():
    a = request.args.get("account")
    d = request.args.get("date")
    all_records = request.args.get("all") == "true"
    cnt = delete_records(a, d, all_records)
    return jsonify({"success": True, "deleted_count": cnt})

@app.errorhandler(Exception)
def handle_global_exception(e):
    if isinstance(e, HTTPException):
        return e
    print("❌ [服务端未捕获异常]:", e)
    traceback.print_exc()
    return jsonify({"success": False, "msg": f"服务端发生异常: {str(e)}"}), 500

# ======================== 启动入口 ========================
if __name__ == "__main__":
    recover_counts_from_records()
    app.run(host="0.0.0.0", port=5000, debug=False)
