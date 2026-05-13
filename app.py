from flask import Flask, render_template_string, request, jsonify, Response
import threading
import time
import json
import os
import datetime
import csv
from io import StringIO

app = Flask(__name__)

# ========== 全局配置 ==========
BINANCE_ACCOUNTS = []       # 币安账号列表（网页配置）
GLOBAL_MODEL_KEYS = {       # 全局模型Key，所有账号共享
    "zhipu": "",
    "deepseek": ""
}
ACCOUNT_CONFIG = {}         # 每个账号的模型选择、提示词、限额、间隔
AUTO_TASKS = {}
DATA_DIR = "data"
RECORDS_FILE = os.path.join(DATA_DIR, "records.json")
os.makedirs(DATA_DIR, exist_ok=True)

# ========== 工具函数 ==========
def load_json(file_path, default=None):
    if default is None:
        default = []
    if not os.path.exists(file_path):
        return default
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"加载文件失败：{e}")
        return default

def save_json(file_path, data):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
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

# ========== 自动发文线程 ==========
def auto_publish_task(account_name):
    acc_cfg = ACCOUNT_CONFIG.get(account_name, {})
    binance_key = next((a["key"] for a in BINANCE_ACCOUNTS if a["name"] == account_name), None)
    model_type = acc_cfg.get("model_type", "zhipu")
    model_key = GLOBAL_MODEL_KEYS.get(model_type, "")
    daily_limit = acc_cfg.get("daily_limit", 8)
    interval = acc_cfg.get("auto_interval", 60)
    custom_prompt = acc_cfg.get("prompt", "")

    while AUTO_TASKS.get(account_name, False):
        stats = get_today_stats().get(account_name, {})
        if stats.get("used", 0) >= daily_limit:
            AUTO_TASKS[account_name] = False
            break

        # 1. 获取交易对
        from topic_main import get_random_topic
        topic = get_random_topic()
        if not topic:
            time.sleep(10)
            continue

        # 2. 生成发文内容
        from ai_core import generate_post_content
        content = generate_post_content(topic["text"], model_type, model_key, custom_prompt)
        if "错误" in content:
            time.sleep(10)
            continue

        # 3. 发文
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
        time.sleep(interval * 60)

def start_auto_task(account_name):
    if AUTO_TASKS.get(account_name, False):
        return False, "任务已在运行"
    AUTO_TASKS[account_name] = True
    thread = threading.Thread(target=auto_publish_task, args=(account_name,), daemon=True)
    thread.start()
    return True, "已启动自动发文"

def stop_auto_task(account_name):
    AUTO_TASKS[account_name] = False
    return True, "已停止自动发文"

