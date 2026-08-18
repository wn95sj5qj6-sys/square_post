import datetime
import json
import os
import threading
import time
import traceback
import urllib.parse
from flask import (
    Flask,
    Response,
    jsonify,
    make_response,
    render_template_string,
    request,
)
from schedule_core import (
    can_publish,
    get_daily_stats,
    get_random_interval,
    inc_auto_published,
    inc_manual_published,
    set_daily_stats,
)
from werkzeug.exceptions import HTTPException

app = Flask(__name__)

# ======================== 核心配置 ========================
ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEFAULT_AUTO_INTERVAL = int(os.getenv("AUTO_INTERVAL_MINUTES", "60"))
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_MAX_LIMIT", "8"))

DATA_DIR = "data"
DB_FILE = f"{DATA_DIR}/records.json"
CONFIG_FILE = f"{DATA_DIR}/config.json"
PROMPT_FILE = f"{DATA_DIR}/prompts.json"
GLOBAL_CONFIG_FILE = f"{DATA_DIR}/global_config.json"
os.makedirs(DATA_DIR, exist_ok=True)

account_running_status = {}
status_lock = threading.Lock()


def load_global_config():
  return load_json(GLOBAL_CONFIG_FILE, {"manual_verbose_mode": False})


def save_global_config(config):
  save_json(GLOBAL_CONFIG_FILE, config)


def get_manual_verbose_mode():
  cfg = load_global_config()
  return cfg.get("manual_verbose_mode", False)


def set_manual_verbose_mode(enabled):
  cfg = load_global_config()
  cfg["manual_verbose_mode"] = enabled
  save_global_config(cfg)


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


def recover_counts_from_records():
  today = str(datetime.date.today())
  db = load_json(DB_FILE, [])
  auto_counts = {}
  manual_counts = {}
  for record in db:
    if record.get("date") == today and record.get("status") == "success":
      acc = record.get("account")
      mode = record.get("mode")
      if not acc:
        continue
      if mode == "auto":
        auto_counts[acc] = auto_counts.get(acc, 0) + 1
      elif mode in ["manual", "article"]:
        manual_counts[acc] = manual_counts.get(acc, 0) + 1
  accounts = get_all_accounts()
  for acc in accounts:
    acc_name = acc["name"]
    auto_pub = auto_counts.get(acc_name, 0)
    manual_pub = manual_counts.get(acc_name, 0)
    set_daily_stats(
        acc_name, acc, auto_published=auto_pub, manual_published=manual_pub
    )
  print(
      f"[启动恢复] 已恢复今日发文计数: auto={auto_counts},"
      f" manual={manual_counts}"
  )


def get_accounts_from_env():
  accounts_env = os.getenv("BINANCE_ACCOUNTS", "").strip()
  accounts = []
  if not accounts_env:
    return accounts

  for item in accounts_env.split(","):
    item = item.strip()
    if "|" not in item:
      continue
    name, key = item.split("|", 1)
    name = name.strip()
    key = key.strip()
    if name and key:
      accounts.append({"name": name, "key": key})
  return accounts


def get_all_accounts():
  env_accounts = get_accounts_from_env()
  prompts = load_json(PROMPT_FILE)
  accounts = []
  for acc in env_accounts:
    acc_name = acc["name"]
    acc_config = prompts.get(acc_name, {})
    with status_lock:
      running = account_running_status.get(acc_name, False)

    accounts.append({
        "name": acc_name,
        "key": acc["key"],
        "prompt": acc_config.get("prompt", ""),
        "model_type": acc_config.get("model_type", "zhipu"),
        "daily_limit": acc_config.get("daily_limit", DEFAULT_DAILY_LIMIT),
        "auto_interval": acc_config.get(
            "auto_interval", DEFAULT_AUTO_INTERVAL
        ),
        "schedule": acc_config.get("schedule", {}),
        "running": running,
    })
  return accounts


def get_account_by_name(name):
  for acc in get_all_accounts():
    if acc["name"] == name:
      return acc
  return None


def get_account_by_key(key):
  for acc in get_all_accounts():
    if acc["key"] == key:
      return acc
  return None


def save_account_prompt(
    account_name,
    prompt,
    daily_limit,
    auto_interval,
    model_type="zhipu",
    schedule=None,
):
  prompts = load_json(PROMPT_FILE)
  data = {
      "prompt": prompt,
      "model_type": model_type,
      "daily_limit": int(daily_limit),
      "auto_interval": int(auto_interval),
  }
  if schedule is not None:
    data["schedule"] = schedule
  prompts[account_name] = data
  save_json(PROMPT_FILE, prompts)


def save_post_record(
    mode, account_name, symbol, content, post_id, status="success"
):
  record = {
      "mode": mode,
      "account": account_name,
      "date": str(datetime.date.today()),
      "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
      "symbol": symbol,
      "content": content,
      "post_id": post_id,
      "status": status,
  }
  db = load_json(DB_FILE, [])
  db.append(record)
  MAX_RECORDS = 1000
  if len(db) > MAX_RECORDS:
    db = db[-MAX_RECORDS:]
  save_json(DB_FILE, db)


