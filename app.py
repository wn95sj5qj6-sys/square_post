from flask import Flask, render_template_string, request, jsonify, make_response
import os
import json
import datetime
import threading
import time
import urllib.parse
import csv

app = Flask(__name__)

# ======================== 核心配置 ========================
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

# 批次执行配置（你要的总控开关策略）
BATCH_SIZE = 2               # 每组几个账号
BATCH_INTERVAL_SECONDS = 15  # 每组跑完等多久再下一组（秒）
ACCOUNT_DELAY_SECONDS = 3    # 组内每个账号之间间隔（秒）

# 数据存储路径
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/records.json"
CONFIG_FILE = f"{DATA_DIR}/config.json"
PROMPT_FILE = f"{DATA_DIR}/prompts.json"
BACKUP_DIR = f"{DATA_DIR}/backups"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# 多账号运行状态
account_running_status = {}
status_lock = threading.Lock()

# 全局总控任务（防止重复点）
global_batch_task = None

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
    if os.path.exists(file_path):
        backup_name = f"{os.path.basename(file_path)}.backup.{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"
        backup_path = os.path.join(BACKUP_DIR, backup_name)
        with open(file_path, "r", encoding="utf-8") as f:
            with open(backup_path, "w", encoding="utf-8") as bf:
                bf.write(f.read())
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
            "daily_limit": acc_config.get("daily_limit", DEFAULT_DAILY_LIMIT),
            "auto_interval": acc_config.get("auto_interval", DEFAULT_AUTO_INTERVAL),
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

def save_account_prompt(account_name, prompt, daily_limit, auto_interval):
    prompts = load_json(PROMPT_FILE)
    prompts[account_name] = {
        "prompt": prompt,
        "daily_limit": int(daily_limit),
        "auto_interval": int(auto_interval)
    }
    save_json(PROMPT_FILE, prompts)

# ======================== 发文记录 ========================
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
    stats = {acc["name"]: {
        "count": 0, "auto_count": 0, "manual_count": 0,
        "limit": acc["daily_limit"], "remaining": acc["daily_limit"], "running": acc["running"]
    } for acc in get_all_accounts()}

    for r in db:
        if r.get("date") == today and r.get("status") == "success":
            acc = r.get("account")
            if acc in stats:
                stats[acc]["count"] += 1
                if r.get("mode") == "auto":
                    stats[acc]["auto_count"] += 1
                else:
                    stats[acc]["manual_count"] += 1
                stats[acc]["remaining"] = stats[acc]["limit"] - stats[acc]["count"]
    return stats.get(account_name) if account_name else stats

def delete_records(account=None, date=None, all_records=False):
    db = load_json(DB_FILE, [])
    if all_records:
        new_db = []
    else:
        new_db = [r for r in db if not (
            (account and r.get("account") == account and (not date or r.get("date") == date)) or
            (date and r.get("date") == date and not account)
        )]
    save_json(DB_FILE, new_db)
    return len(db) - len(new_db)

