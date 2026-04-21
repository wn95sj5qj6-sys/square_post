from flask import Flask, render_template_string, request, jsonify
import os
import logging

logging.getLogger('werkzeug').setLevel(logging.ERROR)
app = Flask(__name__)

# 账号格式：名字|key,名字|key,名字|key
BINANCE_ACCOUNTS = os.getenv("BINANCE_ACCOUNTS", "").strip()
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()

def get_account_list():
    accounts = []
    if not BINANCE_ACCOUNTS:
        return []
    for item in BINANCE_ACCOUNTS.split(","):
        item = item.strip()
        if "|" not in item:
            continue
        name, key = item.split("|", 1)
        name = name.strip()
        key = key.strip()
        if name and key:
            accounts.append({"name": name, "key": key})
    return accounts

from topic_main import run_topic
from ai_core import generate_content
from post_main import post_content

HTML = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>币安广场发文助手</title>
<style>
    *{box-sizing:border-box}
    body{background:#121212;color:#eaeaea;font-family:Arial;padding:15px;margin:0}
    .card{max-width:480px;margin:0 auto;background:#1c1c1c;padding:20px;border-radius:14px}
    h2{color:#00dfff;text-align:center;margin:0 0 16px}
    select,button{width:100%;padding:14px;border-radius:10px;border:none;background:#272727;color:#fff;font-size:16px;margin-bottom:12px}
    button{background:#00dfff;color:#000;font-weight:bold;cursor:pointer}
    button:disabled{background:#444}
    #log{background:#111;padding:14px;border-radius:10px;min-height:240px;white-space:pre-wrap;font-size:14px;line-height:1.5}
</style>

<div class="card">
    <h2>📤 币安广场自动发文</h2>
    <select id="account">
        {% for acc in accounts %}
        <option value="{{acc.key}}">{{acc.name}}</option>
        {% endfor %}
    </select>
    <button onclick="start()">🚀 开始发文</button>
    <div id="log">等待启动...</div>
</div>

<script>
    let log = document.getElementById('log');
    let btn = document.querySelector('button');

    function append(text){
        log.textContent += text + '\\n';
        window.scrollTo(0, document.body.scrollHeight);
    }

    function start(){
        btn.disabled = true;
        log.textContent = '';
        append('✅ 开始执行发文流程...');

        let key = document.getElementById('account').value;
        fetch('/run', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({key: key})
        }).then(res => res.json()).then(data => {
            append(data.log);
            btn.disabled = false;
        }).catch(e=>{
            append('❌ 请求异常');
            btn.disabled = false;
        });
    }
</script>
"""

@app.route('/')
def index():
    accounts = get_account_list()
    return render_template_string(HTML, accounts=accounts)

@app.route('/run', methods=['POST'])
def run():
    try:
        api_key = request.json.get('key', '').strip()
        if not api_key:
            return jsonify({"log": "❌ 未选择账号"})
        if not ZHIPU_API_KEY:
            return jsonify({"log": "❌ 未配置AI密钥"})

        log = ""

        # 1. 抓取话题
        log += "✅ 正在抓取行情话题...\n"
        topic = run_topic()
        log += f"📢 话题：{topic_text}\n\n"

        # 2. AI生成
        log += "✅ AI正在写作...\n"
        content, _ = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            log += "❌ AI生成失败"
            return jsonify({"log": log})
        log += f"📝 AI生成内容：\n{content}\n\n"

        # 3. 发文
        log += "✅ 正在发布到币安广场...\n"
        ok, msg = post_content(content, api_key)
        if ok:
            log += f"🎉 {msg}"
        else:
            log += f"❌ {msg}"

        return jsonify({"log": log})
    except Exception as e:
        return jsonify({"log": f"❌ 异常：{str(e)}"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
