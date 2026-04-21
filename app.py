from flask import Flask, render_template_string, request, jsonify
import os
import logging

# 关闭多余日志，防止 Railway 限流
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__)

# 环境变量
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip().split(",")
BINANCE_ACCOUNTS = [k.strip() for k in BINANCE_ACCOUNTS if k.strip()]

from topic_main import run_topic
from ai_core import generate_content
from post_main import post_with_key

WEB_UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>币安发文</title>
    <style>
        body{background:#121212;color:white;font-family:Arial;padding:20px;}
        .main{max-width:400px;margin:0 auto;background:#1e1e1e;padding:25px;border-radius:12px;}
        h2{color:#00ccff;text-align:center;}
        select,button{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:none;background:#2a2a2a;color:white;font-size:16px;}
        button{background:#00ccff;color:black;font-weight:bold;cursor:pointer;}
        #log{background:#111;padding:12px;border-radius:8px;margin-top:10px;min-height:100px;font-size:13px;white-space:pre-wrap;}
    </style>
</head>
<body>
    <div class="main">
        <h2>📤 币安广场自动发文</h2>
        <select id="acc">
            {% for a in accounts %}
            <option value="{{a}}">账号 {{loop.index}}</option>
            {% endfor %}
        </select>
        <button onclick="go()">🚀 一键发文</button>
        <div id="log">等待操作...</div>
    </div>
    <script>
        function go(){
            document.getElementById('log').innerText = '执行中...'
            fetch('/publish', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({key: document.getElementById('acc').value})
            }).then(r=>r.json()).then(d=>{
                document.getElementById('log').innerText = d.msg
            })
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(WEB_UI, accounts=BINANCE_ACCOUNTS)

@app.route('/publish', methods=['POST'])
def publish():
    try:
        key = request.json.get('key', '').strip()
        if not key or not ZHIPU_API_KEY:
            return jsonify({"msg": "❌ 配置不完整"})
        
        topic = run_topic()
        content, _ = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            return jsonify({"msg": "❌ AI生成失败"})
        
        ok = post_with_key(content, key)
        return jsonify({"msg": "🎉 发文成功！" if ok else "❌ 发文失败"})
    except Exception as e:
        return jsonify({"msg": f"❌ 错误：{str(e)}"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
