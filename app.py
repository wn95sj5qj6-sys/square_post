from flask import Flask, render_template_string, request, jsonify
import threading
import time
import json
import os
import datetime
import urllib.parse

app = Flask(__name__)

# ========== 全局内存存储（全部存在服务器内存，不写文件） ==========
# 1. 币安账号列表（网页配置）
BINANCE_ACCOUNTS = []
# 2. 账号配置：key、模型、提示词、限额、间隔
ACCOUNT_CONFIG = {}
# 3. 全局状态
account_running_status = {}
DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/records.json"
CONFIG_FILE = f"{DATA_DIR}/config.json"
PROMPT_FILE = f"{DATA_DIR}/prompts.json"
os.makedirs(DATA_DIR, exist_ok=True)

# ========== 工具函数（不变） ==========
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

# ========== 账号相关 ==========
def get_all_accounts():
    accounts = []
    for acc in BINANCE_ACCOUNTS:
        name = acc["name"]
        cfg = ACCOUNT_CONFIG.get(name, {})
        accounts.append({
            "name": name,
            "prompt": cfg.get("prompt", ""),
            "daily_limit": cfg.get("daily_limit", 8),
            "auto_interval": cfg.get("auto_interval", 60),
            "model_type": cfg.get("model_type", "zhipu"),
            "api_key_set": bool(cfg.get("api_key", "")),
            "running": account_running_status.get(name, False)
        })
    return accounts

# ========== 发文记录 ==========
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
    MAX_RECORDS = 1000
    if len(db) >= MAX_RECORDS:
        db = db[-MAX_RECORDS:]
    db.append(record)
    save_json(DB_FILE, db)

def get_today_stats(account_name=None):
    today = str(datetime.date.today())
    db = load_json(DB_FILE, [])
    stats = {}
    for acc in get_all_accounts():
        stats[acc["name"]] = {"count":0,"auto_count":0,"manual_count":0,"limit":acc["daily_limit"],"remaining":acc["daily_limit"],"running":acc["running"]}
    for r in db:
        if r.get("date")==today and r.get("status")=="success" and r.get("account") in stats:
            stats[r["account"]]["count"] +=1
            if r["mode"]=="auto":
                stats[r["account"]]["auto_count"] +=1
            else:
                stats[r["account"]]["manual_count"] +=1
            stats[r["account"]]["remaining"] = stats[r["account"]]["limit"] - stats[r["account"]]["count"]
    if account_name:
        return stats.get(account_name, {"count":0,"auto_count":0,"manual_count":0,"limit":8,"remaining":8,"running":False})
    return stats

# ========== 自动发文线程 ==========
def auto_publisher_worker(account_name):
    while account_running_status.get(account_name, False):
        acc_cfg = ACCOUNT_CONFIG.get(account_name, {})
        binance_key = acc_cfg.get("binance_key","")
        model_type = acc_cfg.get("model_type","zhipu")
        model_key = acc_cfg.get("api_key","")
        daily_limit = acc_cfg.get("daily_limit",8)
        interval = acc_cfg.get("auto_interval",60)
        prompt = acc_cfg.get("prompt","")

        stats = get_today_stats(account_name)
        if stats["count"] >= daily_limit:
            account_running_status[account_name] = False
            break

        from topic_main import run_topic
        topic = run_topic()
        if not topic:
            time.sleep(10)
            continue

        from ai_core import generate_content
        content, _ = generate_content(topic, model_key, model_type, custom_prompt=prompt)
        if not content:
            time.sleep(10)
            continue

        from post_main import post_content
        ok, msg, post_id = post_content(content, binance_key)
        post_id = str(post_id) if post_id else "未知"
        if ok:
            save_post_record("auto", account_name, topic.get("symbol",""), content, post_id)
        time.sleep(interval*60)

def start_account_auto_publish(account_name):
    if account_running_status.get(account_name, False):
        return False
    account_running_status[account_name] = True
    t = threading.Thread(target=auto_publisher_worker, args=(account_name,), daemon=True)
    t.start()
    return True

