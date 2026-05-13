from flask import Flask, render_template_string, request, jsonify, Response
import threading
import time
import json
import os
import datetime
import csv
from io import StringIO

app = Flask(__name__)

BINANCE_ACCOUNTS = []
GLOBAL_MODEL_KEYS = {"zhipu": "", "deepseek": ""}
ACCOUNT_CONFIG = {}
AUTO_TASKS = {}
DATA_DIR = "data"
RECORDS_FILE = os.path.join(DATA_DIR, "records.json")
os.makedirs(DATA_DIR, exist_ok=True)

def load_json(path, default=None):
    if default is None:
        default = []
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_today_date():
    return datetime.datetime.now().strftime("%Y-%m-%d")

def calculate_remaining(used, limit):
    return max(0, limit - used)

def load_records():
    return load_json(RECORDS_FILE, [])

def save_record(record):
    records = load_records()
    records.append(record)
    save_json(RECORDS_FILE, records)

def get_today_stats():
    today = get_today_date()
    records = load_records()
    stats = {}
    for acc in BINANCE_ACCOUNTS:
        name = acc["name"]
        cfg = ACCOUNT_CONFIG.get(name, {})
        limit = cfg.get("daily_limit", 8)
        used = sum(1 for r in records if r["date"] == today and r["account"] == name and r["status"] == "success")
        auto_used = sum(1 for r in records if r["date"] == today and r["account"] == name and r["status"] == "success" and r["mode"] == "auto")
        manual_used = sum(1 for r in records if r["date"] == today and r["account"] == name and r["status"] == "success" and r["mode"] == "manual")
        stats[name] = {
            "used": used,
            "auto_used": auto_used,
            "manual_used": manual_used,
            "limit": limit,
            "remaining": calculate_remaining(used, limit),
            "running": AUTO_TASKS.get(name, False)
        }
    return stats

def auto_publish_task(account_name):
    try:
        while True:
            if not AUTO_TASKS.get(account_name, False):
                break
            acc_cfg = ACCOUNT_CONFIG.get(account_name, {})
            binance_key = next((a["key"] for a in BINANCE_ACCOUNTS if a["name"] == account_name), None)
            model_type = acc_cfg.get("model_type", "zhipu")
            model_key = GLOBAL_MODEL_KEYS.get(model_type, "")
            daily_limit = acc_cfg.get("daily_limit", 8)
            interval = acc_cfg.get("auto_interval", 60) * 60
            custom_prompt = acc_cfg.get("prompt", "")

            stats = get_today_stats().get(account_name, {})
            if stats.get("used", 0) >= daily_limit:
                AUTO_TASKS[account_name] = False
                break

            from topic_main import get_random_topic
            topic = get_random_topic()
            if not topic:
                time.sleep(10)
                continue

            from ai_core import generate_post_content
            content = generate_post_content(topic["text"], model_type, model_key, custom_prompt)
            if "错误" in content:
                time.sleep(10)
                continue

            from post_main import post_to_binance
            success, msg, post_id = post_to_binance(content, binance_key)
            record = {
                "date": get_today_date(),
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "account": account_name,
                "symbol": topic["symbol"],
                "content": content,
                "post_id": post_id,
                "mode": "auto",
                "status": "success" if success else "fail",
                "msg": msg
            }
            save_record(record)
            time.sleep(interval)
    except Exception as e:
        print(f"自动线程异常：{e}")
        AUTO_TASKS[account_name] = False

def start_auto_task(account_name):
    if AUTO_TASKS.get(account_name, False):
        return False, "任务已在运行"
    AUTO_TASKS[account_name] = True
    thread = threading.Thread(target=auto_publish_task, args=(account_name,), daemon=True)
    thread.start()
    return True, "已启动"

def stop_auto_task(account_name):
    AUTO_TASKS[account_name] = False
    return True, "已停止"

UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安自动发文助手</title>
    <style>
        * {margin:0;padding:0;box-sizing:border-box}
        body {background:#f5f5f5;padding:20px;font-family:Arial}
        .container {max-width:1000px;margin:auto;background:white;border-radius:12px;padding:30px;box-shadow:0 2px 12px rgba(0,0,0,0.08)}
        h1 {text-align:center;color:#333;margin-bottom:30px;font-size:32px;position:relative}
        .version-badge {position:absolute;top:0;right:20px;background:#28a745;color:white;padding:4px 10px;border-radius:12px;font-size:14px}
        .tabs {display:flex;border-bottom:1px solid #eee;margin-bottom:30px}
        .tab-btn {padding:12px 24px;border:none;background:none;font-size:18px;color:#666;cursor:pointer;margin-right:8px;border-bottom:3px solid transparent}
        .tab-btn.active {color:#007bff;border-bottom-color:#007bff}
        .tab-content {display:none}
        .tab-content.active {display:block}
        .form-group {margin-bottom:20px}
        label {display:block;margin-bottom:8px;color:#333;font-weight:500}
        select,input,textarea {width:100%;padding:12px;border:1px solid #ddd;border-radius:8px;font-size:16px;background:#f8f9fa}
        textarea {resize:vertical;min-height:120px}
        .btn-group {display:flex;gap:12px;margin-top:12px}
        .btn {padding:12px 24px;border:none;border-radius:8px;font-size:16px;cursor:pointer}
        .btn-primary {background:#007bff;color:white}
        .btn-success {background:#28a745;color:white}
        .btn-danger {background:#dc3545;color:white}
        .btn-secondary {background:#6c757d;color:white}
        .btn:hover {opacity:0.9}
        .stats-grid {display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:20px 0}
        .stat-card {background:#f8f9fa;padding:20px;border-radius:8px;text-align:center}
        .stat-number {font-size:36px;font-weight:bold;color:#333;margin-bottom:8px}
        .stat-label {color:#666;font-size:14px}
        .status-badge {display:inline-block;padding:4px 8px;border-radius:12px;font-size:12px;color:white;margin-left:8px}
        .status-running {background:#28a745}
        .status-stopped {background:#6c757d}
        .record-item {background:#f8f9fa;padding:16px;border-radius:8px;margin-bottom:12px;display:flex;justify-content:space-between;align-items:center}
        .record-content {color:#333;white-space:pre-wrap;flex:1}
        .record-btn {margin-left:10px}
        .empty-state {text-align:center;color:#666;padding:40px 0}
    </style>
</head>
<body>
    <div class="container">
        <h1>币安自动发文助手 <span class="version-badge">v2.4</span></h1>
        <div class="tabs">
            <button class="tab-btn" onclick="switchTab('auto')">自动模式</button>
            <button class="tab-btn" onclick="switchTab('manual')">手动模式</button>
            <button class="tab-btn active" onclick="switchTab('config')">账号配置</button>
            <button class="tab-btn" onclick="switchTab('records')">发文记录</button>
        </div>

        <div id="auto" class="tab-content">
            <div class="form-group">
                <label>选择账号</label>
                <select id="auto_account">
                    {% for acc in accounts %}
                    <option value="{{ acc.name }}">{{ acc.name }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>今日发文统计</label>
                <div id="auto_stats" class="stats-grid"></div>
            </div>
            <div class="btn-group">
                <button class="btn btn-success" onclick="startAuto()">启动自动发文</button>
                <button class="btn btn-danger" onclick="stopAuto()">停止自动发文</button>
            </div>
        </div>

        <div id="manual" class="tab-content">
            <div class="form-group">
                <label>选择发文账号</label>
                <select id="manual_account">
                    {% for acc in accounts %}
                    <option value="{{ acc.name }}">{{ acc.name }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>交易对</label>
                <input type="text" id="manual_symbol" placeholder="如 BTCUSDT">
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="autoSelectSymbol()">自动选交易对</button>
                    <button class="btn btn-secondary" onclick="generateAnalysis()">生成完整分析</button>
                </div>
            </div>
            <div class="form-group">
                <label>话题分析</label>
                <textarea id="manual_analysis"></textarea>
            </div>
            <div class="form-group">
                <button class="btn btn-secondary" onclick="generatePostContent()">生成发文内容</button>
            </div>
            <div class="form-group">
                <label>最终内容</label>
                <textarea id="manual_content"></textarea>
            </div>
            <button class="btn btn-success" onclick="publishPost()">确认发文</button>
            <div id="manual_log" class="form-group" style="white-space: pre-wrap; background: #f8f9fa; padding: 10px; border-radius: 6px;"></div>
        </div>

        <div id="config" class="tab-content active">
            <div class="form-group">
                <label>全局DeepSeek API Key</label>
                <input type="password" id="global_deepseek_key">
            </div>
            <div class="form-group">
                <label>全局智谱GLM-4 API Key</label>
                <input type="password" id="global_zhipu_key">
            </div>
            <button class="btn btn-primary" onclick="saveGlobalKeys()">保存全局模型Key</button>
            <div id="global_key_log" class="form-group"></div>
            <hr style="margin:30px 0">
            <div class="form-group">
                <label>添加币安广场账号</label>
                <div style="display:flex;gap:12px;">
                    <input type="text" id="new_acc_name" placeholder="账号名称">
                    <input type="text" id="new_acc_key" placeholder="币安API Key">
                </div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="addBinanceAccount()">添加账号</button>
                    <button class="btn btn-danger" onclick="deleteBinanceAccount()">删除选中账号</button>
                </div>
            </div>
            <div class="form-group">
                <label>选择要配置的账号</label>
                <select id="config_account" onchange="loadAccountConfig()">
                    {% for acc in accounts %}
                    <option value="{{ acc.name }}">{{ acc.name }}</option>
                    {% endfor %}
                </select>
            </div>
            <div class="form-group">
                <label>模型类型</label>
                <select id="config_model">
                    <option value="zhipu">智谱GLM-4</option>
                    <option value="deepseek">DeepSeek</option>
                </select>
            </div>
            <div class="form-group">
                <label>专属提示词</label>
                <textarea id="config_prompt"></textarea>
            </div>
            <div class="form-group">
                <label>每日发文限额</label>
                <input type="number" id="config_daily_limit" value="8">
            </div>
            <div class="form-group">
                <label>自动发文间隔（分钟）</label>
                <input type="number" id="config_interval" value="60">
            </div>
            <button class="btn btn-primary" onclick="saveAccountConfig()">保存账号配置</button>
            <div id="config_log" class="form-group"></div>
        </div>

        <div id="records" class="tab-content">
            <div class="form-group">
                <div style="display:flex;gap:12px;">
                    <select id="record_account" style="flex:1">
                        <option value="">所有账号</option>
                        {% for acc in accounts %}
                        <option value="{{ acc.name }}">{{ acc.name }}</option>
                        {% endfor %}
                    </select>
                    <input type="date" id="record_date" style="flex:1">
                </div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="queryRecords()">查询</button>
                    <button class="btn btn-secondary" onclick="exportRecords()">导出</button>
                    <button class="btn btn-danger" onclick="deleteAllRecords()">清空所有记录</button>
                </div>
            </div>
            <div id="records_list"></div>
        </div>
    </div>

    <script>
        function switchTab(tab) {
            document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick="switchTab('${tab}')"]`).classList.add('active');
            document.getElementById(tab).classList.add('active');
            if(tab==='auto')refreshAutoStats();
            if(tab==='records')queryRecords();
        }

        function refreshAutoStats() {
            fetch('/api/stats').then(r=>r.json()).then(s=>{
                let html='';
                for(let k in s){
                    html+=`
                    <div class="stat-card">
                        <div class="stat-number">${s[k].used}</div>
                        <div class="stat-label">${k}</div>
                        <div class="stat-label">自动:${s[k].auto_used} 手动:${s[k].manual_used}</div>
                        <div class="stat-label">剩余:${s[k].remaining}/${s[k].limit}</div>
                        <span class="status-badge ${s[k].running?'status-running':'status-stopped'}">
                            ${s[k].running?'运行中':'已停止'}
                        </span>
                    </div>`;
                }
                document.getElementById('auto_stats').innerHTML=html;
            });
        }

        function startAuto() {
            fetch('/api/auto/start',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({account:document.getElementById('auto_account').value})
            }).then(r=>r.json()).then(d=>{alert(d.msg);refreshAutoStats();});
        }

        function stopAuto() {
            fetch('/api/auto/stop',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({account:document.getElementById('auto_account').value})
            }).then(r=>r.json()).then(d=>{alert(d.msg);refreshAutoStats();});
        }

        function autoSelectSymbol() {
            document.getElementById('manual_log').innerText = "⏳ 正在自动选择交易对...";
            fetch('/api/topic/random').then(r=>r.json()).then(t=>{
                document.getElementById('manual_symbol').value = t.symbol;
                document.getElementById('manual_analysis').value = t.text;
                document.getElementById('manual_log').innerText = "✅ 已自动选择交易对并生成分析";
            });
        }

        function generateAnalysis() {
            const s = document.getElementById('manual_symbol').value.trim().toUpperCase();
            if(!s)return alert('请输入交易对');
            document.getElementById('manual_log').innerText = "⏳ 正在生成完整行情分析...";
            fetch(`/api/topic?symbol=${s}`).then(r=>r.json()).then(t=>{
                document.getElementById('manual_analysis').value = t.text;
                document.getElementById('manual_log').innerText = "✅ 行情分析生成完成";
            });
        }

        function generatePostContent() {
            const a = document.getElementById('manual_account').value;
            const t = document.getElementById('manual_analysis').value;
            document.getElementById('manual_log').innerText = "⏳ AI 正在生成发文内容...";
            fetch('/api/generate',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({account:a,analysis:t})
            }).then(r=>r.text()).then(c=>{
                document.getElementById('manual_content').value = c;
                document.getElementById('manual_log').innerText = "✅ 发文内容生成完成";
            });
        }

        function publishPost() {
            const a = document.getElementById('manual_account').value;
            const c = document.getElementById('manual_content').value;
            document.getElementById('manual_log').innerText = "⏳ 正在提交发文到币安...";
            fetch('/api/publish',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({account:a,content:c})
            }).then(r=>r.json()).then(d=>{
                document.getElementById('manual_log').innerText = JSON.stringify(d, null, 2);
            });
        }

        function saveGlobalKeys() {
            fetch('/api/global_keys/save',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({
                    deepseek:document.getElementById('global_deepseek_key').value,
                    zhipu:document.getElementById('global_zhipu_key').value
                })
            }).then(r=>r.json()).then(d=>{
                alert('全局Key保存成功');
                if(document.getElementById('global_deepseek_key').value)
                    document.getElementById('global_deepseek_key').value='********';
                if(document.getElementById('global_zhipu_key').value)
                    document.getElementById('global_zhipu_key').value='********';
            });
        }

        function addBinanceAccount() {
            const n=document.getElementById('new_acc_name').value.trim();
            const k=document.getElementById('new_acc_key').value.trim();
            if(!n||!k)return alert('名称和Key不能为空');
            fetch('/api/binance/add',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:n,key:k})
            }).then(r=>r.json()).then(d=>{alert(d.msg);location.reload();});
        }

        function deleteBinanceAccount() {
            const n=document.getElementById('config_account').value;
            if(!confirm('确定删除？'))return;
            fetch('/api/binance/delete',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({name:n})
            }).then(r=>r.json()).then(d=>{alert(d.msg);location.reload();});
        }

        function loadAccountConfig() {
            const a=document.getElementById('config_account').value;
            fetch(`/api/config?account=${a}`).then(r=>r.json()).then(c=>{
                document.getElementById('config_model').value=c.model_type||'zhipu';
                document.getElementById('config_prompt').value=c.prompt||'';
                document.getElementById('config_daily_limit').value=c.daily_limit||8;
                document.getElementById('config_interval').value=c.auto_interval||60;
            });
        }

        function saveAccountConfig() {
            fetch('/api/config/save',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({
                    account:document.getElementById('config_account').value,
                    model_type:document.getElementById('config_model').value,
                    prompt:document.getElementById('config_prompt').value,
                    daily_limit:parseInt(document.getElementById('config_daily_limit').value),
                    auto_interval:parseInt(document.getElementById('config_interval').value)
                })
            }).then(r=>r.json()).then(d=>{alert('保存成功');});
        }

        function queryRecords() {
            const a=document.getElementById('record_account').value;
            const d=document.getElementById('record_date').value;
            fetch(`/api/records?account=${a}&date=${d}`).then(r=>r.json()).then(list=>{
                let html='';
                if(list.length===0){
                    html='<div class="empty-state">暂无记录</div>';
                }else{
                    list.forEach((item,idx)=>{
                        html+=`
                        <div class="record-item">
                            <div class="record-content">
                                ${item.time} | ${item.account} | ${item.mode} | ${item.symbol}\n${item.content}
                            </div>
                            <button class="btn btn-danger record-btn" onclick="deleteOneRecord(${idx})">删除</button>
                        </div>`;
                    });
                }
                document.getElementById('records_list').innerHTML=html;
            });
        }

        function deleteOneRecord(idx) {
            fetch('/api/records/delete_one',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({idx})
            }).then(r=>r.json()).then(d=>{alert(d.msg);queryRecords();});
        }

        function deleteAllRecords() {
            if(!confirm('确定清空所有记录？'))return;
            fetch('/api/records/delete_all',{method:'POST'}).then(r=>r.json()).then(d=>{alert(d.msg);queryRecords();});
        }

        function exportRecords() {
            const a=document.getElementById('record_account').value;
            const d=document.getElementById('record_date').value;
            window.open(`/api/export?account=${a}&date=${d}`);
        }

        window.onload=()=>{
            refreshAutoStats();
            loadAccountConfig();
            fetch('/api/global_keys').then(r=>r.json()).then(k=>{
                if(k.deepseek)document.getElementById('global_deepseek_key').value='********';
                if(k.zhipu)document.getElementById('global_zhipu_key').value='********';
            });
        };
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    if not BINANCE_ACCOUNTS:
        try:
            BINANCE_ACCOUNTS.extend(json.loads(os.getenv("BINANCE_ACCOUNTS", "[]")))
        except:
            pass
    return render_template_string(UI_TEMPLATE, accounts=BINANCE_ACCOUNTS)

@app.route('/api/global_keys')
def api_global_keys():
    return jsonify({
        "deepseek": bool(GLOBAL_MODEL_KEYS["deepseek"]),
        "zhipu": bool(GLOBAL_MODEL_KEYS["zhipu"])
    })

@app.route('/api/global_keys/save', methods=['POST'])
def api_global_save():
    d = request.json
    if d.get('deepseek'):
        GLOBAL_MODEL_KEYS["deepseek"] = d['deepseek']
    if d.get('zhipu'):
        GLOBAL_MODEL_KEYS["zhipu"] = d['zhipu']
    return jsonify({"msg":"ok"})

@app.route('/api/binance/add', methods=['POST'])
def api_binance_add():
    d = request.json
    n = d['name']
    k = d['key']
    for a in BINANCE_ACCOUNTS:
        if a['name'] == n:
            return jsonify({"msg":"已存在"})
    BINANCE_ACCOUNTS.append({"name":n,"key":k})
    return jsonify({"msg":"添加成功"})

@app.route('/api/binance/delete', methods=['POST'])
def api_binance_del():
    n = request.json['name']
    global BINANCE_ACCOUNTS
    BINANCE_ACCOUNTS = [a for a in BINANCE_ACCOUNTS if a['name'] != n]
    if n in ACCOUNT_CONFIG:
        del ACCOUNT_CONFIG[n]
    return jsonify({"msg":"删除成功"})

@app.route('/api/stats')
def api_stats():
    return jsonify(get_today_stats())

@app.route('/api/auto/start', methods=['POST'])
def api_auto_start():
    a = request.json['account']
    ok, msg = start_auto_task(a)
    return jsonify({"msg":msg})

@app.route('/api/auto/stop', methods=['POST'])
def api_auto_stop():
    a = request.json['account']
    ok, msg = stop_auto_task(a)
    return jsonify({"msg":msg})

@app.route('/api/topic/random')
def api_topic_random():
    from topic_main import get_random_topic
    return jsonify(get_random_topic() or {"symbol":"BTCUSDT","text":"获取失败"})

@app.route('/api/topic')
def api_topic():
    s = request.args.get('symbol')
    from topic_main import get_single_symbol_topic
    return jsonify(get_single_symbol_topic(s))

@app.route('/api/generate', methods=['POST'])
def api_generate():
    d = request.json
    a = d['account']
    t = d['analysis']
    cfg = ACCOUNT_CONFIG.get(a, {})
    m = cfg.get('model_type', 'zhipu')
    key = GLOBAL_MODEL_KEYS.get(m, '')
    prompt = cfg.get('prompt', '')
    from ai_core import generate_post_content
    return generate_post_content(t, m, key, prompt)

@app.route('/api/publish', methods=['POST'])
def api_publish():
    d = request.json
    a = d['account']
    c = d['content']
    binance_key = next((x['key'] for x in BINANCE_ACCOUNTS if x['name']==a), None)
    from post_main import post_to_binance
    
    # 获取币安完整返回值
    binance_response = post_to_binance(c, binance_key)
    
    # 记录日志
    record = {
        "date": get_today_date(),
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account": a,
        "symbol": "手动",
        "content": c,
        "post_id": binance_response.get("data", {}).get("postId"),
        "mode": "manual",
        "status": "success" if binance_response.get("success") else "fail",
        "msg": binance_response.get("message") or binance_response.get("msg")
    }
    save_record(record)
    
    # 直接返回币安原生数据（包含链接）
    return jsonify(binance_response)

@app.route('/api/config')
def api_config():
    a = request.args.get('account')
    return jsonify(ACCOUNT_CONFIG.get(a, {}))

@app.route('/api/config/save', methods=['POST'])
def api_config_save():
    d = request.json
    a = d['account']
    ACCOUNT_CONFIG[a] = {
        "model_type":d['model_type'],
        "prompt":d['prompt'],
        "daily_limit":d['daily_limit'],
        "auto_interval":d['auto_interval']
    }
    return jsonify({"msg":"ok"})

@app.route('/api/records')
def api_records():
    a = request.args.get('account','')
    d = request.args.get('date','')
    r = load_records()
    f = []
    for x in r:
        if a and x['account']!=a:continue
        if d and x['date']!=d:continue
        f.append(x)
    return jsonify(f)

@app.route('/api/records/delete_one', methods=['POST'])
def api_del_one():
    idx = request.json['idx']
    r = load_records()
    if 0<=idx<len(r):
        r.pop(idx)
        save_json(RECORDS_FILE, r)
    return jsonify({"msg":"删除成功"})

@app.route('/api/records/delete_all', methods=['POST'])
def api_del_all():
    save_json(RECORDS_FILE, [])
    return jsonify({"msg":"已清空"})

@app.route('/api/export')
def api_export():
    a = request.args.get('account','')
    d = request.args.get('date','')
    r = load_records()
    o = StringIO()
    w = csv.writer(o)
    w.writerow(["日期","时间","账号","模式","交易对","内容","状态"])
    for x in r:
        if a and x['account']!=a:continue
        if d and x['date']!=d:continue
        w.writerow([x['date'],x['time'],x['account'],x['mode'],x['symbol'],x['content'],x['status']])
    o.seek(0)
    return Response(o.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=records.csv"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
