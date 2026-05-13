from flask import Flask, render_template_string, request, jsonify
import threading
import time
from post_main import PostManager
from topic_main import TopicManager
from utils import Utils
import random

# ===================== 【新增：双模型配置面板】 =====================
# 你可以在这里配置多个账号，在网页直接选择
MODEL_ACCOUNTS = [
    {
        "id": 1,
        "name": "账号1 - DeepSeek",
        "model_type": "deepseek",
        "api_key": "sk-1068d968c1594a75bd266aaf869ce645",
    },
    {
        "id": 2,
        "name": "账号2 - 智谱",
        "model_type": "zhipu",
        "api_key": "255b13ab88924d52ace6dc83474bf820.m7UuiXLWCiKHRTod",
    },
]

# 当前选中的模型配置（默认选中第一个）
CURRENT_MODEL_CONFIG = MODEL_ACCOUNTS[0]
# ==================================================================

app = Flask(__name__)

# 初始化业务模块（自动传入当前模型）
post_manager = PostManager(model_config=CURRENT_MODEL_CONFIG)
topic_manager = TopicManager()
utils = Utils()

# 首页模板（完全保留你的原有UI，只新增模型选择区域）
index_html = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>square_post 发文系统</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: Arial, sans-serif;
            background: #f5f5f5;
            padding: 20px;
        }
        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            text-align: center;
            color: #333;
            margin-bottom: 30px;
        }

        /* 新增：模型选择面板 */
        .model-panel {
            background: #e8f4ff;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 25px;
        }
        .model-panel h3 {
            margin-bottom: 10px;
            color: #0d47a1;
        }
        .model-select {
            padding: 8px 12px;
            font-size: 14px;
            border-radius: 5px;
            border: 1px solid #ccc;
        }

        .section {
            margin-bottom: 30px;
            padding: 20px;
            border: 1px solid #ddd;
            border-radius: 8px;
        }
        .section h2 {
            color: #555;
            margin-bottom: 15px;
            font-size: 18px;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #666;
            font-weight: bold;
        }
        input, textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 5px;
            font-size: 14px;
        }
        textarea {
            min-height: 100px;
            resize: vertical;
        }
        .btn {
            background: #007bff;
            color: white;
            border: none;
            padding: 10px 20px;
            border-radius: 5px;
            cursor: pointer;
            font-size: 14px;
            margin-right: 10px;
        }
        .btn:hover {
            background: #0056b3;
        }
        .btn-success {
            background: #28a745;
        }
        .btn-success:hover {
            background: #1e7e34;
        }
        .result {
            margin-top: 15px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 5px;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>square_post 自动化发文系统</h1>

        <!-- ===================== 【新增：模型选择面板】 ===================== -->
        <div class="model-panel">
            <h3>📌 模型账号配置（手动选择）</h3>
            <select id="modelAccountSelect" class="model-select" onchange="switchModelAccount()">
                {% for account in model_accounts %}
                <option value="{{ account.id }}" {% if account.id == current_config.id %}selected{% endif %}>
                    {{ account.name }} ({{ account.model_type }})
                </option>
                {% endfor %}
            </select>
            <div style="margin-top:8px; font-size:13px; color:#333;">
                当前使用模型：<b>{{ current_config.model_type }}</b>
            </div>
        </div>
        <!-- ================================================================= -->

        <!-- 帖子生成模块 -->
        <div class="section">
            <h2>📝 帖子生成</h2>
            <div class="form-group">
                <label>主题</label>
                <input type="text" id="post_topic" placeholder="请输入帖子主题">
            </div>
            <div class="form-group">
                <label>要求</label>
                <textarea id="post_requirement" placeholder="请输入生成要求（可选）"></textarea>
            </div>
            <button class="btn" onclick="generatePost()">生成内容</button>
            <div id="post_result" class="result"></div>
        </div>

        <!-- 手动发文模块 -->
        <div class="section">
            <h2>🚀 手动发文</h2>
            <div class="form-group">
                <label>发文内容</label>
                <textarea id="publish_content" placeholder="请输入发文内容"></textarea>
            </div>
            <button class="btn btn-success" onclick="publishPost()">立即发文</button>
            <div id="publish_result" class="result"></div>
        </div>

        <!-- 自动发文模块 -->
        <div class="section">
            <h2>⚙️ 自动发文</h2>
            <div class="form-group">
                <label>自动发文间隔（秒）</label>
                <input type="number" id="auto_interval" value="60" placeholder="请输入自动发文间隔">
            </div>
            <div class="form-group">
                <label>自动发文主题列表（一行一个）</label>
                <textarea id="auto_topics" placeholder="请输入自动发文主题列表"></textarea>
            </div>
            <button class="btn" onclick="startAutoPublish()">开始自动发文</button>
            <button class="btn" onclick="stopAutoPublish()">停止自动发文</button>
            <div id="auto_result" class="result"></div>
        </div>
    </div>

    <script>
        // 切换模型账号
        function switchModelAccount() {
            const accountId = document.getElementById('modelAccountSelect').value;
            fetch('/switch_model', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({account_id: parseInt(accountId)})
            }).then(res => res.json()).then(data => {
                alert('模型切换成功：' + data.current.name);
                location.reload();
            });
        }

        // 生成帖子
        function generatePost() {
            const topic = document.getElementById('post_topic').value;
            const requirement = document.getElementById('post_requirement').value;
            fetch('/generate_post', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({topic, requirement})
            }).then(res => res.json()).then(data => {
                document.getElementById('post_result').innerText = data.content;
            });
        }

        // 手动发文
        function publishPost() {
            const content = document.getElementById('publish_content').value;
            fetch('/publish_post', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content})
            }).then(res => res.json()).then(data => {
                document.getElementById('publish_result').innerText = JSON.stringify(data, null, 2);
            });
        }

        // 自动发文
        let autoRunning = false;
        function startAutoPublish() {
            autoRunning = true;
            const interval = document.getElementById('auto_interval').value;
            const topics = document.getElementById('auto_topics').value.split('\\n').filter(t => t.trim());
            fetch('/start_auto_publish', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({interval, topics})
            });
        }
        function stopAutoPublish() {
            autoRunning = false;
            fetch('/stop_auto_publish', {method: 'POST'});
        }
    </script>
