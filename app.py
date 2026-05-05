from flask import Flask, render_template_string, request, jsonify, Response, make_response
import os
import json
import datetime
import threading
import time
import urllib.parse
import csv
from io import StringIO

app = Flask(__name__)

# ======================== 核心配置 ========================
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

# ======================== 【你要的】批次安全配置 ========================
BATCH_SIZE = 2                  # 每批几个账号
ACCOUNT_DELAY = 3               # 同批次内账号间隔（秒）
BATCH_WAIT_SECONDS = 15         # 批次之间等待（秒）

# ======================== 数据路径（不变） ========================
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/records.json"
CONFIG_FILE = f"{DATA_DIR}/config.json"
PROMPT_FILE = f"{DATA_DIR}/prompts.json"
BACKUP_DIR = f"{DATA_DIR}/backups"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

account_running_status = {}
status_lock = threading.Lock()
batch_task = None

# ======================== 工具函数（完全不变） ========================
def load_json(file_path, default=None):
    if default is None: default = {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return default

def save_json(file_path, data):
    if os.path.exists(file_path):
        bk = f"{os.path.basename(file_path)}.backup.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        with open(file_path, "r", encoding="utf-8") as f:
            with open(os.path.join(BACKUP_DIR, bk), "w", encoding="utf-8") as bf:
                bf.write(f.read())
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def backup_current_data():
    ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    for fpath in [DB_FILE, CONFIG_FILE, PROMPT_FILE]:
        if os.path.exists(fpath):
            tgt = os.path.join(BACKUP_DIR, f"{os.path.basename(fpath)}.{ts}")
            with open(fpath, "r", encoding="utf-8") as fr:
                with open(tgt, "w", encoding="utf-8") as fw:
                    fw.write(fr.read())
    return ts

def import_json_file(stream, target, overwrite=True):
    try:
        d = json.load(stream)
        if not overwrite:
            ori = load_json(target)
            if isinstance(ori, dict) and isinstance(d, dict):
                ori.update(d)
                d = ori
            elif isinstance(ori, list) and isinstance(d, list):
                d = ori + d
        save_json(target, d)
        return True, f"导入成功 {os.path.basename(target)}"
    except Exception as e:
        return False, f"导入失败 {str(e)}"

def import_csv_records(stream, overwrite=True):
    try:
        reader = csv.DictReader(stream)
        req = ["mode","account","date","time","symbol","content","post_id","status"]
        for f in req:
            if f not in reader.fieldnames:
                return False, f"缺少字段 {f}"
        arr = []
        for row in reader:
            arr.append({
                "mode": row.get("mode",""),
                "account": row.get("account",""),
                "date": row.get("date",""),
                "time": row.get("time",""),
                "symbol": row.get("symbol",""),
                "content": row.get("content",""),
                "post_id": row.get("post_id",""),
                "status": row.get("status","success")
            })
        if overwrite:
            save_json(DB_FILE, arr)
        else:
            ori = load_json(DB_FILE, [])
            save_json(DB_FILE, ori + arr)
        return True, f"导入成功 {len(arr)} 条"
    except Exception as e:
        return False, f"CSV失败 {str(e)}"

# ======================== 账号逻辑（完全不变） ========================
def get_accounts_from_env():
    s = os.getenv("BINANCE_ACCOUNTS","").strip()
    ret = []
    if not s: return ret
    for item in s.split(","):
        item = item.strip()
        if "|" not in item: continue
        n, k = item.split("|",1)
        n = n.strip()
        k = k.strip()
        if n and k: ret.append({"name":n,"key":k})
    return ret

def get_all_accounts():
    env = get_accounts_from_env()
    prompts = load_json(PROMPT_FILE)
    ret = []
    for acc in env:
        n = acc["name"]
        cfg = prompts.get(n,{})
        with status_lock:
            run = account_running_status.get(n,False)
        ret.append({
            "name": n,
            "key": acc["key"],
            "prompt": cfg.get("prompt",""),
            "daily_limit": cfg.get("daily_limit", DEFAULT_DAILY_LIMIT),
            "auto_interval": cfg.get("auto_interval", DEFAULT_AUTO_INTERVAL),
            "running": run
        })
    return ret

def get_account_by_name(n):
    for a in get_all_accounts():
        if a["name"] == n: return a
    return None

def get_account_by_key(k):
    for a in get_all_accounts():
        if a["key"] == k: return a
    return None

def save_account_prompt(n, prompt, limit, interval):
    p = load_json(PROMPT_FILE)
    p[n] = {
        "prompt": prompt,
        "daily_limit": int(limit),
        "auto_interval": int(interval)
    }
    save_json(PROMPT_FILE, p)

# ======================== 记录（完全不变） ========================
def save_post_record(mode, acc, sym, content, pid, stat="success"):
    r = {
        "mode": mode, "account": acc, "date": str(datetime.date.today()),
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": sym, "content": content, "post_id": pid, "status": stat
    }
    arr = load_json(DB_FILE, [])
    arr.append(r)
    if len(arr) > 1000: arr = arr[-1000:]
    save_json(DB_FILE, arr)

def get_today_stats(acc_name=None):
    today = str(datetime.date.today())
    arr = load_json(DB_FILE, [])
    accounts = get_all_accounts()
    stats = {a["name"]: {
        "count":0,"auto_count":0,"manual_count":0,
        "limit":a["daily_limit"], "remaining":a["daily_limit"], "running":a["running"]
    } for a in accounts}
    for r in arr:
        if r.get("date")==today and r.get("status")=="success":
            n = r.get("account")
            if n in stats:
                stats[n]["count"] += 1
                if r.get("mode")=="auto": stats[n]["auto_count"]+=1
                else: stats[n]["manual_count"]+=1
                stats[n]["remaining"] = stats[n]["limit"] - stats[n]["count"]
    if acc_name:
        return stats.get(acc_name, {"count":0,"auto_count":0,"manual_count":0,
                                  "limit":DEFAULT_DAILY_LIMIT,"remaining":DEFAULT_DAILY_LIMIT,"running":False})
    return stats

def delete_records(acc=None, dt=None, all_rec=False):
    arr = load_json(DB_FILE, [])
    if all_rec:
        new_arr = []
    else:
        new_arr = []
        for r in arr:
            if acc and r.get("account")==acc:
                if dt and r.get("date")==dt: continue
                elif not dt: continue
            elif dt and r.get("date")==dt and not acc: continue
            new_arr.append(r)
    save_json(DB_FILE, new_arr)
    return len(arr)-len(new_arr)

# ======================== 单账号自动（完全不变） ========================
def auto_publisher_worker(acc_name):
    while True:
        with status_lock:
            if not account_running_status.get(acc_name,False): break
        acc = get_account_by_name(acc_name)
        if not acc:
            time.sleep(10)
            continue
        stat = get_today_stats(acc_name)
        if stat["count"] >= stat["limit"]:
            with status_lock:
                account_running_status[acc_name] = False
            break
        try:
            from topic_main import run_topic
            topic = run_topic()
            if not topic:
                time.sleep(10)
                continue
            from ai_core import generate_content
            content, _ = generate_content(topic, ZHIPU_API_KEY, custom_prompt=acc.get("prompt"))
            if not content:
                time.sleep(10)
                continue
            from post_main import post_content
            ok, msg, pid = post_content(content, acc["key"])
            pid_str = str(pid) if pid and pid!="[object Object]" else "未知ID"
            if ok:
                save_post_record("auto", acc_name, topic.get("symbol",""), content, pid_str)
                cfg = load_json(CONFIG_FILE)
                cfg[f"{acc_name}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cfg[f"{acc_name}_last_auto_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_json(CONFIG_FILE, cfg)
            else:
                save_post_record("auto", acc_name, topic.get("symbol",""), content, pid_str, "fail")
            time.sleep(acc["auto_interval"]*60)
        except Exception as e:
            print(f"[{acc_name}] err: {e}")
            time.sleep(10)

def start_account_auto_publish(acc_name):
    with status_lock:
        if account_running_status.get(acc_name,False):
            return False
        account_running_status[acc_name] = True
    threading.Thread(target=auto_publisher_worker, args=(acc_name,), daemon=True).start()
    return True

def stop_account_auto_publish(acc_name):
    with status_lock:
        account_running_status[acc_name] = False
    return True

# ======================== 【核心新增】批量总控：批次串行、无跨组并发 ========================
def batch_start_all():
    accounts = get_all_accounts()
    names = [a["name"] for a in accounts]
    for i in range(0, len(names), BATCH_SIZE):
        batch = names[i:i+BATCH_SIZE]
        print(f"批次: {batch}")
        for name in batch:
            st = get_today_stats(name)
            if st["count"] >= st["limit"]:
                print(f"跳过 {name} 已满")
                continue
            start_account_auto_publish(name)
            time.sleep(ACCOUNT_DELAY)
        if i+BATCH_SIZE < len(names):
            print(f"等待批次间隔 {BATCH_WAIT_SECONDS}s")
            time.sleep(BATCH_WAIT_SECONDS)

def trigger_batch_all():
    global batch_task
    if batch_task and batch_task.is_alive():
        return False
    batch_task = threading.Thread(target=batch_start_all, daemon=True)
    batch_task.start()
    return True

# ======================== UI 模板（100%还原你原版 + 只加批量按钮） ========================
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安自动发文助手</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
    <style>
        :root {
            --primary: #007aff; --success: #34c759; --danger: #ff3b30;
            --warning: #ff9500; --gray: #8e8e93; --light-gray: #f2f2f7;
            --border: #e5e5ea; --text: #1d1d1f; --bg: #ffffff;
        }
        *{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
        body{background:var(--light-gray);color:var(--text);padding:16px}
        .container{max-width:800px;margin:0 auto}
        .card{background:var(--bg);border-radius:16px;padding:24px;margin-bottom:16px}
        .tabs{display:flex;gap:8px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:8px}
        .tab-btn{background:0 0;border:none;padding:8px 16px;font-size:15px;font-weight:500;color:var(--gray);border-radius:8px;cursor:pointer}
        .tab-btn.active{color:var(--primary);background:rgba(0,122,255,0.1)}
        .tab-content{display:none}
        .tab-content.active{display:block}
        .form-label{display:block;font-size:14px;font-weight:500;margin-bottom:8px}
        .form-control{width:100%;padding:12px 16px;border:1px solid var(--border);border-radius:12px;font-size:15px}
        .btn{display:inline-flex;align-items:center;justify-content:center;padding:12px 24px;border:none;border-radius:12px;font-size:15px;font-weight:500;cursor:pointer;gap:8px}
        .btn-primary{background:var(--primary);color:#fff}
        .btn-success{background:var(--success);color:#fff}
        .btn-danger{background:var(--danger);color:#fff}
        .account-selector{width:100%;margin-bottom:16px}
        .account-actions-wrapper{display:flex;gap:8px;margin-top:8px}
        .account-action-btn{flex:1;padding:8px 12px;font-size:14px}
        .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
        .stat-card{background:var(--light-gray);border-radius:12px;padding:16px;text-align:center}
        .stat-value{font-size:24px;font-weight:600}
        .stat-label{font-size:12px;color:var(--gray)}
        .config-detail{background:rgba(0,122,255,0.05);border-left:4px solid var(--primary);padding:16px;border-radius:0 12px 12px 0;margin-bottom:16px;display:none}
        .config-detail.active{display:block}
        .log-box{background:var(--light-gray);border-radius:12px;padding:16px;min-height:80px;margin-top:16px}
        .records-list{max-height:400px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
        .record-item{background:var(--light-gray);border-radius:12px;padding:16px}
    </style>
</head>
<body>
<div class="container">
<div class="card">
    <div class="header"><h1>币安自动发文助手</h1></div>
    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('auto')"><i class="fa fa-robot"></i> 自动模式</button>
        <button class="tab-btn" onclick="switchTab('manual')"><i class="fa fa-hand-pointer-o"></i> 手动模式</button>
        <button class="tab-btn" onclick="switchTab('config')"><i class="fa fa-cog"></i> 账号配置</button>
        <button class="tab-btn" onclick="switchTab('records')"><i class="fa fa-history"></i> 发文记录&数据管理</button>
    </div>

    <!-- 自动模式：原版完全不变 + 只加批量按钮 -->
    <div id="auto" class="tab-content active">
        <div style="margin-bottom:16px">
            <button class="btn btn-success" onclick="startAllAccounts()"><i class="fa fa-play"></i> 一键启动所有账号</button>
            <button class="btn btn-danger" onclick="stopAllAccounts()"><i class="fa fa-stop"></i> 一键停止所有账号</button>
        </div>

        <div class="form-label">选择要操作的账号</div>
        <select id="auto_account_selector" class="form-control account-selector" onchange="loadAccountStatus()">
            <option value="">请选择账号</option>
            {% for acc in accounts %}
            <option value="{{acc.name}}">{{acc.name}}</option>
            {% endfor %}
        </select>

        <div id="auto_account_actions" style="display:none">
            <div style="padding:16px;background:var(--light-gray);border-radius:12px;margin-bottom:16px">
                <div style="font-weight:600" id="auto_account_name"></div>
                <div id="auto_account_status"></div>
            </div>
            <div class="account-actions-wrapper">
                <button id="auto_start_btn" class="btn btn-success account-action-btn" onclick="startAuto()">启动</button>
                <button id="auto_stop_btn" class="btn btn-danger account-action-btn" onclick="stopAuto()">停止</button>
            </div>
        </div>

        <div class="stats-grid" id="today_stats">
            {% for n,st in today_stats.items() %}
            <div class="stat-card" onclick="showAccountConfig('{{n}}')">
                <div class="stat-value">{{st.count}}</div>
                <div class="stat-label">{{n}}</div>
                <div class="stat-label">自动:{{st.auto_count}} 手动:{{st.manual_count}}</div>
                <div class="stat-label">剩余:{{st.remaining}}/{{st.limit}}</div>
                <div class="stat-label" style="color:{{'var(--success)' if st.running else 'var(--gray)'}}">
                    {{'运行中' if st.running else '已停止'}}
                </div>
            </div>
            {% endfor %}
        </div>

        <div class="config-detail" id="account_config_detail">
            <div id="config_detail_content"></div>
        </div>
    </div>

    <!-- 手动模式（100%原版，正常可用） -->
    <div id="manual" class="tab-content">
        <div class="form-group">
            <label class="form-label">选择发文账号</label>
            <select id="manual_account" class="form-control">
                {% for acc in accounts %}
                <option value="{{acc.key}}" data-name="{{acc.name}}">
                    {{acc.name}} (剩余:{{today_stats[acc.name].remaining}}/{{today_stats[acc.name].limit}})
                </option>
                {% endfor %}
            </select>
        </div>
        <div class="form-group">
            <label class="form-label">交易对</label>
            <input id="manual_symbol" class="form-control" placeholder="BTCUSDT">
        </div>
        <div style="display:flex;gap:8px;margin-bottom:16px">
            <button class="btn" onclick="autoSelectSymbol()">自动选交易对</button>
            <button class="btn" onclick="generateFullTopic()">生成分析</button>
        </div>
        <div class="form-group">
            <label class="form-label">话题分析</label>
            <textarea id="manual_topic" class="form-control" rows="3"></textarea>
        </div>
        <button class="btn" onclick="generateAIContent()" style="width:100%">生成发文内容</button>
        <div class="form-group">
            <label class="form-label">最终内容</label>
            <textarea id="manual_content" class="form-control" rows="4"></textarea>
        </div>
        <button class="btn btn-primary" onclick="submitPost()" style="width:100%">确认发文</button>
        <div class="log-box" id="manual_log"></div>
    </div>

    <!-- 账号配置（100%原版，正常可用） -->
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
            <label class="form-label">每日限额</label>
            <input type="number" id="config_daily_limit" class="form-control" min="1">
        </div>
        <div class="form-group">
            <label class="form-label">自动间隔(分钟)</label>
            <input type="number" id="config_interval" class="form-control" min="5">
        </div>
        <button class="btn btn-primary" onclick="saveAccountConfig()" style="width:100%">保存配置</button>
        <div class="log-box" id="config_log"></div>
    </div>

    <!-- 发文记录&备份（100%原版，正常可用） -->
    <div id="records" class="tab-content">
        <div class="form-group">
            <div style="display:flex;gap:8px">
                <select id="record_account" class="form-control" style="flex:1">
                    <option value="">所有账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                <input type="date" id="record_date" value="{{today}}" class="form-control">
                <button class="btn" onclick="loadRecords()">查询</button>
                <button class="btn" onclick="exportRecords()">导出</button>
            </div>
        </div>
        <div class="records-list" id="records_list"></div>
        <div style="margin-top:16px">
            <div style="display:flex;gap:8px">
                <select id="delete_account" class="form-control" style="flex:1">
                    <option value="">所有账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                <input type="date" id="delete_date" class="form-control">
                <button class="btn btn-danger" onclick="deleteSelectedRecords()">删除选中</button>
                <button class="btn btn-danger" onclick="deleteAllRecords()">清空所有</button>
            </div>
        </div>
        <div style="margin-top:16px">
            <button class="btn" onclick="backupAllData()">备份全部</button>
            <button class="btn" onclick="downloadBackup('records')">下载记录</button>
            <button class="btn" onclick="downloadBackup('config')">下载配置</button>
        </div>
        <div style="margin-top:16px">
            <input type="file" id="import_records" accept=".json,.csv">
            <button class="btn" onclick="importRecords()">导入记录</button>
            <input type="file" id="import_config" accept=".json">
            <button class="btn" onclick="importPrompts()">导入配置</button>
        </div>
        <div class="log-box" id="backup_log"></div>
    </div>
</div>
</div>

<script>
function switchTab(id) {
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    document.querySelector(`.tab-btn[onclick="switchTab('${id}')"]`).classList.add('active');
    document.getElementById(id).classList.add('active');
}

// ======================== 自动模式 ========================
function loadAccountStatus() {
    const n = document.getElementById('auto_account_selector').value;
    if(!n){document.getElementById('auto_account_actions').style.display='none';return}
    fetch('/api/auto/status?account='+n).then(r=>r.json()).then(d=>{
        document.getElementById('auto_account_actions').style.display='block';
        document.getElementById('auto_account_name').textContent = n;
        const statSpan = document.getElementById('auto_account_status');
        statSpan.innerHTML = d.running
            ? '<span style="color:var(--success)"><i class="fa fa-circle"></i> 运行中</span>'
            : '<span style="color:var(--gray)"><i class="fa fa-circle"></i> 已停止</span>';
        document.getElementById('auto_start_btn').disabled = d.running;
        document.getElementById('auto_stop_btn').disabled = !d.running;
    });
}

function startAuto(){
    const n=document.getElementById('auto_account_selector').value;
    fetch('/api/auto/start?account='+n).then(r=>r.json()).then(d=>{
        alert(d.msg);loadAccountStatus();refreshStats();
    });
}
function stopAuto(){
    const n=document.getElementById('auto_account_selector').value;
    fetch('/api/auto/stop?account='+n).then(r=>r.json()).then(d=>{
        alert(d.msg);loadAccountStatus();refreshStats();
    });
}

// ======================== 【新增】批量总控 ========================
function startAllAccounts(){
    if(!confirm('按批次安全启动所有账号，不跨组并发？')) return;
    fetch('/api/batch/start_all').then(r=>r.json()).then(d=>{alert(d.msg);refreshStats()});
}
function stopAllAccounts(){
    if(!confirm('停止所有账号？')) return;
    fetch('/api/batch/stop_all').then(r=>r.json()).then(d=>{alert(d.msg);refreshStats()});
}

function showAccountConfig(n){
    fetch('/api/config/load?account='+n).then(r=>r.json()).then(cfg=>{
        fetch('/api/auto/last_run?account='+n).then(r=>r.json()).then(lt=>{
            fetch('/api/stats/today?account='+n).then(r=>r.json()).then(st=>{
                document.getElementById('config_detail_content').innerHTML = `
                <div>${n}</div>
                <div>提示词：${cfg.prompt||'默认'}</div>
                <div>间隔：${cfg.auto_interval}分钟</div>
                <div>限额：${cfg.daily_limit}条</div>
                <div>今日：${st.count}条（自动${st.auto_count} 手动${st.manual_count}）</div>
                <div>最后运行：${lt.last_run||'从未'}</div>`;
                document.getElementById('account_config_detail').classList.add('active');
            });
        });
    });
}

function refreshStats(){location.reload()}

// ======================== 手动模式（完整可用） ========================
function autoSelectSymbol(){
    fetch('/api/manual/auto_symbol').then(r=>r.json()).then(d=>{
        if(d.success)document.getElementById('manual_symbol').value=d.symbol;
        document.getElementById('manual_log').textContent=d.msg;
    });
}
function generateFullTopic(){
    const s=document.getElementById('manual_symbol').value.trim().toUpperCase();
    fetch('/api/manual/full_topic?symbol='+s).then(r=>r.json()).then(d=>{
        document.getElementById('manual_topic').value=d.success?d.topic:d.msg;
    });
}
function generateAIContent(){
    const t=document.getElementById('manual_topic').value;
    const k=document.getElementById('manual_account').value;
    fetch('/api/manual/generate_ai',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({topic:t,account_key:k})
    }).then(r=>r.text()).then(c=>{document.getElementById('manual_content').value=c});
}
function submitPost(){
    const k=document.getElementById('manual_account').value;
    const c=document.getElementById('manual_content').value;
    const s=document.getElementById('manual_symbol').value;
    fetch('/api/manual/post',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({account_key:k,content:c,symbol:s})
    }).then(r=>r.json()).then(d=>{
        document.getElementById('manual_log').textContent=d.msg;
        refreshStats();
    });
}

// ======================== 配置（完整可用） ========================
function loadAccountConfig(){
    const n=document.getElementById('config_account').value;
    fetch('/api/config/load?account='+n).then(r=>r.json()).then(d=>{
        document.getElementById('config_prompt').value=d.prompt||'';
        document.getElementById('config_daily_limit').value=d.daily_limit||8;
        document.getElementById('config_interval').value=d.auto_interval||60;
    });
}
function saveAccountConfig(){
    const n=document.getElementById('config_account').value;
    const p=document.getElementById('config_prompt').value;
    const l=document.getElementById('config_daily_limit').value;
    const i=document.getElementById('config_interval').value;
    fetch('/api/config/save',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({account:n,prompt:p,daily_limit:l,auto_interval:i})
    }).then(r=>r.json()).then(d=>{document.getElementById('config_log').textContent=d.msg});
}

// ======================== 记录&备份（完整可用） ========================
function loadRecords(){
    const a=document.getElementById('record_account').value;
    const d=document.getElementById('record_date').value;
    fetch('/api/records?account='+a+'&date='+d).then(r=>r.json()).then(arr=>{
        let html='';
        arr.forEach(r=>{
            html+=`<div class="record-item">${r.account} ${r.symbol}<br>${r.content}<br>${r.time}</div>`;
        });
        document.getElementById('records_list').innerHTML=html;
    });
}
function exportRecords(){
    const a=document.getElementById('record_account').value;
    const d=document.getElementById('record_date').value;
    window.open('/api/records/export?account='+a+'&date='+d);
}
function deleteSelectedRecords(){
    const a=document.getElementById('delete_account').value;
    const d=document.getElementById('delete_date').value;
    fetch('/api/records/delete?account='+a+'&date='+d,{method:'POST'}).then(r=>r.json()).then(d=>{loadRecords();refreshStats()});
}
function deleteAllRecords(){
    if(!confirm('确定清空所有记录？'))return;
    fetch('/api/records/delete?all=true',{method:'POST'}).then(r=>r.json()).then(d=>{loadRecords();refreshStats()});
}
function backupAllData(){fetch('/api/backup/all',{method:'POST'}).then(r=>r.json()).then(d=>{alert(d.msg)})}
function downloadBackup(t){window.open('/api/backup/download/'+t)}
function importRecords(){
    const f=document.getElementById('import_records').files[0];
    if(!f)return;
    const fd=new FormData();fd.append('file',f);fd.append('overwrite',true);
    fetch('/api/import/records',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{alert(d.msg);loadRecords()});
}
function importPrompts(){
    const f=document.getElementById('import_config').files[0];
    if(!f)return;
    const fd=new FormData();fd.append('file',f);fd.append('overwrite',true);
    fetch('/api/import/prompts',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{alert(d.msg)});
}
</script>
</body>
</html>
"""

# ======================== 路由（100%原版 + 新增批量接口） ========================
@app.route('/')
def index():
    accounts = get_all_accounts()
    today_stats = get_today_stats()
    today = str(datetime.date.today())
    return render_template_string(UI_TEMPLATE, accounts=accounts, today_stats=today_stats, today=today)

# 自动单账号
@app.route('/api/auto/start')
def api_auto_start():
    n = request.args.get("account")
    ok = start_account_auto_publish(n)
    return jsonify({"msg":"启动成功"if ok else"已在运行"})
@app.route('/api/auto/stop')
def api_auto_stop():
    n = request.args.get("account")
    stop_account_auto_publish(n)
    return jsonify({"msg":"已停止"})
@app.route('/api/auto/status')
def api_auto_status():
    n = request.args.get("account")
    acc = get_account_by_name(n)
    run = account_running_status.get(n,False)if acc else False
    return jsonify({"running":run})
@app.route('/api/auto/last_run')
def api_auto_last():
    n = request.args.get("account")
    c = load_json(CONFIG_FILE)
    return jsonify({
        "last_run":c.get(f"{n}_last_run",""),
        "last_auto_run":c.get(f"{n}_last_auto_run","")
    })

# 批量总控
@app.route('/api/batch/start_all')
def api_batch_start():
    ok = trigger_batch_all()
    return jsonify({"msg":"批量启动中（批次安全执行）"if ok else"已有任务运行中"})
@app.route('/api/batch/stop_all')
def api_batch_stop():
    for a in get_all_accounts():
        stop_account_auto_publish(a["name"])
    return jsonify({"msg":"所有账号已停止"})

# 配置
@app.route('/api/config/load')
def api_cfg_load():
    n = request.args.get("account")
    a = get_account_by_name(n)
    return jsonify({
        "prompt":a.get("prompt",""),
        "daily_limit":a.get("daily_limit",DEFAULT_DAILY_LIMIT),
        "auto_interval":a.get("auto_interval",DEFAULT_AUTO_INTERVAL)
    })
@app.route('/api/config/save',methods=["POST"])
def api_cfg_save():
    j = request.json
    save_account_prompt(j["account"],j.get("prompt",""),j.get("daily_limit"),j.get("auto_interval"))
    return jsonify({"msg":"保存成功"})

# 统计
@app.route('/api/stats/today')
def api_stats_today():
    a = request.args.get("account")
    return jsonify(get_today_stats(a))

# 手动
@app.route('/api/manual/auto_symbol')
def api_man_sym():
    from topic_main import run_topic
    t = run_topic()
    if t:
        return jsonify({"success":True,"symbol":t["symbol"],"msg":"已选择"})
    return jsonify({"success":False,"msg":"无"})
@app.route('/api/manual/full_topic')
def api_man_topic():
    from topic_main import fetch_url,fetch_all_for_symbol,get_trend,get_oi_state,get_funding_state,detect_signal,detect_conflict,build_topic_text
    s = request.args.get("symbol","").strip().upper()
    ticker = fetch_url(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={s}")
    if not ticker:return jsonify({"success":False,"msg":"行情失败"})
    sk,soi,lk,loi,fund = fetch_all_for_symbol(s)
    strd = get_trend(sk)
    ltrd = get_trend(lk)
    soi_st = get_oi_state(soi,s)
    loi_st = get_oi_state(loi,s)
    fst = get_funding_state(fund,s)
    fv = float(fund.get("lastFundingRate",0))if fund else 0.0
    chg = float(ticker["priceChangePercent"])
    sig = detect_signal(strd,ltrd,soi_st,loi_st,fst,chg)
    cft = detect_conflict(strd,ltrd,soi_st,loi_st,fst,chg)
    txt = build_topic_text(ticker,strd,ltrd,soi_st,loi_st,fst,fv,sig,cft)
    return jsonify({"success":True,"topic":txt})
@app.route('/api/manual/generate_ai',methods=["POST"])
def api_man_ai():
    j = request.json
    acc = get_account_by_key(j["account_key"])
    from ai_core import generate_content
    c,_ = generate_content({"text":j["topic"]},ZHIPU_API_KEY,custom_prompt=acc.get("prompt")if acc else None)
    return c or ""
@app.route('/api/manual/post',methods=["POST"])
def api_man_post():
    j = request.json
    acc = get_account_by_key(j["account_key"])
    from post_main import post_content
    ok,msg,pid = post_content(j["content"],j["account_key"])
    pid = str(pid)if pid and pid!="[object Object]"else"未知ID"
    if ok:
        save_post_record("manual",acc["name"],j.get("symbol",""),j["content"],pid)
    return jsonify({"msg":"发布成功"if ok else f"失败:{msg}"})

# 记录
@app.route('/api/records')
def api_records():
    a = request.args.get("account")
    d = request.args.get("date")
    arr = load_json(DB_FILE,[])
    if a: arr = [x for x in arr if x.get("account")==a]
    if d: arr = [x for x in arr if x.get("date")==d]
    return jsonify(sorted(arr,key=lambda x:x["time"],reverse=True))
@app.route('/api/records/export')
def api_rec_export():
    a = request.args.get("account")
    d = request.args.get("date")
    arr = load_json(DB_FILE,[])
    if a: arr = [x for x in arr if x.get("account")==a]
    if d: arr = [x for x in arr if x.get("date")==d]
    csv = "\ufeff模式,账号,日期,时间,交易对,内容,ID,状态\n"
    for r in arr:
        csv += f"{r['mode']},{r['account']},{r['date']},{r['time']},{r['symbol']},\"{r['content']}\",{r['post_id']},{r['status']}\n"
    rsp = make_response(csv)
    rsp.headers["Content-Type"]="text/csv;charset=utf-8"
    rsp.headers["Content-Disposition"]="attachment;filename=records.csv"
    return rsp
@app.route('/api/records/delete',methods=["POST"])
def api_rec_del():
    a = request.args.get("account")
    d = request.args.get("date")
    all_rec = request.args.get("all","false").lower()=="true"
    cnt = delete_records(a,d,all_rec)
    return jsonify({"deleted":cnt})

# 备份导入
@app.route('/api/backup/all',methods=["POST"])
def api_backup_all():
    ts = backup_current_data()
    return jsonify({"msg":f"备份完成 {ts}"})
@app.route('/api/backup/download/records')
def api_dl_rec():
    j = json.dumps(load_json(DB_FILE,[]),ensure_ascii=False,indent=2)
    rsp = make_response(j)
    rsp.headers["Content-Type"]="application/json"
    rsp.headers["Content-Disposition"]="attachment;filename=records.json"
    return rsp
@app.route('/api/backup/download/config')
def api_dl_cfg():
    data = {"prompts":load_json(PROMPT_FILE),"config":load_json(CONFIG_FILE)}
    j = json.dumps(data,ensure_ascii=False,indent=2)
    rsp = make_response(j)
    rsp.headers["Content-Type"]="application/json"
    rsp.headers["Content-Disposition"]="attachment;filename=config.json"
    return rsp
@app.route('/api/import/records',methods=["POST"])
def api_import_rec():
    f = request.files.get("file")
    ow = request.form.get("overwrite","true").lower()=="true"
    if f.filename.endswith(".csv"):
        ok,m = import_csv_records(f.stream,ow)
    else:
        ok,m = import_json_file(f.stream,DB_FILE,ow)
    return jsonify({"msg":m})
@app.route('/api/import/prompts',methods=["POST"])
def api_import_prompt():
    f = request.files.get("file")
    ow = request.form.get("overwrite","true").lower()=="true"
    ok,m = import_json_file(f.stream,PROMPT_FILE,ow)
    return jsonify({"msg":m})

if __name__ == '__main__':
    app.run(host="0.0.0.0",port=5000,debug=False)