def get_today_stats(account_name=None):
  accounts = get_all_accounts()
  stats = {}
  for acc in accounts:
    acc_name = acc["name"]
    auto_target, auto_pub, manual_pub = get_daily_stats(acc_name, acc)
    stats[acc_name] = {
        "auto_target": auto_target,
        "auto_count": auto_pub,
        "manual_count": manual_pub,
        "running": acc["running"],
    }
  if account_name:
    return stats.get(
        account_name,
        {
            "auto_target": 0,
            "auto_count": 0,
            "manual_count": 0,
            "running": False,
        },
    )
  return stats


def delete_records(account=None, date=None, all_records=False):
  db = load_json(DB_FILE, [])
  if all_records:
    new_db = []
  else:
    new_db = []
    for record in db:
      if account and record.get("account") == account:
        if date and record.get("date") == date:
          continue
        elif not date:
          continue
      elif date and record.get("date") == date and not account:
        continue
      new_db.append(record)
  save_json(DB_FILE, new_db)
  return len(db) - len(new_db)


def auto_publisher_worker(account_name):
  while True:
    with status_lock:
      if not account_running_status.get(account_name, False):
        break
    current_acc = get_account_by_name(account_name)
    if not current_acc or not can_publish(account_name, current_acc):
      time.sleep(30)
      continue

    try:
      from topic_main import run_topic

      topic = run_topic()
      if not topic:
        time.sleep(10)
        continue
      from ai_core import generate_content

      content, _ = generate_content(
          topic,
          api_key=(
              ZHIPU_API_KEY
              if current_acc["model_type"] == "zhipu"
              else DEEPSEEK_API_KEY
          ),
          model_type=current_acc["model_type"],
          custom_prompt=current_acc["prompt"],
      )
      if not content:
        time.sleep(10)
        continue
      from post_main import post_content

      ok, msg, post_id = post_content(content, current_acc["key"])
      post_id_str = str(post_id) if post_id else "未知ID"
      if ok:
        save_post_record(
            "auto",
            account_name,
            topic.get("symbol", ""),
            content,
            post_id_str,
        )
        cfg = load_json(CONFIG_FILE)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg[f"{account_name}_last_run"] = now_str
        cfg[f"{account_name}_last_auto_run"] = now_str
        save_json(CONFIG_FILE, cfg)
        inc_auto_published(account_name)

      schedule_cfg = current_acc.get("schedule", {})
      sleep_min = get_random_interval(
          schedule_cfg.get("interval_min", 8),
          schedule_cfg.get("interval_max", 25),
      )
      time.sleep(sleep_min * 60)
    except Exception as e:
      print("自动异常：", e)
      time.sleep(10)


def start_account_auto_publish(account_name):
  with status_lock:
    if account_running_status.get(account_name, False):
      return False
    account_running_status[account_name] = True
  t = threading.Thread(
      target=auto_publisher_worker, args=(account_name,), daemon=True
  )
  t.start()
  return True


def stop_account_auto_publish(account_name):
  with status_lock:
    account_running_status[account_name] = False
  return True