</body>
</html>
"""

# 首页路由（传入模型配置）
@app.route('/')
def index():
    return render_template_string(
        index_html,
        model_accounts=MODEL_ACCOUNTS,
        current_config=CURRENT_MODEL_CONFIG
    )

# 【新增】切换模型账号
@app.route('/switch_model', methods=['POST'])
def switch_model():
    global CURRENT_MODEL_CONFIG, post_manager
    account_id = request.json.get('account_id')
    for acc in MODEL_ACCOUNTS:
        if acc['id'] == account_id:
            CURRENT_MODEL_CONFIG = acc
            post_manager = PostManager(model_config=CURRENT_MODEL_CONFIG)
            return jsonify({"status": "ok", "current": CURRENT_MODEL_CONFIG})
    return jsonify({"status": "error", "msg": "账号不存在"})

# 生成帖子
@app.route('/generate_post', methods=['POST'])
def generate_post():
    data = request.get_json()
    topic = data.get('topic', '')
    requirement = data.get('requirement', '')
    content = post_manager.generate_post(topic, requirement)
    return jsonify({"content": content})

# 手动发文
@app.route('/publish_post', methods=['POST'])
def publish_post():
    data = request.get_json()
    content = data.get('content', '')
    result = post_manager.publish(content)
    return jsonify(result)

# 自动发文
auto_running = False
@app.route('/start_auto_publish', methods=['POST'])
def start_auto_publish():
    global auto_running
    auto_running = True
    data = request.get_json()
    interval = int(data.get('interval', 60))
    topics = data.get('topics', [])

    def auto_task():
        while auto_running and topics:
            topic = random.choice(topics)
            content = post_manager.generate_post(topic, "自动生成")
            post_manager.publish(content)
            time.sleep(interval)

    thread = threading.Thread(target=auto_task, daemon=True)
    thread.start()
    return jsonify({"status": "started"})

@app.route('/stop_auto_publish', methods=['POST'])
def stop_auto_publish():
    global auto_running
    auto_running = False
    return jsonify({"status": "stopped"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)