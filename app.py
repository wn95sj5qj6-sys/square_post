from flask import Flask, render_template_string, request, jsonify, Response
import os
import json
import datetime
import threading
import time

app = Flask(__name__)

# 配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()
AUTO_INTERVAL_MINUTES = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DAILY_MAX_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

# 本地数据库（自动持久化）
DB_FILE = "data/records.json"
CONFIG_FILE = "data/config.json"
os.makedirs("data", exist_ok=True)

def load_db():
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_db(record):
    db = load_db()
    db.append(record)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {
            "auto_running": False,
            "last_account_index": 0,
            "today_count": 0,
            "last_date": str(datetime.date.today())
        }

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def get_accounts():
    accounts = []
    if not BINANCE_ACCOUNTS:
        return accounts
    for item in BINANCE_ACCOUNTS.split(","):
        item = item.strip()
        if "|" not in item:
            continue
        name_key = item.split("|", 1)
        if len(name_key) != 2:
            continue
        name, key = name_key
        name = name.strip()
        key = key.strip()
        if name and key:
            accounts.append({"name": name, "key": key})
    return accounts

def get_account_name_by_key(key):
    for acc in get_accounts():
        if acc["key"] == key:
            return acc["name"]
    return "未知账号"

# ------------------------------ 自动发文后台线程 ------------------------------
def auto_publisher():
    while True:
        cfg = load_config()
        if not cfg.get("auto_running"):
            time.sleep(3)
            continue

        accounts = get_accounts()
        if len(accounts) == 0:
            time.sleep(10)
            continue

        today = str(datetime.date.today())
        if cfg.get("last_date") != today:
            cfg["today_count"] = 0
            cfg["last_date"] = today
            save_config(cfg)

        if cfg.get("today_count", 0) >= DAILY_MAX_LIMIT:
            time.sleep(60)
            continue

        try:
            from topic_main import run_topic
            topic = run_topic()
            if not topic:
                time.sleep(10)
                continue

            idx = cfg.get("last_account_index", 0) % len(accounts)
            acc = accounts[idx]
            key = acc["key"]
            name = acc["name"]

            from ai_core import generate_content
            content, _ = generate_content(topic, ZHIPU_API_KEY)
            if not content:
                time.sleep(10)
                continue

            from post_main import post_content
            ok, msg, post_id = post_content(content, key)

            record = {
                "mode": "auto",
                "account": name,
                "date": today,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": topic.get("symbol", ""),
                "content": content,
                "post_id": post_id,
                "status": "success" if ok else "fail"
            }
            save_db(record)

            cfg["last_account_index"] = idx + 1
            cfg["today_count"] += 1
            save_config(cfg)
            time.sleep(AUTO_INTERVAL_MINUTES * 60)
        except Exception as e:
            time.sleep(10)

threading.Thread(target=auto_publisher, daemon=True).start()