# ======================== 单账号自动发文 ========================
def auto_publisher_worker(account_name):
    while True:
        with status_lock:
            if not account_running_status.get(account_name, False):
                break

        acc = get_account_by_name(account_name)
        if not acc:
            time.sleep(10)
            continue

        today = get_today_stats(account_name)
        if today["count"] >= today["limit"]:
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
            content, _ = generate_content(topic, ZHIPU_API_KEY, custom_prompt=acc.get("prompt"))
            if not content:
                time.sleep(10)
                continue

            from post_main import post_content
            ok, msg, post_id = post_content(content, acc["key"])
            post_id_str = str(post_id) if post_id and post_id != "[object Object]" else "未知ID"

            if ok:
                save_post_record("auto", account_name, topic.get("symbol", ""), content, post_id_str)
                cfg = load_json(CONFIG_FILE)
                cfg[f"{account_name}_last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cfg[f"{account_name}_last_auto_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                save_json(CONFIG_FILE, cfg)
            else:
                save_post_record("auto", account_name, topic.get("symbol", ""), content, post_id_str, "fail")
            time.sleep(acc["auto_interval"] * 60)
        except Exception as e:
            print(f"[{account_name}] 异常：{e}")
            time.sleep(10)
    print(f"[{account_name}] 自动线程结束")

def start_account_auto_publish(account_name):
    with status_lock:
        if account_running_status.get(account_name, False):
            return False
        account_running_status[account_name] = True
    threading.Thread(target=auto_publisher_worker, args=(account_name,), daemon=True).start()
    return True

def stop_account_auto_publish(account_name):
    with status_lock:
        account_running_status[account_name] = False
    return True

# ======================== 【核心新增】总控：一键启动所有账号（批次串行无并发） ========================
def batch_run_all_accounts():
    accounts = get_all_accounts()
    enabled = [acc["name"] for acc in accounts]

    for i in range(0, len(enabled), BATCH_SIZE):
        batch = enabled[i:i+BATCH_SIZE]
        print(f"\n=== 开始执行批次 {i//BATCH_SIZE+1}，账号：{batch} ===")

        for name in batch:
            try:
                today = get_today_stats(name)
                if today["count"] >= today["limit"]:
                    print(f"⏭️ {name} 今日已满，跳过")
                    continue
                print(f"▶️ 启动账号：{name}")
                start_account_auto_publish(name)
                time.sleep(ACCOUNT_DELAY_SECONDS)
            except Exception as e:
                print(f"❌ {name} 启动失败：{e}")

        if i + BATCH_SIZE < len(enabled):
            print(f"⏳ 批次结束，等待 {BATCH_INTERVAL_SECONDS} 秒进入下一组...")
            time.sleep(BATCH_INTERVAL_SECONDS)
    print("\n✅ 所有批次启动完成")

def start_all_accounts():
    global global_batch_task
    if global_batch_task and global_batch_task.is_alive():
        return False
    global_batch_task = threading.Thread(target=batch_run_all_accounts, daemon=True)
    global_batch_task.start()
    return True

def stop_all_accounts():
    for acc in get_all_accounts():
        stop_account_auto_publish(acc["name"])
    return True

# ======================== UI模板（新增总控开关） ========================
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安自动发文助手</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
    <style>
        :root {--primary:#007aff;--success:#34c759;--danger:#ff3b30;--warning:#ff9500;--gray:#8e8e93;--light:#f2f2f7;}
        *{box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,SegoeUI,Roboto,sans-serif}
        body{background:var(--light);padding:16px}
        .container{max-width:800px;margin:0 auto}
        .card{background:white;border-radius:16px;padding:20px;margin-bottom:16px}
        .tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
        .tab-btn{padding:8px 12px;border:none;border-radius:8px;background:var(--light);cursor:pointer}
        .tab-btn.active{background:var(--primary);color:white}
        .tab-content{display:none}
        .tab-content.active{display:block}
        .btn{padding:10px 16px;border-radius:10px;border:none;margin-right:6px;margin-bottom:6px;cursor:pointer}
        .btn-primary{background:var(--primary);color:white}
        .btn-success{background:var(--success);color:white}
        .btn-danger{background:var(--danger);color:white}
        .btn-warning{background:var(--warning);color:white}
        .form-control{width:100%;padding:10px;border:1px solid #ddd;border-radius:10px;margin-bottom:10px}
        .stats-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:10px 0}
        .stat-card{background:var(--light);padding:12px;border-radius:12px;text-align:center}
        .records-list{max-height:360px;overflow-y:auto}
        .record-item{background:var(--light);padding:12px;border-radius:12px;margin-bottom:8px}
        .log-box{background:var(--light);padding:12px;border-radius:12px;margin-top:10px;min-height:60px}
    </style>
</head>
<body>
<div class="container">
<div class="card">
    <h2>币安自动发文助手</h2>
    <div class="tabs">
        <button class="tab-btn active" onclick="tab('auto')">自动模式</button>
        <button class="tab-btn" onclick="tab('manual')">手动发文</button>
        <button class="tab-btn" onclick="tab('config')">账号配置</button>
        <button class="tab-btn" onclick="tab('records')">记录&备份</button>
    </div>

    <!-- 自动模式 + 总控开关 -->
    <div id="auto" class="tab-content active">
        <div style="margin-bottom:16px">
            <button class="btn btn-success" onclick="startAll()"><i class="fa fa-play"></i> 一键启动所有账号</button>
            <button class="btn btn-danger" onclick="stopAll()"><i class="fa fa-stop"></i> 一键停止所有账号</button>
        </div>

        <div class="form-label">选择账号单独控制</div>
        <select class="form-control" id="accountSelector" onchange="loadStatus()">
            <option value="">选择账号</option>
            {% for acc in accounts %}
            <option value="{{acc.name}}">{{acc.name}}</option>
            {% endfor %}
        </select>

        <div id="accountPanel" style="display:none;margin-top:12px">
            <div id="statusText"></div>
            <button class="btn btn-success" id="btnStart" onclick="startOne()">启动</button>
            <button class="btn btn-danger" id="btnStop" onclick="stopOne()">停止</button>
        </div>

        <div class="stats-grid">
            {% for name, stat in today.items() %}
            <div class="stat-card">
                <div>{{name}}</div>
                <div>今日：{{stat.count}} / {{stat.limit}}</div>
                <div style="color:{{'#34c759' if stat.running else '#ff3b30'}}">
                    {{'运行中' if stat.running else '已停止'}}
                </div>
            </div>
            {% endfor %}
        </div>
    </div>

    <!-- 其余标签页逻辑不变 -->
    <div id="manual" class="tab-content">
        <select class="form-control" id="manualAccount">
            {% for acc in accounts %}
            <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}}</option>
            {% endfor %}
        </select>
        <input class="form-control" id="symbol" placeholder="交易对">
        <button class="btn btn-primary" onclick="genTopic()">生成分析</button>
        <textarea class="form-control" id="topic" rows="3"></textarea>
        <button class="btn btn-warning" onclick="genAI()">生成内容</button>
        <textarea class="form-control" id="content" rows="4"></textarea>
        <button class="btn btn-success" onclick="submitPost()">发布</button>
        <div class="log-box" id="manualLog"></div>
    </div>

    <div id="config" class="tab-content">
        <select class="form-control" id="cfgAccount" onchange="loadCfg()">
            {% for acc in accounts %}
            <option value="{{acc.name}}">{{acc.name}}</option>
            {% endfor %}
        </select>
        <textarea class="form-control" id="cfgPrompt" placeholder="自定义提示词"></textarea>
        <input class="form-control" id="cfgLimit" placeholder="每日条数">
        <input class="form-control" id="cfgInterval" placeholder="自动间隔分钟">
        <button class="btn btn-primary" onclick="saveCfg()">保存</button>
        <div class="log-box" id="cfgLog"></div>
    </div>

    <div id="records" class="tab-content">
        <select class="form-control" id="recAccount">
            <option value="">全部</option>
            {% for acc in accounts %}
            <option value="{{acc.name}}">{{acc.name}}</option>
            {% endfor %}
        </select>
        <input type="date" class="form-control" id="recDate" value="{{todayDate}}">
        <button class="btn" onclick="loadRec()">查询</button>
        <button class="btn" onclick="exportRec()">导出CSV</button>
        <div class="records-list" id="recList"></div>
    </div>
