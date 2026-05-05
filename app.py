from flask import Flask, request, jsonify, make_response
import json
import os
from datetime import datetime
import urllib.parse
import zipfile
import io

app = Flask(__name__)

# ======================== 配置项 ========================
# 本地存储路径（服务端）
RECORD_FILE = "data/records.json"
# 确保存储目录存在
os.makedirs(os.path.dirname(RECORD_FILE), exist_ok=True)
# 最大记录数（防止文件过大）
MAX_RECORDS = 2000

# ======================== 内嵌前端HTML代码 ========================
HTML_CONTENT = '''
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>币安发文记录助手</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: -apple-system, sans-serif; }
        body { padding: 20px; max-width: 1000px; margin: 0 auto; background: #f5f5f7; }
        .card { background: #fff; border-radius: 16px; padding: 24px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        .card-title { font-size: 18px; font-weight: 600; margin-bottom: 20px; }
        .form-group { margin-bottom: 16px; }
        .form-label { display: block; font-weight: 500; margin-bottom: 8px; font-size: 14px; }
        .form-control { width: 100%; padding: 12px 16px; border: 1px solid #e5e5ea; border-radius: 12px; font-size: 14px; }
        .form-control:focus { outline: none; border-color: #007aff; box-shadow: 0 0 0 2px rgba(0,122,255,0.1); }
        textarea.form-control { min-height: 100px; resize: vertical; }
        .btn { padding: 12px 20px; border: none; border-radius: 12px; font-size: 14px; font-weight: 500; cursor: pointer; margin-right: 8px; margin-bottom: 8px; }
        .btn-primary { background: #007aff; color: #fff; }
        .btn-success { background: #34c759; color: #fff; }
        .btn-danger { background: #ff3b30; color: #fff; }
        .btn-secondary { background: #f2f2f7; color: #1d1d1f; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin: 16px 0; }
        .stat-card { background: #f2f2f7; padding: 16px; border-radius: 12px; text-align: center; }
        .stat-value { font-size: 24px; font-weight: 600; margin-bottom: 4px; }
        .records-list { max-height: 400px; overflow-y: auto; margin-top: 16px; border: 1px solid #e5e5ea; border-radius: 12px; }
        .record-item { padding: 16px; border-bottom: 1px solid #e5e5ea; }
        .record-item:last-child { border-bottom: none; }
        .record-header { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 12px; color: #8e8e93; }
        .filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
        .filter-bar .form-control { flex: 1; min-width: 120px; }
    </style>
</head>
<body>
    <div class="card">
        <div class="card-title">📝 添加发文记录</div>
        <div class="form-group">
            <label class="form-label">选择账号</label>
            <select id="accountSelect" class="form-control">
                <option value="SJX">SJX</option>
                <option value="QCC">QCC</option>
                <option value="NEW">自定义账号</option>
            </select>
        </div>
        <div class="form-group">
            <label class="form-label">交易对</label>
            <input type="text" id="symbolInput" class="form-control" placeholder="如 BSBUSDT">
        </div>
        <div class="form-group">
            <label class="form-label">发文类型</label>
            <select id="modeSelect" class="form-control">
                <option value="manual">手动发文</option>
                <option value="auto">自动发文</option>
            </select>
        </div>
        <div class="form-group">
            <label class="form-label">发文内容</label>
            <textarea id="contentInput" class="form-control" placeholder="输入发文内容..."></textarea>
        </div>
        <button class="btn btn-primary" onclick="addRecord()">✅ 保存记录</button>
        <button class="btn btn-secondary" onclick="clearForm()">🗑️ 清空表单</button>
    </div>

    <div class="card">
        <div class="card-title">📊 今日统计（本地缓存）</div>
        <div class="stats-grid" id="statsContainer">
            <div class="stat-card"><div class="stat-value">0</div><div>今日总计</div></div>
            <div class="stat-card"><div class="stat-value">0</div><div>手动发文</div></div>
            <div class="stat-card"><div class="stat-value">0</div><div>自动发文</div></div>
        </div>
    </div>

    <div class="card">
        <div class="card-title">📜 发文记录列表</div>
        <div class="filter-bar">
            <select id="filterAccount" class="form-control">
                <option value="">所有账号</option>
                <option value="SJX">SJX</option>
                <option value="QCC">QCC</option>
                <option value="NEW">NEW</option>
            </select>
            <input type="date" id="filterDate" class="form-control">
            <select id="filterMode" class="form-control">
                <option value="">所有类型</option>
                <option value="manual">手动发文</option>
                <option value="auto">自动发文</option>
            </select>
            <button class="btn btn-secondary" onclick="loadRecords()">🔍 刷新/筛选</button>
            <button class="btn btn-success" onclick="exportLocalCSV()">📥 本地导出CSV</button>
            <button class="btn btn-secondary" onclick="exportServerCSV()">☁️ 服务端导出CSV</button>
        </div>
        <div class="records-list" id="recordsContainer">
            <div style="text-align: center; padding: 20px; color: #8e8e93;">暂无记录，请先添加</div>
        </div>
    </div>

    <script>
        // 初始化日期
        document.getElementById('filterDate').value = new Date().toISOString().split('T')[0];
        
        // ======================== 本地缓存工具 ========================
        function getLocalCache(key) {
            return JSON.parse(localStorage.getItem(`binance_${key}`) || '[]');
        }
        
        function setLocalCache(key, data) {
            localStorage.setItem(`binance_${key}`, JSON.stringify(data));
        }
        
        // ======================== 接口请求 ========================
        async function addRecord() {
            const account = document.getElementById('accountSelect').value;
            const symbol = document.getElementById('symbolInput').value.trim() || '未知';
            const mode = document.getElementById('modeSelect').value;
            const content = document.getElementById('contentInput').value.trim();

            if (!content) {
                alert('请输入发文内容！');
                return;
            }

            try {
                const res = await fetch('/api/records/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ account, symbol, mode, content })
                });
                const data = await res.json();
                
                if (data.code === 200) {
                    alert('记录添加成功！');
                    clearForm();
                    loadRecords(); // 刷新列表
                    loadStats(); // 刷新统计
                } else {
                    alert(`添加失败：${data.msg}`);
                }
            } catch (err) {
                alert('网络错误，请检查服务是否启动！');
                console.error(err);
            }
        }
        
        async function loadRecords() {
            const account = document.getElementById('filterAccount').value;
            const date = document.getElementById('filterDate').value;
            const mode = document.getElementById('filterMode').value;

            try {
                const params = new URLSearchParams();
                if (account) params.append('account', account);
                if (date) params.append('date', date);
                if (mode) params.append('mode', mode);

                const res = await fetch(`/api/records/list?${params.toString()}`);
                const data = await res.json();
                
                if (data.code === 200) {
                    // 缓存到本地
                    setLocalCache('records', data.data);
                    renderRecords(data.data);
                }
            } catch (err) {
                // 离线时使用本地缓存
                const cached = getLocalCache('records');
                renderRecords(cached);
                alert('使用本地缓存数据（网络异常）');
            }
        }
        
        async function loadStats() {
            try {
                const res = await fetch('/api/records/stats');
                const data = await res.json();
                
                if (data.code === 200) {
                    renderStats(data.data);
                }
            } catch (err) {
                // 离线时本地统计
                const records = getLocalCache('records');
                const today = new Date().toISOString().split('T')[0];
                const stats = { total: 0, manual: 0, auto: 0, accounts: {} };
                
                records.forEach(r => {
                    if (r.date === today) {
                        stats.total++;
                        stats[r.mode]++;
                        stats.accounts[r.account] = stats.accounts[r.account] || { total: 0, manual: 0, auto: 0 };
                        stats.accounts[r.account].total++;
                        stats.accounts[r.account][r.mode]++;
                    }
                });
                renderStats(stats);
            }
        }
        
        // ======================== 渲染函数 ========================
        function renderStats(stats) {
            let html = `
                <div class="stat-card"><div class="stat-value">${stats.total}</div><div>今日总计</div></div>
                <div class="stat-card"><div class="stat-value">${stats.manual}</div><div>手动发文</div></div>
                <div class="stat-card"><div class="stat-value">${stats.auto}</div><div>自动发文</div></div>
            `;
            
            // 账号统计
            Object.keys(stats.accounts).forEach(acc => {
                html += `
                    <div class="stat-card">
                        <div class="stat-value">${stats.accounts[acc].total}</div>
                        <div>${acc}（手动: ${stats.accounts[acc].manual} | 自动: ${stats.accounts[acc].auto}）</div>
                    </div>
                `;
            });
            
            document.getElementById('statsContainer').innerHTML = html;
        }
        
        function renderRecords(records) {
            const container = document.getElementById('recordsContainer');
            
            if (!records || records.length === 0) {
                container.innerHTML = '<div style="text-align: center; padding: 20px; color: #8e8e93;">暂无记录</div>';
                return;
            }
            
            let html = '';
            records.reverse().forEach(record => {
                html += `
                    <div class="record-item">
                        <div class="record-header">
                            <span>账号：${record.account} | 类型：${record.mode === 'manual' ? '手动' : '自动'}</span>
                            <span>${record.date} ${record.create_time?.split(' ')[1] || ''}</span>
                        </div>
                        <div style="font-weight: 500; margin-bottom: 4px;">${record.symbol}</div>
                        <div style="font-size: 14px; line-height: 1.4;">${record.content}</div>
                    </div>
                `;
            });
            
            container.innerHTML = html;
        }
        
        // ======================== 导出函数 ========================
        function exportLocalCSV() {
            // 从本地缓存获取数据
            let records = getLocalCache('records');
            const account = document.getElementById('filterAccount').value;
            const date = document.getElementById('filterDate').value;
            const mode = document.getElementById('filterMode').value;
            
            // 本地筛选
            if (account) records = records.filter(r => r.account === account);
            if (date) records = records.filter(r => r.date === date);
            if (mode) records = records.filter(r => r.mode === mode);
            
            if (!records.length) {
                alert('暂无符合条件的记录！');
                return;
            }
            
            // 前端生成CSV
            const csvHeader = '\ufeff账号,交易对,发文类型,日期,时间,内容\n';
            let csvContent = csvHeader;
            
            records.forEach(record => {
                const content = record.content.replace(/"/g, '""');
                csvContent += `"${record.account}","${record.symbol}","${record.mode === 'manual' ? '手动' : '自动'}","${record.date}","${record.create_time}","${content}"\n`;
            });
            
            // 本地下载
            const blob = new Blob([csvContent], { type: 'text/csv; charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `币安发文记录_${new Date().toISOString().split('T')[0]}.csv`;
            link.click();
            URL.revokeObjectURL(url);
            
            alert(`成功导出 ${records.length} 条记录（本地生成）！`);
        }
        
        function exportServerCSV() {
            // 调用服务端导出接口
            const account = document.getElementById('filterAccount').value;
            const date = document.getElementById('filterDate').value;
            const mode = document.getElementById('filterMode').value;
            
            const params = new URLSearchParams();
            if (account) params.append('account', account);
            if (date) params.append('date', date);
            if (mode) params.append('mode', mode);
            
            window.open(`/api/records/export?${params.toString()}`, '_blank');
        }
        
        // ======================== 辅助函数 ========================
        function clearForm() {
            document.getElementById('symbolInput').value = '';
            document.getElementById('contentInput').value = '';
            document.getElementById('modeSelect').value = 'manual';
        }
        
        // 页面加载初始化
        window.onload = function() {
            loadRecords();
            loadStats();
        };
    </script>
</body>
</html>
'''

