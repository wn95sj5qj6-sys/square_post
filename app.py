from flask import Flask, render_template_string, request, jsonify, Response
import os
import json
import datetime
import logging

logging.getLogger('werkzeug').setLevel(logging.ERROR)
app = Flask(__name__)

# ================= 配置 =================
DATA_FILE = "/tmp/records.json"
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()

def get_accounts():
    accounts = []
    for item in BINANCE_ACCOUNTS.split(","):
        item = item.strip()
        if "|" not in item:
            continue
        n, k = item.split("|", 1)
        n = n.strip()
        k = k.strip()
        if n and k:
            accounts.append({"name": n, "key": k})
    return accounts

def load_records():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_record(account_name, symbol, content, post_id):
    records = load_records()
    records.append({
        "account": account_name,
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.date.today().isoformat(),
        "symbol": symbol,
        "content": content,
        "post_id": post_id
    })
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def get_account_name_by_key(api_key):
    for acc in get_accounts():
        if acc["key"] == api_key:
            return acc["name"]
    return "未知账号"

# ================= 页面 =================
MAIN_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>币安广场发文助手</title>
<style>
    *{box-sizing:border-box}
    body{background:#121212;color:#eaeaea;font-family:Arial;padding:15px;margin:0}
    .card{max-width:480px;margin:0 auto;background:#1c1c1c;padding:20px;border-radius:14px;margin-bottom:12px}
    h2{color:#00dfff;text-align:center;margin:0 0 16px}
    select,button{width:100%;padding:14px;border-radius:10px;border:none;background:#272727;color:#fff;font-size:16px;margin-bottom:12px}
    button{background:#00dfff;color:#000;font-weight:bold;cursor:pointer}
    button:disabled{background:#444}
    #log{background:#111;padding:14px;border-radius:10px;min-height:240px;white-space:pre-wrap;font-size:14px;line-height:1.5}
    .nav{display:flex;gap:8px;margin-bottom:12px}
    .nav button{flex:1;background:#272727;color:#00dfff}
</style>

<div class="card">
    <div class="nav">
        <button onclick="location.href='/'">📤 发文</button>
        <button onclick="location.href='/records'">📋 记录</button>
    </div>
    <h2>自动发文</h2>
    <select id="acc">
        {% for a in accounts %}
        <option value="{{a.key}}">{{a.name}}</option>
        {% endfor %}
    </select>
    <button onclick="run()">🚀 开始发文</button>
    <div id="log">等待启动...</div>
</div>

<script>
    let log = document.getElementById('log');
    let btn = document.querySelector('button');
    function append(t){log.textContent+=t+'\\n';window.scrollTo(0,9999)}
    function run(){
        btn.disabled=true; log.textContent='';
        append('✅ 开始执行发文流程...');
        fetch('/run',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({key:document.getElementById('acc').value})
        }).then(r=>r.json()).then(d=>{
            append(d.log);
            btn.disabled=false;
        })
    }
</script>
"""

RECORD_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>发文记录</title>
<style>
    *{box-sizing:border-box}
    body{background:#121212;color:#eaeaea;font-family:Arial;padding:15px}
    .card{max-width:480px;margin:0 auto;background:#1c1c1c;padding:20px;border-radius:14px;margin-bottom:12px}
    h2{color:#00dfff;text-align:center}
    select,button,input{width:100%;padding:12px;border-radius:10px;border:none;background:#272727;color:#fff;margin-bottom:10px}
    button{background:#00dfff;color:#000;font-weight:bold;cursor:pointer}
    .item{background:#111;padding:10px;margin:8px 0;border-radius:8px;font-size:13px;line-height:1.4}
    .symbol{color:#00dfff}
    .time{color:#aaa;font-size:12px}
    .content{color:#ccc;margin-top:4px}
</style>

<div class="card">
    <h2>📋 发文记录</h2>
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

<script>
    let list = document.getElementById('list');
    let data = [];
    async function load(){
        let acc = document.getElementById('acc').value;
        let date = document.getElementById('date').value;
        let res = await fetch('/get_records?acc='+acc+'&date='+date);
        data = await res.json();
        show();
    }
    function show(){
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
    function exportCSV(){
        let res = await fetch('/export?acc='+document.getElementById('acc').value+'&date='+document.getElementById('date').value);
        let blob = await res.blob();
        let a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'record_'+document.getElementById('date').value+'.csv';
        a.click();
    }
</script>
"""

# ================= 接口 =================
@app.route('/')
def index():
    return render_template_string(MAIN_PAGE, accounts=get_accounts())

@app.route('/records')
def records():
    return render_template_string(RECORD_PAGE, accounts=get_accounts())

@app.route('/run', methods=['POST'])
def run():
    try:
        key = request.json.get('key','').strip()
        if not key or not ZHIPU_API_KEY:
            return jsonify({"log":"❌ 配置缺失"})
        log = ""
        from topic_main import run_topic
        from ai_core import generate_content
        from post_main import post_content

        log += "✅ 开始执行\n"
        topic = run_topic()
        log += f"✅ 抓取完成\n📢 交易对：{topic.get('symbol','')}\n"
        log += f"💰 价格：{topic.get('price','')}\n"
        log += f"📈 涨幅：{topic.get('change','')}%\n"
        log += f"🔥 趋势：{topic.get('trend','')}\n\n"

        log += "✅ AI写作中...\n"
        content,_ = generate_content(topic["text"], ZHIPU_API_KEY)
        if not content:
            log += "❌ AI生成失败"
            return jsonify({"log":log})
        log += "✅ AI生成完成\n\n"

        log += "✅ 发布中...\n"
        ok, msg, post_id = post_content(content, key)
        if ok:
            log += f"🎉 发布成功！\n{msg}"
            save_record(get_account_name_by_key(key), topic['symbol'], content, post_id)
        else:
            log += f"❌ 发布失败：{msg}"
        return jsonify({"log":log})
    except Exception as e:
        return jsonify({"log":f"❌ 异常：{str(e)}"})

@app.route('/get_records')
def get_records():
    acc = request.args.get('acc','')
    date = request.args.get('date','')
    res = []
    for r in load_records():
        if r.get('account')==acc and r.get('date')==date:
            res.append(r)
    return jsonify(res)

@app.route('/export')
def export_csv():
    acc = request.args.get('acc','')
    date = request.args.get('date','')
    csv = "账号,时间,交易对,文章ID,内容\n"
    for r in load_records():
        if r.get('account')==acc and r.get('date')==date:
            csv += f"{r['account']},{r['time']},{r['symbol']},{r['post_id']},{repr(r['content'])}\n"
    return Response(csv, mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=record.csv"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
