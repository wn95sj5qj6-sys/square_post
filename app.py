from flask import Flask, render_template_string, request, jsonify, Response
import os
import datetime

app = Flask(__name__)

# 配置
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()

def get_accounts():
    accounts = []
    if not BINANCE_ACCOUNTS:
        return accounts
    for item in BINANCE_ACCOUNTS.split(","):
        item = item.strip()
        if "|" not in item:
            continue
        name, key = item.split("|", 1)
        accounts.append({"name": name.strip(), "key": key.strip()})
    return accounts

# ------------------------------ 发文页面（纯白、简洁、按钮必响应） ------------------------------
INDEX_HTML = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>发文助手</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
<style>
    *{box-sizing:border-box}
    body{background:#ffffff;margin:0;padding:20px;font-family:Arial;color:#222}
    .box{max-width:480px;margin:0 auto}
    .card{background:#fff;border-radius:16px;padding:22px;box-shadow:0 4px 12px rgba(0,0,0,0.05);margin-bottom:20px}
    .title{font-size:19px;font-weight:bold;margin-bottom:18px;display:flex;align-items:center;gap:8px}
    .label{font-size:14px;color:#555;margin-bottom:6px;font-weight:bold}
    select,button,input{width:100%;padding:15px;border-radius:12px;border:1px solid #ddd;font-size:15px;margin-bottom:12px;background:#fff}
    button{background:#007AFF;color:#fff;border:none;font-weight:bold}
    #log{background:#f9f9f9;padding:16px;border-radius:12px;min-height:260px;white-space:pre-wrap;line-height:1.5;border:1px solid #eee}
    .menu{display:flex;gap:10px}
    .menu button{flex:1;background:#f2f2f7;color:#007AFF}
</style>

<div class="box">
    <div class="card">
        <div class="title"><i class="fa fa-paper-plane"></i> 币安广场发文</div>
        <div class="label">选择发文账号</div>
        <select id="acc">
            {% for a in accounts %}
            <option value="{{a.key}}">{{a.name}}</option>
            {% endfor %}
        </select>
        <button onclick="run()"><i class="fa fa-rocket"></i> 开始发文</button>
        <button onclick="clearLog()" style="background:#ff6b6b"><i class="fa fa-trash"></i> 清空日志</button>
        <div id="log">等待启动...</div>
    </div>
    <div class="menu">
        <button onclick="location.href='/'">发文</button>
        <button onclick="location.href='/records'">记录</button>
    </div>
</div>

<script>
    let log = document.getElementById('log');
    
    // 页面加载时恢复日志
    window.onload = function() {
        let last = localStorage.getItem('last_log');
        if (last) log.textContent = last;
    };

    function print(t){ 
        log.textContent += t + '\\n';
        localStorage.setItem('last_log', log.textContent);
    }

    function clearLog(){
        log.textContent = '';
        localStorage.removeItem('last_log');
    }

    function run(){
        // 点击发文 → 清空旧日志
        log.textContent = "✅ 开始执行...\\n";
        localStorage.setItem('last_log', log.textContent);
        
        let key = document.getElementById("acc").value;
        fetch("/run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key: key })
        }).then(res=>res.json()).then(data=>{
            log.textContent = data.log;
            localStorage.setItem('last_log', data.log);
            
            if(data.record){
                let records = JSON.parse(localStorage.getItem("records") || "[]");
                records.push(data.record);
                localStorage.setItem("records", JSON.stringify(records));
            }
        });
    }
</script>
"""

# ------------------------------ 记录页面 ------------------------------
RECORD_HTML = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>发文记录</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
<style>
    *{box-sizing:border-box}
    body{background:#fff;margin:0;padding:20px;font-family:Arial;color:#222}
    .box{max-width:480px;margin:0 auto}
    .card{background:#fff;border-radius:16px;padding:22px;box-shadow:0 4px 12px rgba(0,0,0,0.05);margin-bottom:20px}
    .title{font-size:19px;font-weight:bold;margin-bottom:18px;display:flex;align-items:center;gap:8px}
    .label{font-size:14px;color:#555;margin-bottom:6px;font-weight:bold}
    select,input,button{
        width:100% !important;
        padding:15px;
        border-radius:12px;
        border:1px solid #ddd;
        font-size:15px;
        margin-bottom:10px;
        background:#fff;
        appearance:none;
    }
    button{background:#007AFF;color:#fff;border:none;font-weight:bold}
    .item{background:#f9f9f9;padding:14px;border-radius:12px;margin-bottom:10px;border:1px solid #eee}
    .item .tit{color:#007AFF;font-weight:bold}
    .item .time{font-size:12px;color:#666;margin:4px 0}
    .menu{display:flex;gap:10px}
    .menu button{flex:1;background:#f2f2f7;color:#007AFF}
</style>

<div class="box">
    <div class="card">
        <div class="title"><i class="fa fa-history"></i> 发文记录</div>
        
        <div class="label">选择账号</div>
        <select id="acc">
            {% for a in accounts %}
            <option value="{{a.name}}">{{a.name}}</option>
            {% endfor %}
        </select>

        <div class="label">选择日期</div>
        <input type="date" id="date">

        <button onclick="show()"><i class="fa fa-search"></i> 查询</button>
        <button onclick="exportAll()"><i class="fa fa-download"></i> 全量导出</button>
        <div id="list"></div>
    </div>
    <div class="menu">
        <button onclick="location.href='/'">发文</button>
        <button onclick="location.href='/records'">记录</button>
    </div>
</div>

<script>
    function getRecords(){ return JSON.parse(localStorage.getItem("records") || "[]"); }
    function show(){
        let acc = document.getElementById("acc").value;
        let date = document.getElementById("date").value;
        let list = document.getElementById("list");
        let html = "";
        getRecords().forEach(i=>{
            if(i.account === acc && i.date === date){
                html += `<div class="item">
                    <div class="tit">${i.symbol}</div>
                    <div class="time">${i.time}</div>
                    <div>${i.content}</div>
                </div>`;
            }
        });
        list.innerHTML = html || "暂无记录";
    }
    function exportAll(){
        let csv = "\\ufeff账号,日期,时间,交易对,文章ID,内容\\n";
        getRecords().forEach(i=>{
            csv += `${i.account},${i.date},${i.time},${i.symbol},${i.post_id},"${i.content.replace(/"/g,'')}"\\n`;
        });
        let blob = new Blob([csv], {type:"text/csv"});
        let a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "发文记录_"+new Date().toISOString().split("T")[0]+".csv";
        a.click();
    }
</script>
"""

# ------------------------------ 接口 ------------------------------
@app.route('/')
def index():
    return render_template_string(INDEX_HTML, accounts=get_accounts())

@app.route('/records')
def records():
    return render_template_string(RECORD_HTML, accounts=get_accounts())

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
        log += f"📢 抓取完成：\n{topic}\n\n"

        log += "✅ AI写作中...\n"
        content, _ = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            return {"log": log + "❌ AI生成失败"}

        log += "✅ 内容生成完成\n"
        ok, msg, post_id = post_content(content, key)

        # 自动记录
        account_name = next((a['name'] for a in get_accounts() if a['key'] == key), "未知")
        now = datetime.datetime.now()
        record = {
            "account": account_name,
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": "自动抓取",
            "content": content,
            "post_id": post_id
        }

        if ok:
            log += f"\n🎉 发文成功！ID：{post_id}"
        else:
            log += f"\n❌ 失败：{msg}"

        return {"log": log, "record": record}

    except Exception as e:
        return {"log": f"❌ 异常：{str(e)}"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
