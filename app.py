from flask import Flask, render_template_string, request, jsonify, Response
import os
import json
import datetime

app = Flask(__name__)
app.secret_key = "binance123"

# 配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()

def get_account_list():
    accounts = []
    for item in BINANCE_ACCOUNTS.split(","):
        item = item.strip()
        if "|" not in item:
            continue
        name, key = item.split("|", 1)
        accounts.append({"name": name.strip(), "key": key.strip()})
    return accounts

# 发文页面（白底 + 美观UI）
INDEX_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>币安广场发文助手</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
<style>
    *{box-sizing:border-box}
    body{background:#f7f8fa;color:#333;font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto sans-serif;margin:0;padding:16px}
    .container{max-width:500px;margin:0 auto}
    .card{background:#fff;border-radius:16px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,0.08);margin-bottom:16px}
    .title{font-size:18px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
    .form-item{margin-bottom:12px}
    select,button{width:100%;padding:14px;border-radius:12px;border:1px solid #eee;font-size:15px}
    button{background:#007aff;color:#fff;border:none;font-weight:500;cursor:pointer}
    button.secondary{background:#f2f2f7;color:#333}
    #log{background:#fbfbfb;padding:16px;border-radius:12px;min-height:240px;white-space:pre-wrap;line-height:1.6;font-size:14px;border:1px solid #eee}
    .nav{display:flex;gap:12px;margin-top:8px}
    .nav button{flex:1}
</style>

<div class="container">
    <div class="card">
        <div class="title"><i class="fa fa-paper-plane"></i> 自动发文</div>
        <select id="account">
            {% for acc in accounts %}
            <option value="{{acc.key}}">{{acc.name}}</option>
            {% endfor %}
        </select>
        <button onclick="startPublish()"><i class="fa fa-rocket"></i> 开始发文</button>
        <button class="secondary" onclick="clearLog()"><i class="fa fa-trash"></i> 清空日志</button>
        <div id="log">等待操作...</div>
    </div>

    <div class="nav">
        <button class="secondary" onclick="location.href='/'"><i class="fa fa-home"></i> 发文</button>
        <button class="secondary" onclick="location.href='/records'"><i class="fa fa-history"></i> 记录</button>
    </div>
</div>

<script>
const logBox = document.getElementById('log');

// 加载本地日志
window.onload = () => {
    const log = localStorage.getItem('publish_log');
    if(log) logBox.textContent = log;
}

function appendLog(text){
    logBox.textContent += text + '\n';
    localStorage.setItem('publish_log', logBox.textContent);
    window.scrollTo(0,9999);
}

function clearLog(){
    logBox.textContent = '';
    localStorage.removeItem('publish_log');
}

function startPublish(){
    const key = document.getElementById('account').value;
    logBox.textContent = '';
    localStorage.removeItem('publish_log');
    appendLog('✅ 开始执行');

    fetch('/run', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({api_key: key})
    }).then(res=>res.json()).then(data=>{
        appendLog(data.log);
    });
}
</script>
"""

# 记录页面（本地数据库 + 全量导出 + 日期正常）
RECORD_PAGE = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>发文记录</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
<style>
    *{box-sizing:border-box}
    body{background:#f7f8fa;color:#333;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;padding:16px}
    .container{max-width:500px;margin:0 auto}
    .card{background:#fff;border-radius:16px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,0.08);margin-bottom:16px}
    .title{font-size:18px;font-weight:600;margin-bottom:16px;display:flex;align-items:center;gap:8px}
    select,input,button{width:100%;padding:14px;border-radius:12px;border:1px solid #eee;font-size:15px;margin-bottom:10px}
    button{background:#007aff;color:#fff;border:none;font-weight:500}
    button.secondary{background:#f2f2f7;color:#333}
    .item{background:#fbfbfb;padding:14px;border-radius:12px;margin-bottom:10px;border:1px solid #eee}
    .item .title{font-size:15px;margin:0;color:#007aff}
    .item .time{font-size:12px;color:#888;margin-bottom:8px}
    .item .content{font-size:14px;line-height:1.5}
    .nav{display:flex;gap:12px;margin-top:8px}
    .nav button{flex:1}
</style>

<div class="container">
    <div class="card">
        <div class="title"><i class="fa fa-history"></i> 发文记录</div>
        <select id="acc">
            {% for acc in accounts %}
            <option value="{{acc.name}}">{{acc.name}}</option>
            {% endfor %}
        </select>
        <input type="date" id="date">
        <button onclick="loadRecords()"><i class="fa fa-search"></i> 查询</button>
        <button onclick="exportAll()"><i class="fa fa-download"></i> 全量导出CSV</button>
        <div id="list"></div>
    </div>

    <div class="nav">
        <button class="secondary" onclick="location.href='/'"><i class="fa fa-home"></i> 发文</button>
        <button class="secondary" onclick="location.href='/records'"><i class="fa fa-history"></i> 记录</button>
    </div>
</div>

<script>
// 前端数据库（永久保存）
function getDB(){
    return JSON.parse(localStorage.getItem('publish_records') || '[]')
}
function saveDB(item){
    const db = getDB();
    db.push(item);
    localStorage.setItem('publish_records', JSON.stringify(db));
}

// 接收后端推送的记录
window.addEventListener('load',()=>{
    const last = sessionStorage.getItem('last_record');
    if(last){
        const data = JSON.parse(last);
        saveDB(data);
        sessionStorage.removeItem('last_record');
    }
});

async function loadRecords(){
    const acc = document.getElementById('acc').value;
    const date = document.getElementById('date').value;
    const db = getDB();
    const list = document.getElementById('list');
    const filtered = db.filter(i=>i.account === acc && i.date === date);
    let html = '';
    filtered.forEach(i=>{
        html += `
        <div class="item">
            <div class="title">${i.symbol}</div>
            <div class="time">${i.time}</div>
            <div class="content">${i.content}</div>
        </div>`
    });
    list.innerHTML = html || '暂无记录';
}

function exportAll(){
    const db = getDB();
    let csv = '\ufeff账号,日期,时间,交易对,文章ID,内容\n';
    db.forEach(i=>{
        csv += `${i.account},${i.date},${i.time},${i.symbol},${i.post_id},"${i.content.replace(/"/g,'')}"\n`
    });
    const blob = new Blob([csv], {type:'text/csv'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = '全量发文记录_'+new Date().toISOString().split('T')[0]+'.csv';
    a.click();
}
</script>
"""

# 接口
@app.route('/')
def index():
    return render_template_string(INDEX_PAGE, accounts=get_account_list())

@app.route('/records')
def records():
    return render_template_string(RECORD_PAGE, accounts=get_account_list())

@app.route('/run', methods=['POST'])
def run():
    try:
        key = request.json.get('api_key', '').strip()
        log = ""

        from topic_main import run_topic
        from ai_core import generate_content
        from post_main import post_content

        log += "✅ 开始执行\n"
        topic = run_topic()
        log += f"📢 抓取完成：\n{topic}\n\n"

        log += "✅ AI写作中\n"
        content, _ = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            return {"log": log + "❌ AI生成失败"}

        log += "✅ 内容生成完成\n\n"
        log += "✅ 正在发布\n"

        ok, msg, post_id = post_content(content, key)

        # 自动记录结构
        account_name = next((a['name'] for a in get_account_list() if a['key'] == key), "未知")
        now = datetime.datetime.now()
        record = {
            "account": account_name,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": "自动行情",
            "content": content,
            "post_id": post_id
        }

        if ok:
            log += f"\n🎉 发文成功！文章ID：{post_id}"
        else:
            log += f"\n❌ 发文失败：{msg}"

        # 传给前端存入本地数据库
        from flask import session
        session['last_record'] = json.dumps(record, ensure_ascii=False)

        return {"log": log}

    except Exception as e:
        return {"log": f"❌ 异常：{str(e)}"}

# 供前端同步记录（解决跨页面传递）
@app.route('/sync_last')
def sync_last():
    from flask import session
    return jsonify(session.pop('last_record', ''))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