# ======================== 网页模板 ========================
UI_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安广场内容发布系统</title>
    <style>
        :root {
            --primary: #007aff;
            --success: #34c759;
            --danger: #ff3b30;
            --gray: #8e8e93;
            --light-gray: #f2f2f7;
            --border: #e5e5ea;
            --text: #1d1d1f;
            --bg: #ffffff;
        }
        * { margin:0; padding:0; box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
        body { background:var(--light-gray); color:var(--text); padding:16px; line-height:1.5; }
        .container { max-width:850px; margin:0 auto; }
        .card { background:var(--bg); border-radius:16px; box-shadow:0 2px 10px rgba(0,0,0,0.05); padding:24px; margin-bottom:16px; }
        .header { display:flex; align-items:center; margin-bottom:20px; }
        .header h1 { font-size:22px; font-weight:600; margin-right:12px; }
        .header .badge { background:var(--primary); color:white; font-size:12px; padding:2px 8px; border-radius:10px; }
        .tabs { display:flex; gap:8px; margin-bottom:20px; border-bottom:1px solid var(--border); padding-bottom:8px; overflow-x:auto; }
        .tab-btn { background:none; border:none; padding:8px 16px; font-size:15px; font-weight:500; color:var(--gray); border-radius:8px; cursor:pointer; transition:all 0.2s; white-space:nowrap; }
        .tab-btn.active { color:var(--primary); background:rgba(0,122,255,0.1); }
        .tab-content { display:none; }
        .tab-content.active { display:block; }
        .form-group { margin-bottom:16px; }
        .form-label { display:block; font-size:14px; font-weight:500; margin-bottom:8px; color:var(--text); }
        .form-control { width:100%; padding:12px 16px; border:1px solid var(--border); border-radius:12px; font-size:15px; transition:border 0.2s; }
        .form-control:focus { outline:none; border-color:var(--primary); }
        textarea.form-control { min-height:120px; resize:vertical; line-height:1.5; }
        .btn { display:inline-flex; align-items:center; justify-content:center; padding:12px 24px; border:none; border-radius:12px; font-size:15px; font-weight:500; cursor:pointer; transition:all 0.2s; gap:8px; }
        .btn-primary { background:var(--primary); color:white; }
        .btn-primary:hover { background:#0066cc; }
        .btn-success { background:var(--success); color:white; }
        .btn-danger { background:var(--danger); color:white; }
        .btn-secondary { background:var(--light-gray); color:var(--text); }
        .btn-secondary:hover { background:#e5e5ea; }
        .account-selector { width:100%; margin-bottom:16px; }
        .account-actions-wrapper { display:flex; gap:8px; margin-top:8px; }
        .account-action-btn { flex:1; padding:8px 12px; font-size:14px; }
        .stats-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:20px; }
        .stat-card { background:var(--light-gray); border-radius:12px; padding:16px; text-align:center; cursor:pointer; transition:all 0.2s; }
        .stat-card:hover { transform:scale(1.02); box-shadow:0 2px 8px rgba(0,0,0,0.1); }
        .stat-card.active { border:2px solid var(--primary); background:rgba(0,122,255,0.05); }
        .stat-value { font-size:24px; font-weight:600; margin-bottom:4px; }
        .stat-label { font-size:12px; color:var(--gray); }
        .config-detail { background:rgba(0,122,255,0.05); border-left:4px solid var(--primary); padding:16px; border-radius:0 12px 12px 0; margin-bottom:16px; display:none; }
        .config-detail.active { display:block; }
        .log-box { background:var(--light-gray); border-radius:12px; padding:16px; min-height:60px; font-size:14px; white-space:pre-wrap; margin-top:16px; }
        .records-list { max-height:400px; overflow-y:auto; gap:12px; display:flex; flex-direction:column; }
        .record-item { background:var(--light-gray); border-radius:12px; padding:16px; }
        .record-header { display:flex; justify-content:space-between; margin-bottom:8px; font-size:14px; }
        .record-symbol { font-weight:600; color:var(--primary); }
        .record-time { color:var(--gray); font-size:12px; }
        .record-content { font-size:14px; line-height:1.5; }
        .delete-section { margin-top:16px; padding-top:16px; border-top:1px solid var(--border); }
        .grid-row { display:grid; grid-template-columns:repeat(2,1fr); gap:12px; margin-bottom:12px; }
        .emoji-bar { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; padding:8px; background:var(--light-gray); border-radius:10px; align-items:center; }
        .emoji-btn { background:none; border:1px solid var(--border); border-radius:6px; padding:4px 8px; font-size:15px; cursor:pointer; background:white; }
        .emoji-btn:hover { background:#f2f2f7; transform:scale(1.08); }
        .symbol-btn { font-weight:bold; color:var(--primary); background:#eef6ff; border-color:#bcdbff; }
        @media(max-width:480px){ .card{padding:16px;} .account-actions-wrapper{flex-direction:column;} .grid-row{grid-template-columns:1fr;} }
    </style>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css">
</head>
<body>
    <div class="container">
        <div class="card">
            <div class="header">
                <h1>币安广场内容发布系统</h1>
                <span class="badge">v2.3</span>
            </div>
            <div class="tabs">
                <button class="tab-btn active" onclick="switchTab('auto')"><i class="fa fa-robot"></i> 自动模式</button>
                <button class="tab-btn" onclick="switchTab('manual')"><i class="fa fa-pencil-square-o"></i> 动态短帖</button>
                <button class="tab-btn" onclick="switchTab('article')"><i class="fa fa-newspaper-o"></i> 广场长文</button>
                <button class="tab-btn" onclick="switchTab('config')"><i class="fa fa-cog"></i> 账号配置</button>
                <button class="tab-btn" onclick="switchTab('records')"><i class="fa fa-history"></i> 发文记录</button>
            </div>

            <!-- 1. 自动模式 -->
            <div id="auto" class="tab-content active">
                <div class="form-label">选择要操作的账号</div>
                <select id="auto_account_selector" class="form-control account-selector" onchange="loadAccountStatus()">
                    <option value="">请选择账号</option>
                    {% for acc in accounts %}
                    <option value="{{acc.name}}">{{acc.name}}</option>
                    {% endfor %}
                </select>
                <div id="auto_account_actions" style="display: none;">
                    <div style="padding:16px;background:var(--light-gray);border-radius:12px;margin-bottom:16px;">
                        <div id="auto_account_name" style="font-weight:600;margin-bottom:8px;"></div>
                        <div id="auto_account_status"></div>
                    </div>
                    <div class="account-actions-wrapper">
                        <button id="auto_start_btn" class="btn btn-success account-action-btn" onclick="startAuto()"><i class="fa fa-play"></i> 启动自动发文</button>
                        <button id="auto_stop_btn" class="btn btn-danger account-action-btn" onclick="stopAuto()"><i class="fa fa-stop"></i> 停止自动发文</button>
                    </div>
                </div>
                <div class="form-label" style="margin-top:20px;">今日发文统计</div>
                <div class="stats-grid" id="today_stats">
                    {% for acc_name, stat in today_stats.items() %}
                    <div class="stat-card" id="stat_{{acc_name}}" onclick="showAccountConfig('{{acc_name}}')">
                        <div class="stat-value">{{ stat.auto_count + stat.manual_count }}</div>
                        <div class="stat-label">{{acc_name}}</div>
                        <div class="stat-label">自动: {{stat.auto_count}}/{{stat.auto_target}}</div>
                        <div class="stat-label">手动/长文: {{stat.manual_count}}</div>
                        {% if stat.running %}
                        <div class="stat-label" style="color:var(--success);">运行中</div>
                        {% else %}
                        <div class="stat-label" style="color:var(--gray);">已停止</div>
                        {% endif %}
                    </div>
                    {% endfor %}
                </div>
                <div class="config-detail" id="account_config_detail">
                    <div id="config_detail_content"></div>
                </div>
            </div>

            <!-- 2. 短动态模式 -->
            <div id="manual" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择发文账号</label>
                    <select id="manual_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">交易对</label>
                    <input type="text" id="manual_symbol" class="form-control" placeholder="如 BTCUSDT">
                </div>
                <div style="display:flex;gap:8px;margin-bottom:16px; flex-wrap: wrap; align-items: center;">
                    <button class="btn btn-secondary" onclick="autoSelectSymbol()">自动选交易对</button>
                    <button class="btn btn-secondary" onclick="generateFullTopic()">生成分析报告</button>
                </div>
                <div class="form-group">
                    <label class="form-label">话题分析</label>
                    <textarea id="manual_topic" class="form-control"></textarea>
                </div>
                <div style="margin-bottom:16px;">
                    <button class="btn btn-secondary" onclick="generateAIContent()" style="width:100%;">生成发文内容</button>
                </div>
                <div class="form-group">
                    <label class="form-label">短动态正文</label>
                    <textarea id="manual_content" class="form-control"></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitPost()" style="width:100%">确认发布短动态</button>
                <div class="log-box" id="manual_log">等待操作...</div>
            </div>

            <!-- 3. 独立文章发布模式（方案A 文本排版增强版） -->
            <div id="article" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择发布账号</label>
                    <select id="article_account" class="form-control">
                        {% for acc in accounts %}
                        <option value="{{acc.key}}" data-name="{{acc.name}}">{{acc.name}} (今日已发:{{today_stats[acc.name].manual_count}})</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">文章标题</label>
                    <input type="text" id="article_title" class="form-control" placeholder="请输入引人注目的文章标题...">
                </div>
                <div class="form-group">
                    <label class="form-label">快捷符号与常用表情 (点击直接插入正文光标处)</label>
                    <div class="emoji-bar">
                        <!-- 快捷功能符号 -->
                        <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToArticle('#')"># 话题</button>
                        <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToArticle('$')">$ 标的</button>
                        <button type="button" class="emoji-btn symbol-btn" onclick="insertSymbolToArticle('@')">@ 用户</button>
                        <span style="border-left:1px solid var(--border);height:20px;margin:0 4px;"></span>
                        <!-- 常用金融/情绪 Emoji -->
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('🔥')">🔥</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('🚀')">🚀</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('📈')">📈</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('📉')">📉</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('⚠️')">⚠️</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('💰')">💰</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('📊')">📊</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('💎')">💎</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('🐂')">🐂</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('🐻')">🐻</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('🚨')">🚨</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('🎯')">🎯</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('👀')">👀</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('👇')">👇</button>
                        <button type="button" class="emoji-btn" onclick="insertSymbolToArticle('💡')">💡</button>
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label">文章正文（支持多段落、换行、#话题、$币种 与 Emoji）</label>
                    <textarea id="article_content" class="form-control" style="min-height:240px;" placeholder="在此输入深度行情剖析、宏观观点或策略长文..."></textarea>
                </div>
                <button class="btn btn-primary" onclick="submitArticlePost()" style="width:100%" id="article_submit_btn">
                    <i class="fa fa-paper-plane"></i> 确认发布长文至币安广场
                </button>
                <div class="log-box" id="article_log">就绪，等待输入...</div>
            </div>

            <!-- 4. 账号配置 -->
            <div id="config" class="tab-content">
                <div class="form-group">
                    <label class="form-label">选择要配置的账号</label>
                    <select id="config_account" class="form-control" onchange="loadAccountConfig()">
                        {% for acc in accounts %}
                        <option value="{{acc.name}}">{{acc.name}}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label class="form-label">专属提示词</label>
                    <textarea id="config_prompt" class="form-control"></textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">生成模型</label>
                    <select id="config_model" class="form-control">
                        <option value="zhipu">智谱 GLM-4-Flash</option>
                        <option value="deepseek">DeepSeek V4-Flash</option>
                    </select>
                </div>
                <div class="form-group" style="margin-top:20px;">
                    <label class="form-label">📅 发文计划设置</label>
                </div>
                <div class="grid-row">
                    <div>
                        <label class="form-label">每日最小发文数</label>
                        <input type="number" id="cfg_schedule_daily_min" class="form-control" min="1" value="10">
                    </div>
                    <div>
                        <label class="form-label">每日最大发文数</label>
                        <input type="number" id="cfg_schedule_daily_max" class="form-control" min="1" value="20">
                    </div>
                </div>
                <div class="grid-row">
                    <div>
                        <label class="form-label">最小间隔(分钟)</label>
                        <input type="number" id="cfg_schedule_interval_min" class="form-control" min="2" value="60">
                    </div>
                    <div>
                        <label class="form-label">最大间隔(分钟)</label>
                        <input type="number" id="cfg_schedule_interval_max" class="form-control" min="5" value="90">
                    </div>
                </div>
                <div class="grid-row">
                    <div>
                        <label class="form-label">活跃开始时间</label>
                        <input type="time" class="form-control" id="cfg_schedule_active_start" value="07:00">
                    </div>
                    <div>
                        <label class="form-label">活跃结束时间</label>
                        <input type="time" class="form-control" id="cfg_schedule_active_end" value="23:59">
                    </div>
                </div>
                <button class="btn btn-primary" onclick="saveAccountConfig()" style="width:100%;margin-top:12px;">保存配置</button>
                <div class="log-box" id="config_log">选择账号后加载配置...</div>
            </div>

            <!-- 5. 发文记录 -->
            <div id="records" class="tab-content">
                <div class="form-group">
                    <label class="form-label">筛选条件</label>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <select id="record_account" class="form-control" style="flex:1;min-width:120px;">
                            <option value="">所有账号</option>
                            {% for acc in accounts %}
                            <option value="{{acc.name}}">{{acc.name}}</option>
                            {% endfor %}
                        </select>
                        <input type="date" id="record_date" class="form-control" value="{{today}}">
                        <button class="btn btn-secondary" onclick="loadRecords()">查询</button>
                        <button class="btn btn-secondary" onclick="exportRecords()">导出</button>
                    </div>
                </div>
                <div class="records-list" id="records_list"></div>
                <div class="delete-section">
                    <div class="form-label">删除记录</div>
                    <div style="display:flex;gap:8px;flex-wrap:wrap;">
                        <select id="delete_account" class="form-control" style="flex:1;min-width:120px;">
                            <option value="">所有账号</option>
                            {% for acc in accounts %}
                            <option value="{{acc.name}}">{{acc.name}}</option>
                            {% endfor %}
                        </select>
                        <input type="date" id="delete_date" class="form-control">
                        <button class="btn btn-danger" onclick="deleteSelectedRecords()">删除选中记录</button>
                        <button class="btn btn-danger" onclick="deleteAllRecords()">删除所有记录</button>
                    </div>
                    <div class="log-box" id="delete_log"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            const activeBtn = document.querySelector(`.tab-btn[onclick="switchTab('${tabId}')"]`);
            if(activeBtn) activeBtn.classList.add('active');
            const targetContent = document.getElementById(tabId);
            if(targetContent) targetContent.classList.add('active');
            if(tabId === 'auto') refreshAutoPage();
            if(tabId === 'config') loadAccountConfig();
        }
        
        function loadAccountStatus() {
            const acc = document.getElementById('auto_account_selector').value;
            if(!acc) {
                document.getElementById('auto_account_actions').style.display = 'none';
                return;
            }
            fetch(`/api/auto/status?account=${acc}`)
                .then(r => r.json())
                .then(d => {
                    document.getElementById('auto_account_actions').style.display = 'block';
                    document.getElementById('auto_account_name').textContent = acc;
                    fetch(`/api/stats/today?account=${acc}`)
                        .then(r => r.json())
                        .then(s => {
                            const st = d.running ? `<span style="color:var(--success);">运行中</span>` : `<span style="color:var(--gray);">已停止</span>`;
                            const total = (s.auto_count||0) + (s.manual_count||0);
                            document.getElementById('auto_account_status').innerHTML = `${st} | 今日总发文:${total} (自动:${s.auto_count}/${s.auto_target} 手动/长文:${s.manual_count})`;
                            document.getElementById('auto_start_btn').disabled = d.running;
                            document.getElementById('auto_stop_btn').disabled = !d.running;
                        });
                });
        }
        
        function startAuto() {
            const acc = document.getElementById('auto_account_selector').value;
            fetch(`/api/auto/start?account=${acc}`).then(r => r.json()).then(d => {
                alert(d.msg);
                refreshAutoPage();
            });
        }
        
        function stopAuto() {
            const acc = document.getElementById('auto_account_selector').value;
            fetch(`/api/auto/stop?account=${acc}`).then(r => r.json()).then(d => {
                alert(d.msg);
                refreshAutoPage();
            });
        }
        
        function showAccountConfig(acc) {
            document.querySelectorAll('.stat-card').forEach(c => c.classList.remove('active'));
            const card = document.getElementById('stat_'+acc);
            if(card) card.classList.add('active');
            fetch(`/api/config/load?account=${acc}`).then(r => r.json()).then(c => {
                let h = `<div><strong>提示词：</strong>${c.prompt||'无'}</div><div><strong>模型：</strong>${c.model_type}</div>`;
                document.getElementById('config_detail_content').innerHTML = h;
                document.getElementById('account_config_detail').classList.add('active');
            });
        }
        
        function refreshAutoPage() {
            fetch('/api/auto/refresh').then(r => r.json()).then(d => {
                let h = '';
                for(const acc of d.accounts) {
                    const s = d.today_stats[acc.name];
                    if(!s) continue;
                    const total = (s.auto_count||0) + (s.manual_count||0);
                    h += `<div class="stat-card" onclick="showAccountConfig('${acc.name}')">
                        <div class="stat-value">${total}</div>
                        <div class="stat-label">${acc.name}</div>
                        <div class="stat-label">自动: ${s.auto_count}/${s.auto_target}</div>
                        <div class="stat-label">手动/长文: ${s.manual_count}</div>
                        ${s.running?'<div class="stat-label" style="color:var(--success);">运行中</div>':'<div class="stat-label" style="color:var(--gray);">已停止</div>'}
                    </div>`;
                }
                document.getElementById('today_stats').innerHTML = h;
                updateSelectOptions('manual_account', d.today_stats);
                updateSelectOptions('article_account', d.today_stats);
            });
        }

        function updateSelectOptions(elementId, stats) {
            const selectEl = document.getElementById(elementId);
            if(!selectEl) return;
            for(let i=0; i<selectEl.options.length; i++) {
                const opt = selectEl.options[i];
                const name = opt.getAttribute('data-name');
                if(name && stats[name]) {
                    opt.text = `${name} (今日已发:${stats[name].manual_count})`;
                }
            }
        }
        
        function loadAccountConfig() {
            const a = document.getElementById('config_account').value;
            fetch(`/api/config/load?account=${a}`).then(r => r.json()).then(c => {
                document.getElementById('config_prompt').value = c.prompt || '';
                document.getElementById('config_model').value = c.model_type || 'zhipu';
                const s = c.schedule || {};
                document.getElementById('cfg_schedule_daily_min').value = s.daily_min || 10;
                document.getElementById('cfg_schedule_daily_max').value = s.daily_max || 20;
                document.getElementById('cfg_schedule_interval_min').value = s.interval_min || 8;
                document.getElementById('cfg_schedule_interval_max').value = s.interval_max || 25;
                document.getElementById('cfg_schedule_active_start').value = s.active_start || '08:00';
                document.getElementById('cfg_schedule_active_end').value = s.active_end || '22:00';
                document.getElementById('config_log').textContent = '已加载';
            });
        }
        
        function saveAccountConfig() {
            const a = document.getElementById('config_account').value;
            const p = document.getElementById('config_prompt').value;
            const m = document.getElementById('config_model').value;
            const schedule = {
                daily_min: parseInt(document.getElementById('cfg_schedule_daily_min').value),
                daily_max: parseInt(document.getElementById('cfg_schedule_daily_max').value),
                interval_min: parseInt(document.getElementById('cfg_schedule_interval_min').value),
                interval_max: parseInt(document.getElementById('cfg_schedule_interval_max').value),
                active_start: document.getElementById('cfg_schedule_active_start').value,
                active_end: document.getElementById('cfg_schedule_active_end').value
            };
            fetch('/api/config/save', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({ account:a, prompt:p, model_type:m, daily_limit:8, auto_interval:60, schedule:schedule })
            }).then(r => r.json()).then(d => {
                document.getElementById('config_log').textContent = '✅ 保存成功';
                refreshAutoPage();
            });
        }
        
        function autoSelectSymbol() {
            fetch('/api/manual/auto_symbol').then(r => r.json()).then(d => {
                document.getElementById('manual_symbol').value = d.symbol;
            });
        }
        
        function generateFullTopic() {
            const s = document.getElementById('manual_symbol').value;
            if(!s) { alert('请先输入或自动选择交易对'); return; }
            fetch(`/api/manual/full_topic?symbol=${s}`).then(r => r.json()).then(d => {
                document.getElementById('manual_topic').value = d.topic;
            });
        }
        
        function generateAIContent() {
            const t = document.getElementById('manual_topic').value;
            const k = document.getElementById('manual_account').value;
            fetch('/api/manual/generate_ai', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({topic:t, account_key:k})
            }).then(r => r.text()).then(c => {
                document.getElementById('manual_content').value = c;
            });
        }
        
        function submitPost() {
            const k = document.getElementById('manual_account').value;
            const c = document.getElementById('manual_content').value;
            const s = document.getElementById('manual_symbol').value;
            if(!c.trim()) { alert('发文内容不能为空'); return; }
            fetch('/api/manual/post', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({account_key:k, content:c, symbol:s})
            }).then(r => r.json()).then(d => {
                document.getElementById('manual_log').textContent = d.success ? '✅ 发文成功' : '❌ 发文失败: '+d.msg;
                refreshAutoPage();
            });
        }

        // ================= 文章专属 JS 逻辑（纯文本排版与符号插入） =================
        function insertSymbolToArticle(symbol) {
            const textarea = document.getElementById('article_content');
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            const text = textarea.value;
            textarea.value = text.substring(0, start) + symbol + text.substring(end);
            textarea.focus();
            textarea.selectionStart = textarea.selectionEnd = start + symbol.length;
        }

        function submitArticlePost() {
            const k = document.getElementById('article_account').value;
            const title = document.getElementById('article_title').value.trim();
            const content = document.getElementById('article_content').value.trim();
            const logBox = document.getElementById('article_log');
            const submitBtn = document.getElementById('article_submit_btn');

            if (!title) { alert('文章标题不能为空！'); return; }
            if (!content) { alert('文章正文不能为空！'); return; }

            submitBtn.disabled = true;
            logBox.textContent = '⏳ 正在排版并向币安广场发布长文...';

            fetch('/api/article/post', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    account_key: k,
                    title: title,
                    content: content
                })
            }).then(r => r.json()).then(d => {
                submitBtn.disabled = false;
                if (d.success) {
                    logBox.textContent = `✅ 文章发布成功！ID: ${d.post_id}`;
                    document.getElementById('article_title').value = '';
                    document.getElementById('article_content').value = '';
                    refreshAutoPage();
                    loadRecords();
                } else {
                    logBox.textContent = `❌ 发布失败: ${d.msg}`;
                }
            }).catch(err => {
                submitBtn.disabled = false;
                logBox.textContent = `❌ 请求异常: ${err}`;
            });
        }
        
        function loadRecords() {
            const a = document.getElementById('record_account').value;
            const d = document.getElementById('record_date').value;
            fetch(`/api/records?account=${a}&date=${d}`).then(r => r.json()).then(rs => {
                let h = '';
                rs.forEach(r => {
                    h += `<div class="record-item"><div class="record-header"><span class="record-symbol">[${r.mode.toUpperCase()}] ${r.symbol}</span><span>${r.account}</span><span class="record-time">${r.time}</span></div><div class="record-content">${r.content}</div></div>`;
                });
                document.getElementById('records_list').innerHTML = h;
            });
        }
        
        function exportRecords() {
            const a = document.getElementById('record_account').value;
            const d = document.getElementById('record_date').value;
            window.open(`/api/records/export?account=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`);
        }
        
        function deleteSelectedRecords() {
            const a = document.getElementById('delete_account').value;
            const d = document.getElementById('delete_date').value;
            fetch(`/api/records/delete?account=${encodeURIComponent(a)}&date=${encodeURIComponent(d)}`, {method:'POST'}).then(r => r.json()).then(d => {
                document.getElementById('delete_log').textContent = '已删除'+d.deleted_count+'条';
                loadRecords();
            });
        }
        
        function deleteAllRecords() {
            fetch('/api/records/delete?all=true', {method:'POST'}).then(r => r.json()).then(d => {
                document.getElementById('delete_log').textContent = '已删除全部';
                loadRecords();
            });
        }
        
        window.onload = function() {
            refreshAutoPage();
            loadRecords();
        };
    </script>