# ======================== 核心工具函数 ========================
def init_record_file():
    """初始化记录文件（若不存在则创建）"""
    if not os.path.exists(RECORD_FILE):
        with open(RECORD_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f, ensure_ascii=False, indent=2)

def get_all_records():
    """从服务端本地文件读取所有记录"""
    init_record_file()
    with open(RECORD_FILE, 'r', encoding='utf-8') as f:
        try:
            records = json.load(f)
        except json.JSONDecodeError:
            records = []
    return records

def save_record(record):
    """保存单条记录到服务端本地文件"""
    records = get_all_records()
    # 添加时间戳和ID
    record['id'] = str(datetime.now().timestamp() * 1000).split('.')[0]
    record['create_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    record['date'] = datetime.now().strftime('%Y-%m-%d')
    
    # 追加记录并限制数量
    records.append(record)
    if len(records) > MAX_RECORDS:
        records = records[-MAX_RECORDS:]  # 保留最新的MAX_RECORDS条
    
    # 写入文件
    with open(RECORD_FILE, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    return record

def filter_records(filter_params):
    """按条件筛选记录"""
    records = get_all_records()
    account = filter_params.get('account')
    date = filter_params.get('date')
    mode = filter_params.get('mode')
    
    filtered = records
    if account:
        filtered = [r for r in filtered if r.get('account') == account]
    if date:
        filtered = [r for r in filtered if r.get('date') == date]
    if mode:
        filtered = [r for r in filtered if r.get('mode') == mode]
    
    return filtered

# ======================== API接口 ========================
@app.route('/api/records/add', methods=['POST'])
def add_record():
    """添加发文记录（服务端存储）"""
    data = request.json
    # 校验必填字段
    if not all([data.get('account'), data.get('content')]):
        return jsonify({'code': 400, 'msg': '账号和内容不能为空'}), 400
    
    # 构造记录
    record = {
        'account': data.get('account'),
        'symbol': data.get('symbol', '未知'),
        'mode': data.get('mode', 'manual'),  # manual/auto
        'content': data.get('content'),
        'status': 'success'
    }
    
    # 保存到服务端本地
    saved_record = save_record(record)
    return jsonify({
        'code': 200,
        'msg': '记录添加成功',
        'data': saved_record
    })

@app.route('/api/records/list', methods=['GET'])
def get_records():
    """获取筛选后的记录（供前端本地缓存）"""
    filter_params = {
        'account': request.args.get('account'),
        'date': request.args.get('date'),
        'mode': request.args.get('mode')
    }
    filtered_records = filter_records(filter_params)
    return jsonify({
        'code': 200,
        'data': filtered_records
    })

@app.route('/api/records/export', methods=['GET'])
def export_records():
    """服务端生成CSV导出（备用方案，优先前端本地导出）"""
    filter_params = {
        'account': request.args.get('account'),
        'date': request.args.get('date'),
        'mode': request.args.get('mode')
    }
    records = filter_records(filter_params)
    
    if not records:
        return jsonify({'code': 400, 'msg': '无符合条件的记录'}), 400
    
    # 生成CSV内容（带BOM头解决Excel中文乱码）
    csv_header = '\ufeffID,账号,交易对,发文类型,日期,创建时间,内容\n'
    csv_content = csv_header
    
    for record in records:
        # CSV格式转义
        content = record.get('content', '').replace('"', '""')
        csv_line = (
            f'"{record.get("id")}","{record.get("account")}","{record.get("symbol")}",'
            f'"{"手动" if record.get("mode")=="manual" else "自动"}","{record.get("date")}",'
            f'"{record.get("create_time")}","{content}"\n'
        )
        csv_content += csv_line
    
    # 构造响应
    filename = urllib.parse.quote(f'币安发文记录_{datetime.now().strftime("%Y%m%d")}.csv')
    response = make_response(csv_content)
    response.headers['Content-Type'] = 'text/csv; charset=utf-8'
    response.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{filename}'
    return response

@app.route('/api/records/export_zip', methods=['GET'])
def export_zip():
    """导出压缩包（大容量记录时使用）"""
    filter_params = {
        'account': request.args.get('account'),
        'date': request.args.get('date'),
        'mode': request.args.get('mode')
    }
    records = filter_records(filter_params)
    
    if not records:
        return jsonify({'code': 400, 'msg': '无符合条件的记录'}), 400
    
    # 生成CSV内容
    csv_header = '\ufeffID,账号,交易对,发文类型,日期,创建时间,内容\n'
    csv_content = csv_header
    for record in records:
        content = record.get('content', '').replace('"', '""')
        csv_line = (
            f'"{record.get("id")}","{record.get("account")}","{record.get("symbol")}",'
            f'"{"手动" if record.get("mode")=="manual" else "自动"}","{record.get("date")}",'
            f'"{record.get("create_time")}","{content}"\n'
        )
        csv_content += csv_line
    
    # 生成ZIP包
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            f'币安发文记录_{datetime.now().strftime("%Y%m%d")}.csv',
            csv_content.encode('utf-8')
        )
    zip_buffer.seek(0)
    
    # 构造响应
    filename = urllib.parse.quote(f'币安发文记录_{datetime.now().strftime("%Y%m%d")}.zip')
    response = make_response(zip_buffer)
    response.headers['Content-Type'] = 'application/zip'
    response.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{filename}'
    return response

@app.route('/api/records/stats', methods=['GET'])
def get_stats():
    """获取统计数据"""
    records = get_all_records()
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 统计今日数据
    stats = {
        'total': 0,
        'manual': 0,
        'auto': 0,
        'accounts': {}
    }
    
    # 初始化账号统计
    for record in records:
        acc = record.get('account')
        if acc not in stats['accounts']:
            stats['accounts'][acc] = {'total': 0, 'manual': 0, 'auto': 0}
    
    # 统计今日记录
    for record in records:
        if record.get('date') == today:
            stats['total'] += 1
            mode = record.get('mode', 'manual')
            stats[mode] += 1
            
            acc = record.get('account')
            stats['accounts'][acc]['total'] += 1
            stats['accounts'][acc][mode] += 1
    
    return jsonify({
        'code': 200,
        'data': stats
    })

# ======================== 前端页面路由（直接返回内嵌的HTML） ========================
@app.route('/')
def index():
    """返回内嵌的前端页面"""
    return make_response(HTML_CONTENT)

# ======================== 启动配置 ========================
if __name__ == '__main__':
    # 初始化记录文件
    init_record_file()
    # 启动服务（调试模式，云部署时需改为生产模式）
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,  # 云部署时务必关闭debug模式！
        threaded=True  # 开启多线程，支持并发访问
    )