# ------------------------------ 页面 ------------------------------
HOME_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>自动发文助手</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
<style>
    *{box-sizing:border-box}
    body{background:#fff;margin:0;padding:20px;font-family:Arial;color:#222}
    .box{max-width:500px;margin:0 auto}
    .card{background:#fff;border-radius:16px;padding:22px;box-shadow:0 4px 14px rgba(0,0,0,0.06);margin-bottom:20px}
    .title{font-size:20px;font-weight:bold;margin-bottom:18px}
    .label{font-size:14px;color:#555;margin:6px 0}
    input,select,textarea,button{width:100%;padding:14px;border-radius:12px;border:1px solid #ddd;margin-bottom:12px;font-size:15px}
    button{background:#007aff;color:white;border:none;font-weight:bold}
    .tab{display:flex;gap:10px;margin-bottom:16px}
    .tab button{flex:1;background:#f2f2f7;color:#007aff}
    .section{display:none}
    .section.active{display:block}
    #log{background:#f9f9f9;padding:16px;border-radius:12px;min-height:220px;white-space:pre-wrap}
</style>

<div class="box">
    <div class="card">
        <div class="title"><i class="fa fa-robot"></i> 发文助手</div>
        <div class="tab">
            <button onclick="tab('auto')">自动模式</button>
            <button onclick="tab('manual')">手动模式</button>
            <button onclick="tab('records')">记录</button>
        </div>

        <div id="auto" class="section active">
            <div class="label">自动状态</div>
            <input id="auto_stat" readonly>
            <button onclick="toggleAuto()">启动/停止</button>
            <div class="label">间隔：{{interval}}分钟 | 日上限：{{limit}}</div>
            <div class="label">今日自动已发：{{today}}条</div>
        </div>

        <div id="manual" class="section">
            <div class="label">选择账号</div>
            <select id="m_acc">
                {% for a in accounts %}
                <option value="{{a.key}}">{{a.name}}</option>
                {% endfor %}
            </select>
            <div class="label">交易对（手动输入）</div>
            <input id="m_sym" placeholder="如 BTCUSDT">
            <button onclick="getTopic()">生成话题</button>
            <div class="label">话题（可编辑）</div>
            <textarea id="m_topic" rows="4"></textarea>
            <button onclick="genAI()">生成发文内容</button>
            <div class="label">最终内容（可编辑）</div>
            <textarea id="m_content" rows="6"></textarea>
            <button onclick="doPost()">确认发文</button>
            <div id="log">等待操作...</div>
        </div>

        <div id="records" class="section">
            <div class="label">选择账号</div>
            <select id="r_acc">
                {% for a in accounts %}
                <option value="{{a.name}}">{{a.name}}</option>
                {% endfor %}
            </select>
            <div class="label">选择日期</div>
            <input type="date" id="r_date">
            <button onclick="loadRecords()">查询</button>
            <button onclick="exportCSV()">导出全量</button>
            <div id="r_list" style="max-height:300px;overflow-y:auto"></div>
        </div>
    </div>
</div>

<script>
    let current_tab = "auto";
    function tab(t){
        document.querySelectorAll(".section").forEach(s=>s.classList.remove("active"));
        document.getElementById(t).classList.add("active");
        current_tab = t;
    }

    async function toggleAuto(){
        await fetch("/api/toggle_auto");
        location.reload();
    }

    async function getTopic(){
        let sym = document.getElementById("m_sym").value;
        let r = await fetch("/api/manual_topic?sym="+sym);
        let t = await r.text();
        document.getElementById("m_topic").value = t;
    }

    async function genAI(){
        let topic = document.getElementById("m_topic").value;
        let r = await fetch("/api/manual_ai", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({topic:topic})
        });
        let c = await r.text();
        document.getElementById("m_content").value = c;
    }

    async function doPost(){
        let key = document.getElementById("m_acc").value;
        let content = document.getElementById("m_content").value;
        let res = await fetch("/api/manual_post", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({key:key, content:content})
        });
        let log = await res.text();
        document.getElementById("log").textContent = log;
    }

    async function loadRecords(){
        let acc = document.getElementById("r_acc").value;
        let date = document.getElementById("r_date").value;
        let r = await fetch("/api/records?acc="+acc+"&date="+date);
        let list = await r.json();
        let html = "";
        list.forEach(i=>{
            html+=`<div style='padding:10px;background:#f9f9f9;border-radius:10px;margin:8px 0'>
            <b>${i.symbol}</b><br>${i.time}<br>${i.content}</div>`;
        });
        document.getElementById("r_list").innerHTML = html || "暂无记录";
    }

    function exportCSV(){
        window.open("/api/export");
    }

    window.onload = function(){
        let log = localStorage.getItem("last_log");
        if(log) document.getElementById("log").textContent = log;
    }
</script>
"""

# ------------------------------ 接口 ------------------------------
@app.route('/')
def index():
    accounts = get_accounts()
    cfg = load_config()
    return render_template_string(
        HOME_PAGE,
        accounts=accounts,
        interval=AUTO_INTERVAL_MINUTES,
        limit=DAILY_MAX_LIMIT,
        today=cfg.get("today_count", 0),
        auto_stat="运行中" if cfg.get("auto_running") else "已停止"
    )

@app.route('/api/toggle_auto')
def toggle_auto():
    cfg = load_config()
    cfg["auto_running"] = not cfg.get("auto_running")
    save_config(cfg)
    return "ok"

@app.route('/api/manual_topic')
def manual_topic():
    from topic_main import get_single_symbol_topic
    sym = request.args.get("sym", "").strip()
    if not sym:
        return "请输入交易对"
    topic = get_single_symbol_topic(sym)
    return topic.get("text", "获取失败")

@app.route('/api/manual_ai', methods=['POST'])
def manual_ai():
    data = request.json
    topic_text = data.get("topic", "")
    if not topic_text:
        return "无话题"
    from ai_core import generate_content
    fake_topic = {"text": topic_text, "symbol": "", "change": 0}
    content, _ = generate_content(fake_topic, ZHIPU_API_KEY)
    return content or "生成失败"

@app.route('/api/manual_post', methods=['POST'])
def manual_post():
    data = request.json
    key = data.get("key", "").strip()
    content = data.get("content", "").strip()
    if not key or not content:
        return "参数缺失"
    from post_main import post_content
    ok, msg, post_id = post_content(content, key)
    account_name = get_account_name_by_key(key)
    record = {
        "mode": "manual",
        "account": account_name,
        "date": str(datetime.date.today()),
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": "手动",
        "content": content,
        "post_id": post_id,
        "status": "success" if ok else "fail"
    }
    save_db(record)
    return f"✅ 发文成功！ID：{post_id}" if ok else f"❌ 失败：{msg}"

@app.route('/api/records')
def api_records():
    acc = request.args.get("acc", "")
    date = request.args.get("date", "")
    db = load_db()
    out = [r for r in db if r.get("account")==acc and r.get("date")==date]
    return jsonify(out)

@app.route('/api/export')
def export_csv():
    db = load_db()
    csv = "\ufeff模式,账号,日期,时间,交易对,文章ID,内容\n"
    for r in db:
        csv += f"{r['mode']},{r['account']},{r['date']},{r['time']},{r['symbol']},{r['post_id']},\"{r['content'].replace('\"','')}\"\n"
    return Response(csv, mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=records.csv"})

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
