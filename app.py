from flask import Flask, render_template_string, request, jsonify, Response
import os
import json
import datetime
import logging

logging.getLogger('werkzeug').setLevel(logging.ERROR)
app = Flask(__name__)

# 使用项目目录下的 data 文件夹，保证数据持久化
DATA_DIR = "./data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
DATA_FILE = os.path.join(DATA_DIR, "records.json")

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()

def get_accounts():
    accounts = []
    for item in BINANCE_ACCOUNTS.split(","):
        item = item.strip()
        if "|" not in item: continue
        n, k = item.split("|", 1)
        accounts.append({"name": n.strip(), "key": k.strip()})
    return accounts

def load_records():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_record(account, symbol, content, post_id):
    now = datetime.datetime.now()
    records = load_records()
    records.append({
        "account": account,
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "symbol": symbol,
        "content": content,
        "post_id": post_id
    })
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def get_account_name(key):
    for acc in get_accounts():
        if acc["key"] == key:
            return acc["name"]
    return "未知"

# ------------------------------ 发文页面（日志本地缓存） ------------------------------
MAIN_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>币安广场发文助手</title>
<style>
    *{box-sizing:border-box}
    body{background:#121212;color:#fff;font-family:Arial;padding:15px;margin:0}
    .box{max-width:460px;margin:0 auto}
    .card{background:#1c1c1c;padding:20px;border-radius:14px;margin-bottom:12px}
    .title{color:#00dfff;font-size:18px;font-weight:bold;margin-bottom:14px}
    select,button{width:100%;padding:14px;border-radius:10px;border:none;background:#2a2a2a;color:#fff;font-size:15px;margin-bottom:10px}
    button{background:#00dfff;color:#000;font-weight:bold}
    button:disabled{background:#444}
    #log{background:#111;padding:14px;border-radius:10px;min-height:260px;white-space:pre-wrap;line-height:1.5;font-size:14px}
    .foot{display:flex;gap:8px;margin-top:12px}
    .foot button{flex:1;background:#2a2a2a;color:#00dfff}
</style>

<div class="box">
    <div class="card">
        <div class="title">📤 自动发文</div>
        <select id="acc">
            {% for a in accounts %}
            <option value="{{a.key}}">{{a.name}}</option>
            {% endfor %}
        </select>
        <button onclick="run()">🚀 开始发文</button>
        <button onclick="clearLog()">🗑️ 清空日志</button>
        <div id="log">等待启动...</div>
    </div>
    <div class="foot">
        <button onclick="location.href='/'">📤 发文</button>
        <button onclick="location.href='/records'">📋 记录</button>
    </div>
</div>

<script>
    let log = document.getElementById('log');
    let btn = document.querySelector('button[onclick="run()"]');

    // 页面加载时，读取本地保存的日志
    window.onload = function() {
        let saved = localStorage.getItem('lastLog');
        if (saved) {
            log.textContent = saved;
        }
    }

    function append(t){
        log.textContent += t + '\\n';
        localStorage.setItem('lastLog', log.textContent); // 每次追加都保存到本地
        window.scrollTo(0,9999);
    }

    function clearLog(){
        log.textContent = '';
        localStorage.removeItem('lastLog');
    }

    function run(){
        btn.disabled = true;
        log.textContent = '';
        localStorage.removeItem('lastLog'); // 重新发文时清空
        append('✅ 开始执行发文流程...');
        fetch('/run',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({key:document.getElementById('acc').value})
        }).then(res=>res.json()).then(data=>{
            append(data.log);
            btn.disabled = false;
        })
    }
</script>
"""

# ------------------------------ 记录页面 ------------------------------
RECORD_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>发文记录</title>
<style>
    *{box-sizing:border-box}
    body{background:#121212;color:#fff;font-family:Arial;padding:15px;margin:0}
    .box{max-width:460px;margin:0 auto}
    .card{background:#1c1c1c;padding:20px;border-radius:14px;margin-bottom:12px}
    .title{color:#00dfff;font-size:18px;font-weight:bold;margin-bottom:14px}
    select,input,button{width:100%;padding:12px;border-radius:10px;border:none;background:#2a2a2a;color:#fff;margin-bottom:8px}
    button{background:#00dfff;color:#000;font-weight:bold}
    .item{background:#111;padding:12px;border-radius:8px;margin-bottom:8px;font-size:13px;line-height:1.4}
    .item .symbol{color:#00dfff}
    .item .time{color:#aaa;font-size:12px}
    .item .content{color:#eee;margin-top:6px}
    .foot{display:flex;gap:8px;margin-top:12px}
    .foot button{flex:1;background:#2a2a2a;color:#00dfff}
</style>

<div class="box">
    <div class="card">
        <div class="title">📋 发文记录</div>
        <select id="acc">
            {% for a in accounts %}
            <option value="{{a.name}}">{{a.name}}</option>
            {% endfor %}
        </select>
        <input type="date" id="date">
        <button onclick="load()">🔍 查询</button>
        <button onclick="exportCSV()">📥 导出CSV</button>
        <div id="list"></div>
    </div>
    <div class="foot">
        <button onclick="location.href='/'">📤 发文</button>
        <button onclick="location.href='/records'">📋 记录</button>
    </div>
</div>

<script>
    let list = document.getElementById('list');
    async function load(){
        let acc = document.getElementById('acc').value;
        let date = document.getElementById('date').value;
        let res = await fetch('/get_records?acc='+acc+'&date='+date);
        let data = await res.json();
        let html = '';
        data.forEach(i=>{
            html += `
            <div class="item">
                <div class="symbol">${i.symbol}</div>
                <div class="time">${i.time}</div>
                <div class="content">${i.content}</div>
            </div>`;
        });
        list.innerHTML = html || '暂无记录';
    }
    async function exportCSV(){
        let acc = document.getElementById('acc').value;
        let date = document.getElementById('date').value;
        let res = await fetch('/export?acc='+acc+'&date='+date);
        let blob = await res.blob();
        let a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = '记录_'+date+'.csv';
        a.click();
    }
</script>
"""

# ------------------------------ 接口 ------------------------------
@app.route('/')
def index():
    return render_template_string(MAIN_PAGE, accounts=get_accounts())

@app.route('/records')
def records():
    return render_template_string(RECORD_PAGE, accounts=get_accounts())

@app.route('/run', methods=['POST'])
def run():
    try:
        key = request.json.get('key', '').strip()
        log = ""
        from topic_main import run_topic
        from ai_core import generate_content
        from post_main import post_content

        log += "✅ 开始执行\n"
        topic = run_topic()
        log += f"✅ 抓取完成\n📢 行情信息：\n{topic}\n\n"

        log += "✅ AI写作中...\n"
        content, _ = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            log += "❌ AI生成失败"
            return jsonify({"log": log})
        log += "✅ AI生成完成\n\n"

        log += "✅ 发布中...\n"
        ok, msg, post_id = post_content(content, key)
        if ok:
            log += f"🎉 发布成功！\n{msg}"
            save_record(get_account_name(key), "自动抓取", content, post_id)
        else:
            log += f"❌ 发布失败：{msg}"
        return jsonify({"log": log})
    except Exception as e:
        return jsonify({"log": f"❌ 异常：{str(e)}"})

@app.route('/get_records')
def get_records():
    acc = request.args.get('acc')
    date = request.args.get('date')
    res = [r for r in load_records() if r.get('account')==acc and r.get('date')==date]
    return jsonify(res)

@app.route('/export')
def export():
    acc = request.args.get('acc')
    date = request.args.get('date')
    csv = "账号,时间,交易对,文章ID,内容\n"
    for r in load_records():
        if r.get('account')==acc and r.get('date')==date:
            csv += f"{r['account']},{r['time']},{r['symbol']},{r['post_id']},{repr(r['content'])}\n"
    return Response(csv, mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=record.csv"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