</body>
</html>
"""


# ======================== 路由接口 ========================
@app.route("/")
def index():
  accounts = get_all_accounts()
  today_stats = get_today_stats()
  today = str(datetime.date.today())
  return render_template_string(
      UI_TEMPLATE, accounts=accounts, today_stats=today_stats, today=today
  )


@app.route("/api/auto/start")
def auto_start():
  a = request.args.get("account")
  ok = start_account_auto_publish(a)
  return jsonify({"success": ok, "msg": "已启动" if ok else "已运行"})


@app.route("/api/auto/stop")
def auto_stop():
  a = request.args.get("account")
  stop_account_auto_publish(a)
  return jsonify({"success": True, "msg": "已停止"})


@app.route("/api/auto/status")
def auto_status():
  a = request.args.get("account")
  acc = get_account_by_name(a) or {}
  return jsonify({
      "running": account_running_status.get(a, False),
      "daily_limit": acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
      "auto_interval": acc.get("auto_interval", DEFAULT_AUTO_INTERVAL),
  })


@app.route("/api/auto/refresh")
def auto_refresh():
  return jsonify(
      {"accounts": get_all_accounts(), "today_stats": get_today_stats()}
  )


@app.route("/api/config/load")
def config_load():
  a = request.args.get("account")
  acc = get_account_by_name(a) or {}
  return jsonify({
      "prompt": acc.get("prompt", ""),
      "model_type": acc.get("model_type", "zhipu"),
      "daily_limit": acc.get("daily_limit", DEFAULT_DAILY_LIMIT),
      "auto_interval": acc.get("auto_interval", DEFAULT_AUTO_INTERVAL),
      "schedule": acc.get("schedule", {}),
  })


@app.route("/api/config/save", methods=["POST"])
def config_save():
  d = request.json
  save_account_prompt(
      d["account"],
      d["prompt"],
      d["daily_limit"],
      d["auto_interval"],
      d["model_type"],
      d.get("schedule"),
  )
  return jsonify({"success": True})


@app.route("/api/manual/auto_symbol")
def manual_auto_symbol():
  try:
    from topic_main import run_topic

    topic = run_topic()
    symbol = topic.get("symbol", "BTCUSDT")
    return jsonify({"success": True, "symbol": symbol})
  except:
    return jsonify({"success": True, "symbol": "BTCUSDT"})


@app.route("/api/manual/full_topic")
def manual_full_topic():
  symbol = request.args.get("symbol", "").strip()
  from topic_main import run_topic

  topic = run_topic(target_symbol=symbol, verbose=True)
  return jsonify({"success": True, "topic": topic.get("text", "")})


@app.route("/api/manual/generate_ai", methods=["POST"])
def manual_generate_ai():
  d = request.json
  t = d["topic"]
  k = d["account_key"]
  acc = get_account_by_key(k)
  from ai_core import generate_content

  api_key = (
      ZHIPU_API_KEY if acc.get("model_type") == "zhipu" else DEEPSEEK_API_KEY
  )
  c, _ = generate_content(
      {"text": t},
      api_key=api_key,
      model_type=acc.get("model_type", "zhipu"),
      custom_prompt=acc.get("prompt", ""),
  )
  return c or ""


@app.route("/api/manual/post", methods=["POST"])
def manual_post():
  d = request.json
  k = d["account_key"]
  c = d["content"]
  s = d["symbol"]
  acc = get_account_by_key(k)
  from post_main import post_content

  ok, msg, pid = post_content(c, k)
  pid = str(pid) if pid else "未知"
  if ok:
    save_post_record("manual", acc["name"], s, c, pid)
    inc_manual_published(acc["name"])
    cfg = load_json(CONFIG_FILE)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cfg[f"{acc['name']}_last_run"] = now_str
    cfg[f"{acc['name']}_last_manual_run"] = now_str
    save_json(CONFIG_FILE, cfg)
  return jsonify({"success": ok, "post_id": pid, "msg": msg})


# ======================== 方案 A：长文发布接口（JSON 直传） ========================
@app.route("/api/article/post", methods=["POST"])
def article_post():
  d = request.json or {}
  account_key = d.get("account_key", "").strip()
  title = d.get("title", "").strip()
  content = d.get("content", "").strip()

  if not account_key:
    return jsonify({"success": False, "msg": "请选择发布账号"})
  if not title:
    return jsonify({"success": False, "msg": "文章标题不能为空"})
  if not content:
    return jsonify({"success": False, "msg": "文章正文不能为空"})

  acc = get_account_by_key(account_key)
  if not acc:
    return jsonify({"success": False, "msg": "指定账号不存在"})

  from post_main import post_article

  ok, msg, pid = post_article(title, content, account_key)
  pid = str(pid) if pid else "未知"
  if ok:
    save_post_record("article", acc["name"], title[:20], content, pid)
    inc_manual_published(acc["name"])
    cfg = load_json(CONFIG_FILE)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cfg[f"{acc['name']}_last_run"] = now_str
    cfg[f"{acc['name']}_last_article_run"] = now_str
    save_json(CONFIG_FILE, cfg)

  return jsonify({"success": ok, "post_id": pid, "msg": msg})


@app.route("/api/records")
def records():
  a = request.args.get("account")
  d = request.args.get("date")
  db = load_json(DB_FILE, [])
  res = []
  for r in db:
    if a and r["account"] != a:
      continue
    if d and r["date"] != d:
      continue
    res.append(r)
  return jsonify(res)


@app.route("/api/records/export")
def records_export():
  a = request.args.get("account")
  d = request.args.get("date")
  db = load_json(DB_FILE, [])
  res = []
  for r in db:
    if a and r["account"] != a:
      continue
    if d and r["date"] != d:
      continue
    res.append(r)

  def csv_escape(s):
    return s.replace('"', '""') if isinstance(s, str) else s

  csv = "模式,账号,日期,时间,标题/币种,ID,状态,内容\n"
  for r in res:
    csv += (
        f"{csv_escape(r['mode'])},{csv_escape(r['account'])},{csv_escape(r['date'])},{csv_escape(r['time'])},{csv_escape(r['symbol'])},{csv_escape(r['post_id'])},{csv_escape(r['status'])},\"{csv_escape(r['content'])}\"\n"
    )
  response = make_response(csv)
  response.headers["Content-Type"] = "text/csv;charset=utf-8"
  response.headers["Content-Disposition"] = "attachment;filename=records.csv"
  return response


@app.route("/api/records/delete", methods=["POST"])
def records_delete():
  a = request.args.get("account")
  d = request.args.get("date")
  all_records = request.args.get("all") == "true"
  cnt = delete_records(a, d, all_records)
  return jsonify({"success": True, "deleted_count": cnt})


# ======================== 全局异常拦截处理 ========================
@app.errorhandler(Exception)
def handle_global_exception(e):
  # 正常放行 404 等标准 HTTP 异常
  if isinstance(e, HTTPException):
    return e
  print("❌ [服务端未捕获异常]:", e)
  traceback.print_exc()
  return jsonify({"success": False, "msg": f"服务端发生异常: {str(e)}"}), 500


if __name__ == "__main__":
  recover_counts_from_records()
  app.run(host="0.0.0.0", port=5000, debug=False)
