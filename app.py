from flask import Flask, render_template_string, request, jsonify
import threading
import time
from post_main import PostManager
from topic_main import TopicManager
from utils import Utils

app = Flask(__name__)

# ===================== 内存全局配置（网页填写，不写死密钥） =====================
SYS_CONFIG = {
    # 模型密钥 网页填写
    "zhipu_api_key": "",
    "deepseek_api_key": "",
    # 当前选中
    "current_model_type": "deepseek",  # deepseek / zhipu
    "current_model_name": "deepseek-chat",
    "temperature": 0.7,
    "max_tokens": 2048
}
auto_running = False
# =============================================================================

# 初始化业务
post_manager = PostManager(sys_config=SYS_CONFIG)
topic_manager = TopicManager()
utils = Utils()

# 完整前端页面：保留你原有所有UI布局，只新增【模型配置面板】，不改动原有样式
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
        /* 新增配置面板样式，不干扰原有布局 */
        .config-panel {
            background: #f0f8ff;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 25px;
            border:1px solid #cce5ff;
        }
        .config-panel h3 {
            margin-bottom:15px;
            color:#0d47a1;
        }
        .row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap:15px;
            margin-bottom:12px;
        }
        .form-item label {
            display:block;
            margin-bottom:5px;
            color:#555;
            font-weight:bold;
        }
        .form-item input,.form-item select {
            width:100%;
            padding:8px;
            border:1px solid #ccc;
            border-radius:4px;
        }
        .btn-save {
            background:#28a745;
            color:#fff;
            border:none;
            padding:8px 20px;
            border-radius:4px;
            cursor:pointer;
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

        <!-- 模型配置面板：网页填Key，不写死代码 -->
        <div class="config-panel">
            <h3>🔑 大模型配置（网页填写，无需改代码）</h3>
            <div class="row">
                <div class="form-item">
                    <label>智谱 API Key</label>
                    <input type="text" id="zhipuKey" value="{{cfg.zhipu_api_key}}" placeholder="填写智谱API密钥">
                </div>
                <div class="form-item">
                    <label>DeepSeek API Key</label>
                    <input type="text" id="dsKey" value="{{cfg.deepseek_api_key}}" placeholder="填写DeepSeekAPI密钥">
                </div>
            </div>
            <div class="row">
                <div class="form-item">
                    <label>选择模型</label>
                    <select id="modelType" onchange="refreshModelSelect()">
                        <option value="deepseek" {% if cfg.current_model_type=='deepseek' %}selected{% endif %}>DeepSeek</option>
                        <option value="zhipu" {% if cfg.current_model_type=='zhipu' %}selected{% endif %}>智谱</option>
                    </select>
                </div>
                <div class="form-item">
                    <label>模型版本</label>
                    <select id="modelName">
                    </select>
                </div>
            </div>
            <div class="row">
                <div class="form-item">
                    <label>创作温度(0.1~1.2)</label>
                    <input type="number" step="0.1" min="0.1" max="1.2" id="tempVal" value="{{cfg.temperature}}">
                </div>
                <div class="form-item" style="display:flex;align-items:flex-end;">
                    <button class="btn-save" onclick="saveConfig()">保存配置</button>
                </div>
            </div>
        </div>

        <!-- 以下是你原有全部页面，完全未改动 -->
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

        <div class="section">
            <h2>🚀 手动发文</h2>
            <div class="form-group">
                <label>发文内容</label>
                <textarea id="publish_content" placeholder="请输入发文内容"></textarea>
            </div>
            <button class="btn btn-success" onclick="publishPost()">立即发文</button>
            <div id="publish_result" class="result"></div>
        </div>

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
        // 模型版本列表
        const modelMap = {
            deepseek:["deepseek-chat","deepseek-coder"],
            zhipu:["glm-4","glm-4-flash","glm-3-turbo"]
        };
        // 初始化模型下拉
        function refreshModelSelect(){
            let type = document.getElementById("modelType").value;
            let sel = document.getElementById("modelName");
            sel.innerHTML = "";
            modelMap[type].forEach(m=>{
                let opt = document.createElement("option");
                opt.value = m;
                opt.innerText = m;
                if(m === "{{cfg.current_model_name}}") opt.selected = true;
                sel.appendChild(opt);
            });
        }
        window.onload = function(){
            refreshModelSelect();
        }
        // 保存配置到服务端内存
        function saveConfig(){
            let payload = {
                zhipu_key:document.getElementById("zhipuKey").value,
                deepseek_key:document.getElementById("dsKey").value,
                model_type:document.getElementById("modelType").value,
                model_name:document.getElementById("modelName").value,
                temperature:parseFloat(document.getElementById("tempVal").value)
            };
            fetch('/save_config',{
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify(payload)
            }).then(res=>res.json()).then(data=>{
                alert("配置保存成功，立即生效！");
                location.reload();
            });
        }
        // 原有函数不变
        function generatePost(){
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
        function publishPost(){
            const content = document.getElementById('publish_content').value;
            fetch('/publish_post', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content})
            }).then(res => res.json()).then(data => {
                document.getElementById('publish_result').innerText = JSON.stringify(data, null, 2);
            });
        }
        function startAutoPublish(){
            const interval = document.getElementById('auto_interval').value;
            const topics = document.getElementById('auto_topics').value.split('\n').filter(t => t.trim());
            fetch('/start_auto_publish', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({interval, topics})
            });
            alert("已启动自动发文");
        }
        function stopAutoPublish(){
            fetch('/stop_auto_publish', {method: 'POST'});
            alert("已停止自动发文");
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(index_html, cfg=SYS_CONFIG)

# 保存配置接口
@app.route('/save_config', methods=['POST'])
def save_config():
    data = request.get_json()
    SYS_CONFIG["zhipu_api_key"] = data.get("zhipu_key","")
    SYS_CONFIG["deepseek_api_key"] = data.get("deepseek_key","")
    SYS_CONFIG["current_model_type"] = data.get("model_type","deepseek")
    SYS_CONFIG["current_model_name"] = data.get("model_name","deepseek-chat")
    SYS_CONFIG["temperature"] = data.get("temperature",0.7)
    return jsonify({"status":"ok"})

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
@app.route('/start_auto_publish', methods=['POST'])
def start_auto_publish():
    global auto_running
    auto_running = True
    data = request.get_json()
    interval = int(data.get('interval', 60))
    topics = data.get('topics', [])

    def auto_task():
        global auto_running
        while auto_running and topics:
            for t in topics:
                if not auto_running:
                    break
                content = post_manager.generate_post(t, "自动生成广场发文内容")
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
    app.run(host='0.0.0.0', port=5000)