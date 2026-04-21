from flask import Flask, render_template_string, request, jsonify
import os
import logging

# 关闭多余日志，防止 Railway 限流
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__)

# 读取环境变量
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
BINANCE_KEYS = os.getenv("BINANCE_ACCOUNTS", "").strip().split(",")
BINANCE_KEYS = [k.strip() for k in BINANCE_KEYS if k]

# 导入你的业务逻辑
from topic_main import run_topic
from ai_core import generate_content
from post_main import post_with_key

# 网页面板
HTML = """
<!DOCTYPE html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>币安发文</title>
<style>
  body{background:#121212;color:#fff;font-family:Arial;padding:20px}
  .card{max-width:400px;margin:0 auto;background:#1e1e1e;padding:24px;border-radius:12px}
  h2{color:#00ccff;text-align:center}
  select,button{width:100%;padding:12px;margin:8px 0;border-radius:8px;border:none;background:#2a2a2a;color:#fff}
  button{background:#00ccff;color:#000;font-weight:700;cursor:pointer}
  #log{background:#111;padding:12px;border-radius:8px;margin-top:10px;min-height:100px;font-size:13px}
</style>
<div class=card>
  <h2>📤 币安广场一键发文</h2>
  <select id=acc>
    {% for k in keys %}
    <option value="{{k}}">账号 {{loop.index}}</option>
    {% endfor %}
  </select>
  <button onclick=run()>🚀 开始发文</button>
  <div id=log>等待操作...</div>
</div>
<script>
  function run(){
    document.getElementById('log').innerText = '执行中...'
    fetch('/publish',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:document.getElementById('acc').value})
    }).then(r=>r.json()).then(d=>{
      document.getElementById('log').innerText = d.msg
    })
  }
</script>
"""

@app.route('/')
def home():
    return render_template_string(HTML, keys=BINANCE_KEYS)

@app.route('/publish', methods=['POST'])
def pub():
    try:
        key = request.json.get('key', '').strip()
        if not key or not ZHIPU_API_KEY:
            return jsonify({"msg":"❌ 配置缺失"})
        
        topic = run_topic()
        content, _ = generate_content(topic, ZHIPU_API_KEY)
        if not content:
            return jsonify({"msg":"❌ AI生成失败"})
        
        ok = post_with_key(content, key)
        return jsonify({"msg":"🎉 发文成功！" if ok else "❌ 发文失败"})
    except Exception as e:
        return jsonify({"msg":f"❌ 错误：{str(e)}"})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