# ========== 前端UI（1:1还原原界面 + 全局模型Key配置） ==========
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安自动发文助手</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }
        body {
            background-color: #f5f5f5;
            padding: 20px;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
            background-color: white;
            border-radius: 12px;
            box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
            padding: 30px;
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
            font-size: 32px;
            position: relative;
        }
        .version-badge {
            position: absolute;
            top: 0;
            right: 20px;
            background-color: #28a745;
            color: white;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 14px;
        }
        .tabs {
            display: flex;
            border-bottom: 1px solid #eee;
            margin-bottom: 30px;
        }
        .tab-btn {
            padding: 12px 24px;
            border: none;
            background: none;
            font-size: 18px;
            color: #666;
            cursor: pointer;
            margin-right: 8px;
            border-bottom: 3px solid transparent;
        }
        .tab-btn.active {
            color: #007bff;
            border-bottom-color: #007bff;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 500;
        }
        select, input, textarea {
            width: 100%;
            padding: 12px;
            border: 1px solid #ddd;
            border-radius: 8px;
            font-size: 16px;
            background-color: #f8f9fa;
        }
        textarea {
            resize: vertical;
            min-height: 120px;
        }
        .btn-group {
            display: flex;
            gap: 12px;
            margin-top: 12px;
        }
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            transition: background-color 0.2s;
        }
        .btn-primary {
            background-color: #007bff;
            color: white;
        }
        .btn-success {
            background-color: #28a745;
            color: white;
        }
        .btn-danger {
            background-color: #dc3545;
            color: white;
        }
        .btn-secondary {
            background-color: #6c757d;
            color: white;
        }
        .btn:hover {
            opacity: 0.9;
        }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin: 20px 0;
        }
        .stat-card {
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }
        .stat-number {
            font-size: 36px;
            font-weight: bold;
            color: #333;
            margin-bottom: 8px;
        }
        .stat-label {
            color: #666;
            font-size: 14px;
        }
        .status-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 12px;
            color: white;
            margin-left: 8px;
        }
        .status-running {
            background-color: #28a745;
        }
        .status-stopped {
            background-color: #6c757d;
        }
        .record-item {
            background-color: #f8f9fa;
            padding: 16px;
            border-radius: 8px;
            margin-bottom: 12px;
        }
        .record-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            color: #666;
            font-size: 14px;
        }
        .record-content {
            color: #333;
            white-space: pre-wrap;
        }
        .empty-state {
            text-align: center;
            color: #666;
            padding: 40px 0;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>币安自动发文助手 <span class="version-badge">v2.2</span></h1>

        <div class="tabs">
            <button class="tab-btn" onclick="switchTab('auto')">自动模式</button>
            <button class="tab-btn" onclick="switchTab('manual')">手动模式</button>
            <button class="tab-btn active" onclick="switchTab('config')">账号配置</button>
            <button class="tab-btn" onclick="switchTab('records')">发文记录</button>
        </div>

        <!-- 自动模式 -->
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

        <!-- 手动模式 -->
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
                <input type="text" id="manual_symbol" placeholder="如 BTCUSDT，支持大小写">
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="autoSelectSymbol()">自动选交易对</button>
                    <button class="btn btn-secondary" onclick="generateAnalysis()">生成完整分析</button>
                </div>
            </div>

            <div class="form-group">
                <label>话题分析（可编辑）</label>
                <textarea id="manual_analysis" placeholder="点击上方按钮生成完整分析内容..."></textarea>
            </div>

            <div class="form-group">
                <button class="btn btn-secondary" onclick="generatePostContent()">生成发文内容</button>
            </div>

            <div class="form-group">
                <label>最终内容（可编辑）</label>
                <textarea id="manual_content" placeholder="AI生成的内容将显示在这里..."></textarea>
            </div>

            <button class="btn btn-success" onclick="publishPost()">确认发文</button>
            <div id="manual_log" class="form-group"></div>
        </div>

        <!-- 账号配置（修复：全局模型Key + 币安账号管理） -->
        <div id="config" class="tab-content active">
            <!-- 全局模型Key配置 -->
            <div class="form-group">
                <label>全局DeepSeek API Key（所有账号共享）</label>
                <input type="password" id="global_deepseek_key" placeholder="输入DeepSeek API Key，保存后隐藏为星号">
            </div>
            <div class="form-group">
                <label>全局智谱GLM-4 API Key（所有账号共享）</label>
                <input type="password" id="global_zhipu_key" placeholder="输入智谱API Key，保存后隐藏为星号">
            </div>
            <button class="btn btn-primary" onclick="saveGlobalKeys()">保存全局模型Key</button>
            <div id="global_key_log" class="form-group"></div>

            <hr style="margin: 30px 0;">

            <!-- 币安账号管理 -->
            <div class="form-group">
                <label>添加币安广场账号</label>
                <div style="display: flex; gap: 12px; margin-bottom: 8px;">
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
                <label>模型类型（自动带入全局Key）</label>
                <select id="config_model">
                    <option value="zhipu">智谱GLM-4</option>
                    <option value="deepseek">DeepSeek-v4-flash</option>
                </select>
            </div>

            <div class="form-group">
                <label>专属提示词</label>
                <textarea id="config_prompt" placeholder="该账号的专属AI提示词，留空使用默认提示词..."></textarea>
            </div>

            <div class="form-group">
                <label>每日发文限额</label>
                <input type="number" id="config_daily_limit" value="8" min="1">
            </div>

            <div class="form-group">
                <label>自动发文间隔（分钟）</label>
                <input type="number" id="config_interval" value="60" min="5">
            </div>

            <button class="btn btn-primary" onclick="saveAccountConfig()">保存账号配置</button>
            <div id="config_log" class="form-group"></div>
        </div>

        <!-- 发文记录 -->
        <div id="records" class="tab-content">
            <div class="form-group">
                <div style="display: flex; gap: 12px; align-items: center;">
                    <select id="record_account" style="flex: 1;">
                        <option value="">所有账号</option>
                        {% for acc in accounts %}
                        <option value="{{ acc.name }}">{{ acc.name }}</option>
                        {% endfor %}
                    </select>
                    <input type="date" id="record_date" style="flex: 1;">
                </div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="queryRecords()">查询</button>
                    <button class="btn btn-secondary" onclick="exportRecords()">导出</button>
                </div>
            </div>

            <div id="records_list"></div>

            <div class="form-group" style="margin-top: 30px;">
                <label>删除记录</label>
                <div style="display: flex; gap: 12px; align-items: center;">
                    <select id="delete_account" style="flex: 1;">
                        <option value="">所有账号</option>
                        {% for acc in accounts %}
                        <option value="{{ acc.name }}">{{ acc.name }}</option>
                        {% endfor %}
                    </select>
                    <input type="date" id="delete_date" style="flex: 1;">
                </div>
                <div class="btn-group">
                    <button class="btn btn-danger" onclick="deleteSelectedRecords()">删除选中记录</button>
                    <button class="btn btn-danger" onclick="deleteAllRecords()">删除所有记录</button>
                </div>
                <p style="color: #dc3545; margin-top: 8px; font-size: 14px;">谨慎操作！删除后无法恢复</p>
            </div>
        </div>
    </div>

    <script>
        // Tab切换
        function switchTab(tab) {
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.querySelector(`.tab-btn[onclick="switchTab('${tab}')"]`).classList.add('active');
            document.getElementById(tab).classList.add('active');
            if (tab === 'auto') refreshAutoStats();
            if (tab === 'records') queryRecords();
        }

        // 自动模式
        function refreshAutoStats() {
            fetch('/api/stats').then(res => res.json()).then(stats => {
                const html = Object.entries(stats).map(([name, data]) => `
                    <div class="stat-card">
                        <div class="stat-number">${data.used}</div>
                        <div class="stat-label">${name}</div>
                        <div class="stat-label">自动: ${data.auto_used} | 手动: ${data.manual_used}</div>
                        <div class="stat-label">剩余: ${data.remaining}/${data.limit}</div>
                        <span class="status-badge ${data.running ? 'status-running' : 'status-stopped'}">
                            ${data.running ? '运行中' : '已停止'}
                        </span>
                    </div>
                `).join('');
                document.getElementById('auto_stats').innerHTML = html;
            });
        }

        function startAuto() {
            const account = document.getElementById('auto_account').value;
            fetch('/api/auto/start', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({account})
            }).then(res => res.json()).then(data => {
                alert(data.msg);
                refreshAutoStats();
            });
        }

        function stopAuto() {
            const account = document.getElementById('auto_account').value;
            fetch('/api/auto/stop', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({account})
            }).then(res => res.json()).then(data => {
                alert(data.msg);
                refreshAutoStats();
            });
        }

        // 手动模式
        function autoSelectSymbol() {
            fetch('/api/topic/random').then(res => res.json()).then(topic => {
                document.getElementById('manual_symbol').value = topic.symbol;
                document.getElementById('manual_analysis').value = topic.text;
            });
        }

        function generateAnalysis() {
            const symbol = document.getElementById('manual_symbol').value.trim().toUpperCase();
            if (!symbol) return alert('请输入交易对或点击自动选交易对');
            fetch(`/api/topic?symbol=${symbol}`).then(res => res.json()).then(topic => {
                document.getElementById('manual_analysis').value = topic.text;
            });
        }

        function generatePostContent() {
            const account = document.getElementById('manual_account').value;
            const analysis = document.getElementById('manual_analysis').value;
            if (!analysis) return alert('请先生成话题分析');
            fetch('/api/generate', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({account, analysis})
            }).then(res => res.text()).then(content => {
                document.getElementById('manual_content').value = content;
            });
        }

        function publishPost() {
            const account = document.getElementById('manual_account').value;
            const content = document.getElementById('manual_content').value;
            if (!content) return alert('请先生成或输入发文内容');
            fetch('/api/publish', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({account, content})
            }).then(res => res.json()).then(data => {
                document.getElementById('manual_log').innerText = JSON.stringify(data, null, 2);
                alert(data.msg);
            });
        }

        // 账号配置 - 全局模型Key
        function saveGlobalKeys() {
            const deepseekKey = document.getElementById('global_deepseek_key').value;
            const zhipuKey = document.getElementById('global_zhipu_key').value;
            fetch('/api/global_keys/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({deepseek: deepseekKey, zhipu: zhipuKey})
            }).then(res => res.json()).then(data => {
                document.getElementById('global_key_log').innerText = data.msg;
                alert('全局模型Key保存成功');
                // 保存后显示为星号
                if (deepseekKey) document.getElementById('global_deepseek_key').value = '********';
                if (zhipuKey) document.getElementById('global_zhipu_key').value = '********';
            });
        }

        // 账号配置 - 币安账号管理
        function addBinanceAccount() {
            const name = document.getElementById('new_acc_name').value.trim();
            const key = document.getElementById('new_acc_key').value.trim();
            if (!name || !key) return alert('账号名称和API Key不能为空');
            fetch('/api/binance/add', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, key})
            }).then(res => res.json()).then(data => {
                alert(data.msg);
                location.reload();
            });
        }

        function deleteBinanceAccount() {
            const name = document.getElementById('config_account').value;
            if (!confirm('确定删除该账号？删除后无法恢复')) return;
            fetch('/api/binance/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name})
            }).then(res => res.json()).then(data => {
                alert(data.msg);
                location.reload();
            });
        }

        // 账号配置 - 账号配置
        function loadAccountConfig() {
            const account = document.getElementById('config_account').value;
            fetch(`/api/config?account=${account}`).then(res => res.json()).then(cfg => {
                document.getElementById('config_model').value = cfg.model_type || 'zhipu';
                document.getElementById('config_prompt').value = cfg.prompt || '';
                document.getElementById('config_daily_limit').value = cfg.daily_limit || 8;
                document.getElementById('config_interval').value = cfg.auto_interval || 60;
            });
        }

        function saveAccountConfig() {
            const account = document.getElementById('config_account').value;
            const model_type = document.getElementById('config_model').value;
            const prompt = document.getElementById('config_prompt').value;
            const daily_limit = parseInt(document.getElementById('config_daily_limit').value);
            const auto_interval = parseInt(document.getElementById('config_interval').value);

            fetch('/api/config/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    account, model_type, prompt, daily_limit, auto_interval
                })
            }).then(res => res.json()).then(data => {
                document.getElementById('config_log').innerText = data.msg;
                alert('账号配置保存成功');
            });
        }

        // 发文记录
        function queryRecords() {
            const account = document.getElementById('record_account').value;
            const date = document.getElementById('record_date').value;
            fetch(`/api/records?account=${account}&date=${date}`).then(res => res.json()).then(records => {
                if (records.length === 0) {
                    document.getElementById('records_list').innerHTML = '<div class="empty-state">暂无记录</div>';
                    return;
                }
                const html = records.map(r => `
                    <div class="record-item">
                        <div class="record-header">
                            <span>${r.time} | ${r.account} | ${r.mode === 'auto' ? '自动' : '手动'}</span>
                            <span>${r.symbol} | ${r.status === 'success' ? '成功' : '失败'}</span>
                        </div>
                        <div class="record-content">${r.content}</div>
                    </div>
                `).join('');
                document.getElementById('records_list').innerHTML = html;
            });
        }

        function exportRecords() {
            const account = document.getElementById('record_account').value;
            const date = document.getElementById('record_date').value;
            window.open(`/api/export?account=${account}&date=${date}`);
        }

        function deleteSelectedRecords() {
            const account = document.getElementById('delete_account').value;
            const date = document.getElementById('delete_date').value;
            if (!confirm('确定删除选中的记录？删除后无法恢复')) return;
            fetch('/api/records/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({account, date})
            }).then(res => res.json()).then(data => {
                alert(data.msg);
                queryRecords();
            });
        }

        function deleteAllRecords() {
            if (!confirm('确定删除所有记录？删除后无法恢复')) return;
            fetch('/api/records/delete/all', {method: 'POST'}).then(res => res.json()).then(data => {
                alert(data.msg);
                queryRecords();
            });
        }

        window.onload = function() {
            document.getElementById('record_date').value = new Date().toISOString().split('T')[0];
            document.getElementById('delete_date').value = new Date().toISOString().split('T')[0];
            refreshAutoStats();
            loadAccountConfig();
            // 加载全局Key状态（仅显示是否已配置）
            fetch('/api/global_keys').then(res => res.json()).then(keys => {
                if (keys.deepseek) document.getElementById('global_deepseek_key').value = '********';
                if (keys.zhipu) document.getElementById('global_zhipu_key').value = '********';
            });
        };
    </script>