</div>
</div>

<script>
function tab(id) {
    document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'))
    document.querySelectorAll('.tab-btn').forEach(el=>el.classList.remove('active'))
    document.getElementById(id).classList.add('active')
    event.target.classList.add('active')
}

function loadStatus() {
    let name = document.getElementById('accountSelector').value
    if (!name) return
    fetch('/api/account/status?name='+name).then(r=>r.json()).then(d=>{
        document.getElementById('accountPanel').style.display = 'block'
        document.getElementById('statusText').innerHTML = `状态：${d.running?'运行中':'已停止'} | 今日：${d.today.count}/${d.today.limit}`
        document.getElementById('btnStart').disabled = d.running
        document.getElementById('btnStop').disabled = !d.running
    })
}

function startOne() {
    let name = document.getElementById('accountSelector').value
    fetch('/api/account/start?name='+name).then(r=>r.json()).then(d=>{
        alert(d.msg)
        loadStatus()
    })
}

function stopOne() {
    let name = document.getElementById('accountSelector').value
    fetch('/api/account/stop?name='+name).then(r=>r.json()).then(d=>{
        alert(d.msg)
        loadStatus()
    })
}

function startAll() {
    if (!confirm('确定批量启动所有账号？将按批次依次执行，不会并发调用API')) return
    fetch('/api/all/start').then(r=>r.json()).then(d=>{
        alert(d.msg)
    })
}

function stopAll() {
    if (!confirm('停止所有账号自动发文？')) return
    fetch('/api/all/stop').then(r=>r.json()).then(d=>{
        alert(d.msg)
    })
}

function loadRec() {
    let a = document.getElementById('recAccount').value
    let d = document.getElementById('recDate').value
    fetch('/api/records?account='+a+'&date='+d).then(r=>r.json()).then(arr=>{
        let html = ''
        arr.forEach(r=>{
            html += `<div class="record-item">${r.account} ${r.symbol}<br>${r.content}</div>`
        })
        document.getElementById('recList').innerHTML = html
    })
}

function exportRec() {
    let a = document.getElementById('recAccount').value
    let d = document.getElementById('recDate').value
    window.open('/api/records/export?account='+a+'&date='+d)
}

function genTopic() {
    let s = document.getElementById('symbol').value
    fetch('/api/manual/topic?symbol='+s).then(r=>r.json()).then(d=>{
        document.getElementById('topic').value = d.topic
    })
}

function genAI() {
    let t = document.getElementById('topic').value
    let k = document.getElementById('manualAccount').value
    fetch('/api/manual/ai', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({topic:t, key:k})
    }).then(r=>r.text()).then(t=>{
        document.getElementById('content').value = t
    })
}

function submitPost() {
    let k = document.getElementById('manualAccount').value
    let c = document.getElementById('content').value
    let s = document.getElementById('symbol').value
    fetch('/api/manual/post', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({key:k, content:c, symbol:s})
    }).then(r=>r.json()).then(d=>{
        document.getElementById('manualLog').innerText = d.msg
    })
}