def stop_account_auto_publish(account_name):
    account_running_status[account_name] = False
    return True

# ========== 前端UI（完全不变，仅扩展账号配置tab） ==========
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>币安自动发文助手</title>
    <style>
        *{margin:0;padding:0;box-sizing:border-box}
        body{font-family:Arial;background:#f5f5f5;padding:20px}
        .container{max-width:1000px;margin:auto;background:white;padding:30px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}
        h1{text-align:center;color:#333;margin-bottom:30px}
        .tabs{display:flex;gap:8px;margin-bottom:20px;border-bottom:1px solid #ddd;padding-bottom:8px}
        .tab-btn{background:none;border:none;padding:10px 20px;font-size:16px;cursor:pointer;border-radius:5px}
        .tab-btn.active{background:#007bff;color:white}
        .tab-content{display:none}
        .tab-content.active{display:block}
        .section{margin-bottom:30px;padding:20px;border:1px solid #ddd;border-radius:8px}
        .form-group{margin-bottom:15px}
        label{display:block;margin-bottom:5px;color:#666;font-weight:bold}
        input,textarea,select{width:100%;padding:10px;border:1px solid #ddd;border-radius:5px;font-size:14px}
        textarea{min-height:100px;resize:vertical}
        .btn{background:#007bff;color:white;border:none;padding:10px 20px;border-radius:5px;cursor:pointer}
        .btn-success{background:#28a745}
        .btn-danger{background:#dc3545}
        .btn-secondary{background:#6c757d}
        .result{margin-top:15px;padding:15px;background:#f8f9fa;border-radius:5px;white-space:pre-wrap}
        .badge{display:inline-block;padding:3px 8px;border-radius:10px;font-size:12px;color:white;background:#28a745}
        .badge-gray{background:#6c757d}
        .badge-red{background:#dc3545}
        .grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
    </style>
</head>
<body>
<div class="container">
    <h1>币安自动发文助手 <span class="badge">v2.2</span></h1>
    <div class="tabs">
        <button class="tab-btn active" onclick="switchTab('auto')">自动模式</button>
        <button class="tab-btn" onclick="switchTab('manual')">手动模式</button>
        <button class="tab-btn" onclick="switchTab('config')">账号配置</button>
        <button class="tab-btn" onclick="switchTab('records')">发文记录</button>
    </div>

    <!-- 自动模式（不变） -->
    <div id="auto" class="tab-content active">
        <div class="form-group">
            <label>选择账号</label>
            <select id="auto_acc" class="form-control">
                {% for acc in accounts %}
                <option value="{{acc.name}}">{{acc.name}}</option>
                {% endfor %}
            </select>
        </div>
        <div class="form-group">
            <label>今日统计</label>
            <div id="auto_stats" class="result"></div>
        </div>
        <button class="btn btn-success" onclick="startAuto()">启动自动发文</button>
        <button class="btn btn-danger" onclick="stopAuto()">停止自动发文</button>
    </div>

    <!-- 手动模式（不变） -->
    <div id="manual" class="tab-content">
        <div class="form-group">
            <label>选择账号</label>
            <select id="manual_acc" class="form-control">
                {% for acc in accounts %}
                <option value="{{acc.name}}">{{acc.name}}</option>
                {% endfor %}
            </select>
        </div>
        <div class="form-group">
            <label>交易对</label>
            <input type="text" id="manual_symbol" placeholder="BTCUSDT">
        </div>
        <button class="btn btn-secondary" onclick="genTopic()">生成分析</button>
        <div class="form-group">
            <label>分析内容</label>
            <textarea id="manual_topic"></textarea>
        </div>
        <button class="btn btn-secondary" onclick="genContent()">生成发文</button>
        <div class="form-group">
            <label>发文内容</label>
            <textarea id="manual_content"></textarea>
        </div>
        <button class="btn btn-success" onclick="publish()">立即发文</button>
        <div id="manual_log" class="result"></div>
    </div>

    <!-- 账号配置【扩展：网页配置币安账号、模型、key、隐藏星号】 -->
    <div id="config" class="tab-content">
        <!-- 1. 币安账号管理 -->
        <div class="form-group">
            <label>添加币安账号</label>
            <div class="grid">
                <input type="text" id="acc_name" placeholder="账号名称">
                <input type="text" id="binance_key" placeholder="币安API Key">
            </div>
            <button class="btn btn-secondary mt-2" onclick="addBinanceAcc()">添加账号</button>
        </div>
        <div class="form-group">
            <label>已配置账号</label>
            <select id="config_acc" class="form-control" onchange="loadAccConfig()">
                {% for acc in accounts %}
                <option value="{{acc.name}}">{{acc.name}}</option>
                {% endfor %}
            </select>
        </div>
        <!-- 2. 模型配置（关键：隐藏星号、模型选择） -->
        <div class="form-group">
            <label>模型选择</label>
            <select id="model_type" class="form-control">
                <option value="zhipu">智谱GLM-4</option>
                <option value="deepseek">DeepSeek-v4-flash</option>
            </select>
        </div>
        <div class="form-group">
            <label>模型API Key（显示为星号）</label>
            <input type="password" id="model_key" placeholder="输入模型Key，保存后隐藏">
        </div>
        <!-- 3. 原有配置不变 -->
        <div class="form-group">
            <label>专属提示词</label>
            <textarea id="prompt"></textarea>
        </div>
        <div class="grid">
            <div class="form-group">
                <label>每日限额</label>
                <input type="number" id="daily_limit" min="1" value="8">
            </div>
            <div class="form-group">
                <label>自动间隔（分钟）</label>
                <input type="number" id="auto_interval" min="5" value="60">
            </div>
        </div>
        <button class="btn btn-success" onclick="saveAccConfig()">保存配置</button>
        <button class="btn btn-danger" onclick="delBinanceAcc()">删除账号</button>
        <div id="config_log" class="result"></div>
    </div>

    <!-- 发文记录（不变） -->
    <div id="records" class="tab-content">
        <div class="form-group">
            <div class="grid">
                <select id="rec_acc" class="form-control">
                    <option value="">所有账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                <input type="date" id="rec_date">
            </div>
            <button class="btn btn-secondary mt-2" onclick="loadRecords()">查询</button>
            <button class="btn btn-secondary mt-2" onclick="exportRecords()">导出</button>
        </div>
        <div id="records_list" class="result"></div>
    </div>
</div>

<script>
// Tab切换（不变）
function switchTab(t){
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
    document.querySelector(`.tab-btn[onclick="switchTab('${t}')"]`).classList.add('active');
    document.getElementById(t).classList.add('active');
    if(t==='auto') refreshAutoStats();
}

// 自动模式
function refreshAutoStats(){
    fetch('/api/today_stats').then(r=>r.json()).then(d=>{
        let html='';
        for(let k in d){
            let s=d[k];
            let status=s.running?'<span class="badge">运行中</span>':'<span class="badge-gray">已停止</span>';
            html+=`${k}: 今日${s.count}/${s.limit} 剩余${s.remaining} ${status}\n`;
        }
        document.getElementById('auto_stats').innerText=html;
    });
}
function startAuto(){
    let acc=document.getElementById('auto_acc').value;
    fetch('/api/auto/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acc})}).then(r=>r.json()).then(d=>{
        alert(d.msg);refreshAutoStats();
    });
}
function stopAuto(){
    let acc=document.getElementById('auto_acc').value;
    fetch('/api/auto/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acc})}).then(r=>r.json()).then(d=>{
        alert(d.msg);refreshAutoStats();
    });
}

// 手动模式
function genTopic(){
    let s=document.getElementById('manual_symbol').value.trim().toUpperCase();
    fetch('/api/topic?symbol='+s).then(r=>r.json()).then(d=>{
        document.getElementById('manual_topic').value=d.text;
    });
}
function genContent(){
    let acc=document.getElementById('manual_acc').value;
    let t=document.getElementById('manual_topic').value;
    fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acc,topic:t})}).then(r=>r.text()).then(d=>{
        document.getElementById('manual_content').value=d;
    });
}
function publish(){
    let acc=document.getElementById('manual_acc').value;
    let c=document.getElementById('manual_content').value;
    fetch('/api/publish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({acc,content:c})}).then(r=>r.json()).then(d=>{
        document.getElementById('manual_log').innerText=JSON.stringify(d,null,2);
    });
}

// 账号配置（核心：网页配置、星号隐藏）
function addBinanceAcc(){
    let n=document.getElementById('acc_name').value.trim();
    let k=document.getElementById('binance_key').value.trim();
    if(!n||!k)return alert('名称和key不能为空');
    fetch('/api/add_acc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n,key:k})}).then(r=>r.json()).then(d=>{
        alert(d.msg);location.reload();
    });
}
function delBinanceAcc(){
    let n=document.getElementById('config_acc').value;
    if(!confirm('确定删除？'))return;
    fetch('/api/del_acc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})}).then(r=>r.json()).then(d=>{
        alert(d.msg);location.reload();
    });
}
function loadAccConfig(){
    let n=document.getElementById('config_acc').value;
    fetch('/api/get_acc_cfg?name='+n).then(r=>r.json()).then(d=>{
        document.getElementById('model_type').value=d.model_type||'zhipu';
        document.getElementById('model_key').value=d.api_key?'********':'';
        document.getElementById('prompt').value=d.prompt||'';
        document.getElementById('daily_limit').value=d.daily_limit||8;
        document.getElementById('auto_interval').value=d.auto_interval||60;
    });
}
function saveAccConfig(){
    let n=document.getElementById('config_acc').value;
    let mt=document.getElementById('model_type').value;
    let mk=document.getElementById('model_key').value;
    let p=document.getElementById('prompt').value;
    let dl=parseInt(document.getElementById('daily_limit').value);
    let ai=parseInt(document.getElementById('auto_interval').value);
    // 如果是星号，不覆盖原有key
    let apiKey=mk==='********'?'':mk;
    fetch('/api/save_acc_cfg',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        name,model_type:mt,api_key:apiKey,prompt:p,daily_limit:dl,auto_interval:ai
    })}).then(r=>r.json()).then(d=>{
        document.getElementById('config_log').innerText=d.msg;
    });
}

// 记录
function loadRecords(){
    let a=document.getElementById('rec_acc').value;
    let d=document.getElementById('rec_date').value;
    fetch(`/api/records?acc=${a}&date=${d}`).then(r=>r.json()).then(list=>{
        let html='';
        list.forEach(r=>{
            html+=`[${r.time}] ${r.account}(${r.mode})｜${r.symbol}\n${r.content}\n---\n`;
        });
        document.getElementById('records_list').innerText=html||'暂无记录';
    });
}
function exportRecords(){
    let a=document.getElementById('rec_acc').value;
    let d=document.getElementById('rec_date').value;
    window.open(`/api/export?acc=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`);
}

window.onload=()=>{
    refreshAutoStats();
    document.getElementById('rec_date').value=new Date().toISOString().split('T')[0];
};
</script>
</body>
</html>
"""

# ========== 路由 ==========
@app.route('/')
def index():
    return render_template_string(UI_TEMPLATE, accounts=get_all_accounts())

# 账号管理
@app.route('/api/add_acc', methods=['POST'])
def add_acc():
    d = request.json
    name = d['name']
    key = d['key']
    for a in BINANCE_ACCOUNTS:
        if a['name'] == name:
            return jsonify({'msg':'账号已存在'})
    BINANCE_ACCOUNTS.append({'name':name,'key':key})
    return jsonify({'msg':'添加成功'})

@app.route('/api/del_acc', methods=['POST'])
def del_acc():
    name = request.json['name']
    global BINANCE_ACCOUNTS
    BINANCE_ACCOUNTS = [a for a in BINANCE_ACCOUNTS if a['name']!=name]
    if name in ACCOUNT_CONFIG:
        del ACCOUNT_CONFIG[name]
    return jsonify({'msg':'删除成功'})

@app.route('/api/get_acc_cfg')
def get_acc_cfg():
    name = request.args['name']
    cfg = ACCOUNT_CONFIG.get(name, {})
    return jsonify({
        'model_type': cfg.get('model_type','zhipu'),
        'api_key': cfg.get('api_key',''),
        'prompt': cfg.get('prompt',''),
        'daily_limit': cfg.get('daily_limit',8),
        'auto_interval': cfg.get('auto_interval',60)
    })

@app.route('/api/save_acc_cfg', methods=['POST'])
def save_acc_cfg():
    d = request.json
    name = d['name']
    if name not in ACCOUNT_CONFIG:
        ACCOUNT_CONFIG[name] = {}
    # 只更新非空key（星号不覆盖）
    if d['api_key']:
        ACCOUNT_CONFIG[name]['api_key'] = d['api_key']
    ACCOUNT_CONFIG[name]['model_type'] = d['model_type']
    ACCOUNT_CONFIG[name]['prompt'] = d['prompt']
    ACCOUNT_CONFIG[name]['daily_limit'] = d['daily_limit']
    ACCOUNT_CONFIG[name]['auto_interval'] = d['auto_interval']
    return jsonify({'msg':'配置已保存'})

# 自动启停
@app.route('/api/auto/start', methods=['POST'])
def auto_start():
    acc = request.json['acc']
    ok = start_account_auto_publish(acc)
    return jsonify({'msg':'启动成功' if ok else '已在运行'})

@app.route('/api/auto/stop', methods=['POST'])
def auto_stop():
    acc = request.json['acc']
    stop_account_auto_publish(acc)
    return jsonify({'msg':'已停止'})

# 统计
@app.route('/api/today_stats')
def today_stats():
    return jsonify(get_today_stats())

# 手动生成
@app.route('/api/topic')
def topic():
    symbol = request.args.get('symbol','')
    from topic_main import get_single_symbol_topic
    t = get_single_symbol_topic(symbol)
    return jsonify(t)

@app.route('/api/generate', methods=['POST'])
def generate():
    d = request.json
    acc = d['acc']
    topic_text = d['topic']
    acc_cfg = ACCOUNT_CONFIG.get(acc, {})
    model_type = acc_cfg.get('model_type','zhipu')
    model_key = acc_cfg.get('api_key','')
    prompt = acc_cfg.get('prompt','')
    topic = {'text':topic_text}
    from ai_core import generate_content
    content, _ = generate_content(topic, model_key, model_type, custom_prompt=prompt)
    return content

@app.route('/api/publish', methods=['POST'])
def publish():
    d = request.json
    acc = d['acc']
    content = d['content']
    acc_cfg = ACCOUNT_CONFIG.get(acc, {})
    binance_key = acc_cfg.get('binance_key','')
    from post_main import post_content
    ok, msg, post_id = post_content(content, binance_key)
    post_id = str(post_id) if post_id else "未知"
    if ok:
        save_post_record("manual", acc, "手动", content, post_id)
    return jsonify({'success':ok,'msg':msg,'post_id':post_id})

# 记录
@app.route('/api/records')
def records():
    acc = request.args.get('acc','')
    date = request.args.get('date','')
    db = load_json(DB_FILE, [])
    res = []
    for r in db:
        if acc and r.get('account')!=acc:
            continue
        if date and r.get('date')!=date:
            continue
        res.append(r)
    return jsonify(res)

@app.route('/api/export')
def export():
    acc = request.args.get('acc','')
    date = request.args.get('date','')
    db = load_json(DB_FILE, [])
    csv = "模式,账号,日期,时间,交易对,内容\n"
    for r in db:
        if acc and r.get('account')!=acc:
            continue
        if date and r.get('date')!=date:
            continue
        content = r.get('content','').replace('"','""')
        csv += f"{r.get('mode','')},{r.get('account','')},{r.get('date','')},{r.get('time','')},{r.get('symbol','')},\"{content}\"\n"
    filename = f"发文记录_{datetime.date.today()}.csv"
    encoded = urllib.parse.quote(filename)
    resp = app.response_class(csv, mimetype='text/csv')
    resp.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{encoded}'
    return resp

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
