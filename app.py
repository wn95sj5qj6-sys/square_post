from flask import Flask, render_template_string, request, jsonify
from config import ZHIPU_API_KEY, BINANCE_API_KEYS
from topic_main import run_topic
from ai_core import generate_content
from post_main import post_with_key

app = Flask(__name__)

# 简洁网页面板
WEB_UI = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>币安广场发文面板</title>
    <style>
        body{font-family:Arial;margin:30px;background:#1a1a1a;color:white;}
        .box{max-width:450px;margin:auto;background:#222;padding:25px;border-radius:12px;}
        h2{text-align:center;color:#00d1ff;margin-bottom:20px;}
        select,button{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:none;font-size:15px;}
        select{background:#333;color:white;}
        button{background:#00d1ff;color:black;font-weight:bold;cursor:pointer;}
        button:hover{opacity:0.9;}
        #log{background:#111;padding:12px;border-radius:8px;margin-top:15px;min-height:100px;white-space:pre-wrap;font-size:12px;}
    </style>
</head>
<body>
    <div class="box">
        <h2>📤 币安广场自动发文</h2>
        <select id="account">
            {% for i, key in accounts %}
            <option value="{{ key }}">账号 {{ i+1 }}</option>
            {% endfor %}
        </select>
        <button onclick="startPublish()">🚀 开始发文</button>
        <div id="log">等待操作...</div>
    </div>
    <script>
        function startPublish(){
            let key = document.getElementById('account').value;
            let log = document.getElementById('log');
            log.textContent = "执行中...";
            
            fetch('/publish', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({api_key: key})
            })
            .then(res=>res.json())
            .then(data=>{
                log.textContent = data.msg;
            })
            .catch(err=>{
                log.textContent = "请求异常";
            });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    accounts = list(enumerate(BINANCE_API_KEYS))
    return render_template_string(WEB_UI, accounts=accounts)

@app.route('/publish', methods=['POST'])
def publish():
    try:
        api_key = request.json.get('api_key', '').strip()
        if not api_key:
            return jsonify({"msg": "❌ 未选择账号"})
        
        # 执行流程
        topic = run_topic()
        if not topic:
            return jsonify({"msg": "❌ 话题生成失败"})
        
        content, strategy = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            return jsonify({"msg": "❌ AI生成失败"})
        
        ok = post_with_key(content, api_key)
        if ok:
            return jsonify({"msg": f"🎉 发文成功！\n主题：{topic}\n内容已发布"})
        else:
            return jsonify({"msg": "❌ 发文失败（查看日志）"})
    except Exception as e:
        return jsonify({"msg": f"❌ 异常：{str(e)}"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)