function loadCfg() {
    let n = document.getElementById('cfgAccount').value
    fetch('/api/config/load?name='+n).then(r=>r.json()).then(d=>{
        document.getElementById('cfgPrompt').value = d.prompt||''
        document.getElementById('cfgLimit').value = d.daily_limit||8
        document.getElementById('cfgInterval').value = d.auto_interval||60
    })
}

function saveCfg() {
    let n = document.getElementById('cfgAccount').value
    let p = document.getElementById('cfgPrompt').value
    let l = document.getElementById('cfgLimit').value
    let i = document.getElementById('cfgInterval').value
    fetch('/api/config/save', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:n, prompt:p, daily_limit:l, auto_interval:i})
    }).then(r=>r.json()).then(d=>{
        document.getElementById('cfgLog').innerText = d.msg
    })
}

window.onload = () => { tab('auto') }
</script>
</body>
</html>
'''

# ======================== 路由 ========================
@app.route('/')
def index():
    accounts = get_all_accounts()
    today = get_today_stats()
    today_date = str(datetime.date.today())
    return render_template_string(HTML_TEMPLATE,
        accounts=accounts, today=today, todayDate=today_date)

# 单账号控制
@app.route('/api/account/status')
def api_account_status():
    name = request.args.get('name')
    acc = get_account_by_name(name)
    running = account_running_status.get(name, False)
    today = get_today_stats(name)
    return jsonify({"running": running, "today": today})

@app.route('/api/account/start')
def api_account_start():
    name = request.args.get('name')
    ok = start_account_auto_publish(name)
    return jsonify({"msg": "启动成功" if ok else "已在运行"})

@app.route('/api/account/stop')
def api_account_stop():
    name = request.args.get('name')
    stop_account_auto_publish(name)
    return jsonify({"msg": "已停止"})

# 总控开关
@app.route('/api/all/start')
def api_all_start():
    ok = start_all_accounts()
    return jsonify({"msg": "批量启动已开始，按批次安全执行" if ok else "已有批量任务在运行"})

@app.route('/api/all/stop')
def api_all_stop():
    stop_all_accounts()
    return jsonify({"msg": "所有账号已停止"})

# 配置
@app.route('/api/config/load')
def api_config_load():
    name = request.args.get('name')
    acc = get_account_by_name(name)
    return jsonify({
        "prompt": acc.get("prompt", ""),
        "daily_limit": acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": acc.get("auto_interval", DEFAULT_AUTO_INTERVAL)
    })

@app.route('/api/config/save', methods=['POST'])
def api_config_save():
    d = request.json
    save_account_prompt(d["name"], d.get("prompt",""), d.get("daily_limit",8), d.get("auto_interval",60))
    return jsonify({"msg":"保存成功"})

# 手动
@app.route('/api/manual/topic')
def api_manual_topic():
    from topic_main import run_topic
    t = run_topic()
    return jsonify({"topic": t.get("text","")})

@app.route('/api/manual/ai', methods=['POST'])
def api_manual_ai():
    d = request.json
    acc = get_account_by_key(d["key"])
    from ai_core import generate_content
    c, _ = generate_content({"text":d["topic"]}, ZHIPU_API_KEY, custom_prompt=acc.get("prompt"))
    return c or ""

@app.route('/api/manual/post', methods=['POST'])
def api_manual_post():
    d = request.json
    acc = get_account_by_key(d["key"])
    from post_main import post_content
    ok, msg, pid = post_content(d["content"], d["key"])
    pid = str(pid) if pid and pid != "[object Object]" else "未知ID"
    if ok:
        save_post_record("manual", acc["name"], d.get("symbol",""), d["content"], pid)
    return jsonify({"msg": msg if ok else "发布失败："+msg})

# 记录
@app.route('/api/records')
def api_records():
    account = request.args.get("account")
    date = request.args.get("date")
    recs = load_json(DB_FILE, [])
    if account:
        recs = [r for r in recs if r.get("account") == account]
    if date:
        recs = [r for r in recs if r.get("date") == date]
    return jsonify(sorted(recs, key=lambda x:x["time"], reverse=True))

@app.route('/api/records/export')
def api_records_export():
    account = request.args.get("account")
    date = request.args.get("date")
    recs = load_json(DB_FILE, [])
    if account:
        recs = [r for r in recs if r.get("account") == account]
    if date:
        recs = [r for r in recs if r.get("date") == date]
    csv_content = "\ufeff账号,模式,日期,时间,交易对,内容,ID\n"
    for r in recs:
        csv_content += f"{r['account']},{r['mode']},{r['date']},{r['time']},{r['symbol']},\"{r['content']}\",{r['post_id']}\n"
    resp = make_response(csv_content)
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = "attachment; filename=records.csv"
    return resp

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000, debug=False)