</body>
</html>
"""

# ========== 路由 ==========
@app.route('/')
def index():
    # 兼容原有环境变量（首次运行加载）
    if not BINANCE_ACCOUNTS:
        binance_accounts = os.getenv("BINANCE_ACCOUNTS", "[]")
        try:
            BINANCE_ACCOUNTS.extend(json.loads(binance_accounts))
        except:
            pass
    return render_template_string(UI_TEMPLATE, accounts=BINANCE_ACCOUNTS)

# 全局模型Key接口
@app.route('/api/global_keys')
def get_global_keys():
    return jsonify({
        "deepseek": bool(GLOBAL_MODEL_KEYS["deepseek"]),
        "zhipu": bool(GLOBAL_MODEL_KEYS["zhipu"])
    })

@app.route('/api/global_keys/save', methods=['POST'])
def save_global_keys():
    data = request.json
    if data.get('deepseek'):
        GLOBAL_MODEL_KEYS["deepseek"] = data['deepseek']
    if data.get('zhipu'):
        GLOBAL_MODEL_KEYS["zhipu"] = data['zhipu']
    return jsonify({"msg": "全局模型Key保存成功"})

# 币安账号管理接口
@app.route('/api/binance/add', methods=['POST'])
def add_binance_account():
    data = request.json
    name = data.get('name')
    key = data.get('key')
    if not name or not key:
        return jsonify({"msg": "账号名称和API Key不能为空"})
    for acc in BINANCE_ACCOUNTS:
        if acc["name"] == name:
            return jsonify({"msg": "账号已存在"})
    BINANCE_ACCOUNTS.append({"name": name, "key": key})
    return jsonify({"msg": "账号添加成功"})

@app.route('/api/binance/delete', methods=['POST'])
def delete_binance_account():
    name = request.json.get('name')
    global BINANCE_ACCOUNTS
    BINANCE_ACCOUNTS = [acc for acc in BINANCE_ACCOUNTS if acc["name"] != name]
    if name in ACCOUNT_CONFIG:
        del ACCOUNT_CONFIG[name]
    return jsonify({"msg": "账号删除成功"})

# 自动模式接口
@app.route('/api/stats')
def get_stats():
    return jsonify(get_today_stats())

@app.route('/api/auto/start', methods=['POST'])
def auto_start():
    account = request.json.get('account')
    ok, msg = start_auto_task(account)
    return jsonify({"msg": msg})

@app.route('/api/auto/stop', methods=['POST'])
def auto_stop():
    account = request.json.get('account')
    ok, msg = stop_auto_task(account)
    return jsonify({"msg": msg})

# 手动模式接口
@app.route('/api/topic/random')
def get_random_topic_api():
    from topic_main import get_random_topic
    topic = get_random_topic()
    return jsonify(topic or {"error": "获取失败"})

@app.route('/api/topic')
def get_topic_api():
    from topic_main import get_single_symbol_topic
    symbol = request.args.get('symbol')
    topic = get_single_symbol_topic(symbol)
    return jsonify(topic or {"error": "获取失败"})

@app.route('/api/generate', methods=['POST'])
def generate_api():
    data = request.json
    account = data.get('account')
    analysis = data.get('analysis')
    if not account or not analysis:
        return "参数错误"
    cfg = ACCOUNT_CONFIG.get(account, {})
    model_type = cfg.get('model_type', 'zhipu')
    model_key = GLOBAL_MODEL_KEYS.get(model_type, "")
    prompt = cfg.get('prompt', '')
    from ai_core import generate_post_content
    content = generate_post_content(analysis, model_type, model_key, prompt)
    return content

@app.route('/api/publish', methods=['POST'])
def publish_api():
    data = request.json
    account = data.get('account')
    content = data.get('content')
    if not account or not content:
        return jsonify({"success": False, "msg": "参数错误"})
    binance_key = next((a["key"] for a in BINANCE_ACCOUNTS if a["name"] == account), None)
    from post_main import post_to_binance
    success, msg, post_id = post_to_binance(content, binance_key)
    record = {
        "date": get_today_date(),
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account": account,
        "symbol": "手动",
        "content": content,
        "post_id": post_id,
        "mode": "manual",
        "status": "success" if success else "fail",
        "msg": msg
    }
    save_record(record)
    return jsonify({"success": success, "msg": msg, "post_id": post_id})

# 账号配置接口
@app.route('/api/config')
def get_config():
    account = request.args.get('account')
    cfg = ACCOUNT_CONFIG.get(account, {})
    return jsonify({
        "model_type": cfg.get("model_type", "zhipu"),
        "prompt": cfg.get("prompt", ""),
        "daily_limit": cfg.get("daily_limit", 8),
        "auto_interval": cfg.get("auto_interval", 60)
    })

@app.route('/api/config/save', methods=['POST'])
def save_config():
    data = request.json
    account = data.get('account')
    if account not in ACCOUNT_CONFIG:
        ACCOUNT_CONFIG[account] = {}
    ACCOUNT_CONFIG[account]['model_type'] = data['model_type']
    ACCOUNT_CONFIG[account]['prompt'] = data['prompt']
    ACCOUNT_CONFIG[account]['daily_limit'] = data['daily_limit']
    ACCOUNT_CONFIG[account]['auto_interval'] = data['auto_interval']
    return jsonify({"msg": "账号配置保存成功"})

# 发文记录接口
@app.route('/api/records')
def get_records():
    account = request.args.get('account', '')
    date = request.args.get('date', '')
    records = load_records()
    filtered = []
    for r in records:
        if account and r.get('account') != account:
            continue
        if date and r.get('date') != date:
            continue
        filtered.append(r)
    return jsonify(filtered)

@app.route('/api/export')
def export_records():
    account = request.args.get('account', '')
    date = request.args.get('date', '')
    records = load_records()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["日期", "时间", "账号", "模式", "交易对", "内容", "状态", "消息"])
    for r in records:
        if account and r.get('account') != account:
            continue
        if date and r.get('date') != date:
            continue
        writer.writerow([
            r.get('date'), r.get('time'), r.get('account'),
            r.get('mode'), r.get('symbol'), r.get('content'),
            r.get('status'), r.get('msg')
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=发文记录_{get_today_date()}.csv"}
    )

@app.route('/api/records/delete', methods=['POST'])
def delete_records():
    data = request.json
    account = data.get('account', '')
    date = data.get('date', '')
    records = load_records()
    filtered = []
    for r in records:
        if account and r.get('account') != account:
            filtered.append(r)
            continue
        if date and r.get('date') != date:
            filtered.append(r)
            continue
    save_json(RECORDS_FILE, filtered)
    return jsonify({"msg": "删除成功"})

@app.route('/api/records/delete/all', methods=['POST'])
def delete_all_records():
    save_json(RECORDS_FILE, [])
    return jsonify({"msg": "所有记录已删除"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
