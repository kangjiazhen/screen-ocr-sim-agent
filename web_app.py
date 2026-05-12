# -*- coding: utf-8 -*-
"""
问题统计 Web 后台 v1.0.31
========================================
- 问题列表 + 筛选 + 分页
- 填写/编辑专业答复 + 采用Coze回答
- 三状态管理: 待答复 / 已答复 / 不予处理
- 分类维护 (categories 表)
- Excel 导出（明细 + 汇总双Sheet）
- 密码登录 + 局域网协同
- 基于 Flask + SQLite
========================================
使用方法: python web_app.py
"""
import os
import datetime
import logging
import functools
from flask import Flask, render_template_string, request, jsonify, send_file, session, redirect, url_for

from question_stats import QuestionStats, parse_coze_category
try:
    from config import WEB_HOST, WEB_PORT, WEB_PASSWORD
except ImportError:
    WEB_HOST, WEB_PORT, WEB_PASSWORD = "127.0.0.1", 5000, "admin123"

STATS_DB_PATH = os.path.join(os.path.dirname(__file__), "questions.db")

app = Flask(__name__)
app.secret_key = os.urandom(24)
stats = QuestionStats(STATS_DB_PATH)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>登录 - 问题统计后台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:linear-gradient(135deg,#667eea,#764ba2);min-height:100vh;display:flex;justify-content:center;align-items:center;font-family:-apple-system,"Microsoft YaHei",sans-serif}
.login-box{background:#fff;border-radius:12px;padding:40px;width:360px;box-shadow:0 8px 32px rgba(0,0,0,.2)}
.login-box h2{text-align:center;margin-bottom:28px;color:#333;font-size:20px}
.login-box input{width:100%;padding:12px;border:1px solid #d9d9d9;border-radius:6px;font-size:14px;margin-bottom:16px;outline:none}
.login-box input:focus{border-color:#667eea}
.login-box button{width:100%;padding:12px;background:#667eea;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer}
.login-box button:hover{background:#5a6fd6}
.error{color:#ff4d4f;text-align:center;margin-bottom:12px;font-size:13px}
</style></head>
<body>
<div class="login-box">
    <h2>🔐 问题统计后台</h2>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <form method="POST">
        <input type="password" name="password" placeholder="请输入密码" autofocus>
        <button type="submit">登 录</button>
    </form>
</div>
</body>
</html>
"""

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == WEB_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = '密码错误'
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>问题统计后台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;background:#f0f2f5;color:#333;min-height:100vh}
.header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:20px 32px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
.header h1{font-size:22px;font-weight:600}
.header .btn-export{background:rgba(255,255,255,.2);color:#fff;border:1px solid rgba(255,255,255,.3);padding:8px 18px;border-radius:6px;cursor:pointer;font-size:14px;text-decoration:none}
.header .btn-export:hover,.header .btn-cats:hover{background:rgba(255,255,255,.35)}
.header .btn-cats{background:rgba(255,255,255,.2);color:#fff;border:1px solid rgba(255,255,255,.3);padding:8px 18px;border-radius:6px;cursor:pointer;font-size:14px}
.cards{display:flex;gap:16px;padding:20px 32px;flex-wrap:wrap}
.card{background:#fff;border-radius:10px;padding:18px 24px;flex:1;min-width:140px;box-shadow:0 1px 4px rgba(0,0,0,.06);cursor:default}
.card.replied .num{color:#52c41a}
.card.pending .num{color:#fa8c16}
.card.dismissed .num{color:#999}
.card .num{font-size:32px;font-weight:700;color:#667eea}
.card .label{font-size:13px;color:#999;margin-top:4px}
.filters{background:#fff;margin:0 32px;padding:16px 20px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.06);display:flex;gap:12px;flex-wrap:wrap;align-items:center}
.filters input,.filters select,.filters button{padding:8px 12px;border:1px solid #d9d9d9;border-radius:6px;font-size:13px;outline:none}
.filters input:focus,.filters select:focus{border-color:#667eea}
.filters input[type="date"]{width:140px}
.filters input[type="text"]{width:200px}
.filters .btn-search{background:#667eea;color:#fff;border:none;cursor:pointer;padding:8px 20px;border-radius:6px}
.filters .btn-search:hover{background:#5a6fd6}
.filters .btn-reset{background:#f0f0f0;border:1px solid #d9d9d9;cursor:pointer;padding:8px 16px;border-radius:6px}
.table-wrap{background:#fff;margin:16px 32px;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.06);overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th{background:#fafafa;padding:12px 14px;text-align:left;font-weight:600;border-bottom:2px solid #f0f0f0;white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid #f0f0f0;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:#f5f7ff}
tr.dismissed td{opacity:.6}
.reply-cell{max-width:220px;cursor:pointer}
.reply-cell:hover{color:#667eea}
.btn-edit{background:#667eea;color:#fff;border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px}
.btn-edit:hover{background:#5a6fd6}
.pagination{display:flex;justify-content:center;align-items:center;gap:10px;padding:16px 0;flex-wrap:wrap}
.pagination a,.pagination span{display:inline-block;padding:6px 14px;border:1px solid #d9d9d9;border-radius:4px;font-size:13px;text-decoration:none;color:#333;background:#fff}
.pagination a:hover{background:#667eea;color:#fff;border-color:#667eea}
.pagination .current{background:#667eea;color:#fff;border-color:#667eea}
.pagination .disabled{color:#ccc;pointer-events:none}
.empty-state{text-align:center;padding:60px;color:#999}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px}
.badge.cat{background:#e6f7ff;color:#1890ff}
.badge.nocat{background:#fff7e6;color:#fa8c16}
.badge.s-replied{background:#f6ffed;color:#52c41a}
.badge.s-pending{background:#fff7e6;color:#fa8c16}
.badge.s-dismissed{background:#f5f5f5;color:#999}
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.45);z-index:1000;justify-content:center;align-items:center}
.modal-overlay.active{display:flex}
.modal{background:#fff;border-radius:12px;width:600px;max-width:90vw;max-height:85vh;overflow:auto;box-shadow:0 8px 32px rgba(0,0,0,.15)}
.modal-header{padding:16px 20px;border-bottom:1px solid #f0f0f0;display:flex;justify-content:space-between;align-items:center;font-weight:600;font-size:15px}
.modal-header .close{background:none;border:none;font-size:20px;cursor:pointer;color:#999}
.modal-body{padding:20px;font-size:13px}
.modal-body .field{margin-bottom:14px}
.modal-body .field label{display:block;font-weight:600;margin-bottom:4px;color:#555}
.modal-body .field .val{background:#fafafa;padding:10px 12px;border-radius:6px;word-break:break-all;max-height:100px;overflow-y:auto;white-space:pre-wrap}
.modal-body textarea{width:100%;min-height:120px;border:1px solid #d9d9d9;border-radius:6px;padding:10px;font-size:13px;resize:vertical;font-family:inherit}
.modal-body textarea:focus{border-color:#667eea;outline:none}
.modal-body select{width:100%;padding:8px 12px;border:1px solid #d9d9d9;border-radius:6px;font-size:13px;outline:none}
.modal-footer{padding:12px 20px;border-top:1px solid #f0f0f0;text-align:right}
.modal-footer button{padding:8px 18px;border-radius:6px;font-size:13px;cursor:pointer;margin-left:8px}
.modal-footer .btn-save{background:#667eea;color:#fff;border:none}
.modal-footer .btn-dismiss{background:#f5f5f5;color:#999;border:1px solid #d9d9d9}
.modal-footer .btn-cancel{background:#f0f0f0;border:1px solid #d9d9d9;color:#333}
.btn-adopt{font-size:12px;padding:4px 12px;background:#e6f7ff;color:#1890ff;border:1px solid #91d5ff;border-radius:4px;cursor:pointer}
.btn-adopt:hover{background:#bae7ff}
.toast{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#52c41a;color:#fff;padding:10px 24px;border-radius:6px;z-index:2000;display:none;font-size:14px}
.toast.show{display:block;animation:fadeIn .3s}
.toast.error{background:#ff4d4f}
@keyframes fadeIn{from{opacity:0;transform:translate(-50%,-10px)}to{opacity:1;transform:translate(-50%,0)}}
.category-table{width:100%;font-size:13px;margin-top:8px}
.category-table td{padding:6px 8px;border-bottom:1px solid #f0f0f0}
.category-table .btn-sm{font-size:11px;padding:2px 8px;border-radius:3px;cursor:pointer;border:1px solid #d9d9d9;background:#fafafa}
.category-table .btn-sm:hover{background:#eee}
.category-table input{border:1px solid #d9d9d9;border-radius:3px;padding:3px 6px;font-size:12px;width:100%}
.cat-add-row{display:flex;gap:8px;margin-top:8px}
.cat-add-row input{flex:1;padding:6px 10px;border:1px solid #d9d9d9;border-radius:4px;font-size:12px}
.cat-add-row button{padding:6px 14px;background:#667eea;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px}
</style>
</head>
<body>
<div class="header">
    <h1>📊 问题统计后台</h1>
    <div class="toolbar">
        <button class="btn-cats" onclick="openCatManager()">📂 分类管理</button>
        <a href="/export?{{ filter_query }}" class="btn-export">📥 导出 Excel</a>
        <a href="/logout" class="btn-export" style="font-size:13px">🚪 退出</a>
    </div>
</div>

<div class="cards">
    <div class="card"><div class="num">{{ summary.total_questions }}</div><div class="label">总提问数</div></div>
    <div class="card"><div class="num">{{ summary.total_clusters }}</div><div class="label">问题分类</div></div>
    <div class="card replied"><div class="num">{{ summary.cnt_replied }}</div><div class="label">✅ 已答复</div></div>
    <div class="card pending"><div class="num">{{ summary.cnt_pending }}</div><div class="label">⏳ 待答复</div></div>
    <div class="card dismissed"><div class="num">{{ summary.cnt_dismissed }}</div><div class="label">🚫 不予处理</div></div>
</div>

<form method="GET" class="filters">
    <input type="text" name="keyword" placeholder="🔍 搜索问题/分类..." value="{{ keyword }}">
    <select name="category">
        <option value="">📂 全部分类</option>
        {% for cat in categories %}
        <option value="{{ cat }}" {% if category == cat %}selected{% endif %}>{{ cat }}</option>
        {% endfor %}
    </select>
    <input type="date" name="date_from" value="{{ date_from }}" title="开始日期">
    <input type="date" name="date_to" value="{{ date_to }}" title="结束日期">
    <select name="reply_status">
        <option value="">📋 全部状态</option>
        <option value="pending" {% if reply_status == 'pending' %}selected{% endif %}>⏳ 待答复</option>
        <option value="replied" {% if reply_status == 'replied' %}selected{% endif %}>✅ 已答复</option>
        <option value="dismissed" {% if reply_status == 'dismissed' %}selected{% endif %}>🚫 不予处理</option>
    </select>
    <button type="submit" class="btn-search">搜索</button>
    <a href="/" class="btn-reset" style="text-decoration:none;display:inline-block;line-height:1.5">重置</a>
</form>

<div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th>ID</th>
                <th>分类</th>
                <th>提问内容</th>
                <th>昵称</th>
                <th>Coze答复</th>
                <th>专业答复</th>
                <th>状态</th>
                <th>时间</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            {% if rows %}
            {% for row in rows %}
            <tr class="{% if row[8] == 'dismissed' %}dismissed{% endif %}">
                <td>{{ row[0] }}</td>
                <td>{% if row[1] %}<span class="badge cat">{{ row[1] }}</span>{% else %}<span class="badge nocat">未分类</span>{% endif %}</td>
                <td title="{{ row[2] }}">{{ row[2][:50] }}{% if row[2]|length > 50 %}...{% endif %}</td>
                <td>{{ row[3] or '-' }}</td>
                <td title="{{ (row[7]|strip_category) or '' }}">{% set coze_text = row[7]|strip_category %}{% if coze_text %}{{ coze_text[:50] }}{% if coze_text|length > 50 %}...{% endif %}{% else %}<span style="color:#ccc">-</span>{% endif %}</td>
                <td class="reply-cell" onclick="openEdit({{ row[0] }})" title="点击编辑">
                    {% if row[4] %}<span>{{ row[4][:40] }}{% if row[4]|length > 40 %}...{% endif %}</span>
                    {% else %}<span style="color:#ccc;font-style:italic">点击填写</span>{% endif %}
                </td>
                <td>
                    {% if row[8] == 'replied' %}<span class="badge s-replied">已答复</span>
                    {% elif row[8] == 'pending' %}<span class="badge s-pending">待答复</span>
                    {% elif row[8] == 'dismissed' %}<span class="badge s-dismissed">不予处理</span>
                    {% else %}<span class="badge s-pending">待答复</span>{% endif %}
                </td>
                <td>{{ row[5]|timestamp }}</td>
                <td>
                    <button class="btn-edit" onclick="openEdit({{ row[0] }})">编辑</button>
                </td>
            </tr>
            {% endfor %}
            {% else %}
            <tr><td colspan="9" class="empty-state"><div class="icon">📭</div>暂无数据 — bot.py 运行后会自动采集</td></tr>
            {% endif %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<div class="pagination">
    {% if page > 1 %}
    <a href="?page=1&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}">首页</a>
    <a href="?page={{ page-1 }}&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}">上一页</a>
    {% else %}
    <span class="disabled">首页</span><span class="disabled">上一页</span>
    {% endif %}
    <span>第 {{ page }} / {{ total_pages }} 页 (共 {{ total }} 条)</span>
    {% if page < total_pages %}
    <a href="?page={{ page+1 }}&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}">下一页</a>
    <a href="?page={{ total_pages }}&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}">末页</a>
    {% else %}
    <span class="disabled">下一页</span><span class="disabled">末页</span>
    {% endif %}
</div>
{% endif %}

<div class="modal-overlay" id="editModal">
    <div class="modal">
        <div class="modal-header">
            <span>✏️ 编辑</span>
            <button class="close" onclick="closeEdit()">&times;</button>
        </div>
        <div class="modal-body">
            <div class="field"><label>分类</label><div class="val" id="modalCategory">-</div></div>
            <div class="field"><label>用户提问</label><div class="val" id="modalQuestion">-</div></div>
            <div class="field"><label>Coze 自动回复</label><div class="val" id="modalCozeReply" style="color:#888">-</div></div>
            <div class="field">
                <label>专业答复 <span style="color:#999;font-weight:400">(人工补充，存入 RAG 知识库)</span></label>
                <textarea id="modalReply" placeholder="输入专业答复内容..."></textarea>
                <div style="margin-top:6px">
                    <button class="btn-adopt" onclick="adoptCozeReply()">📋 采用Coze回答</button>
                </div>
            </div>
            <div class="field">
                <label>状态</label>
                <select id="modalStatus">
                    <option value="pending">⏳ 待答复</option>
                    <option value="replied">✅ 已答复</option>
                    <option value="dismissed">🚫 不予处理</option>
                </select>
            </div>
            <div class="field" style="margin-top:8px">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:400;color:#555">
                    <input type="checkbox" id="applyToCluster"> 同时应用到该分类下的所有提问
                </label>
            </div>
        </div>
        <div class="modal-footer">
            <button class="btn-dismiss" onclick="setStatus('dismissed')">🚫 不予处理</button>
            <button class="btn-cancel" onclick="closeEdit()">取消</button>
            <button class="btn-save" onclick="saveReply()">💾 保存</button>
        </div>
    </div>
</div>

<div class="modal-overlay" id="catModal">
    <div class="modal">
        <div class="modal-header">
            <span>📂 分类管理</span>
            <button class="close" onclick="closeCatManager()">&times;</button>
        </div>
        <div class="modal-body">
            <div class="cat-add-row">
                <input type="text" id="catName" placeholder="分类名称，如: 二手房出租咨询">
                <input type="text" id="catDesc" placeholder="备注（可选）">
                <button onclick="addCategory()">+ 添加</button>
            </div>
            <table class="category-table" style="margin-top:12px">
                <thead><tr><th>名称</th><th>备注</th><th>启用</th><th>操作</th></tr></thead>
                <tbody id="catTableBody"><tr><td colspan="4" style="text-align:center;color:#ccc">加载中...</td></tr></tbody>
            </table>
        </div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
let currentQuestionId = null;
let currentClusterId = null;
let currentCozeReply = '';

function openEdit(id) {
    currentQuestionId = id;
    document.getElementById('editModal').classList.add('active');
    fetch('/api/detail/' + id)
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                currentClusterId = d.data.cluster_id;
                currentCozeReply = d.data.coze_body || d.data.coze_reply || '';
                document.getElementById('modalCategory').innerText = d.data.category || '未分类';
                document.getElementById('modalQuestion').innerText = d.data.question;
                document.getElementById('modalCozeReply').innerText = currentCozeReply || '(无)';
                document.getElementById('modalReply').value = d.data.reply_body || '';
                document.getElementById('modalStatus').value = d.data.status || 'pending';
                document.getElementById('applyToCluster').checked = false;
            }
        }).catch(e => showToast('加载失败: '+e,'error'));
}

function adoptCozeReply() {
    if (currentCozeReply) {
        document.getElementById('modalReply').value = currentCozeReply;
    }
}

function closeEdit() {
    document.getElementById('editModal').classList.remove('active');
}

function saveReply() {
    const reply = document.getElementById('modalReply').value;
    const status = document.getElementById('modalStatus').value;
    const applyAll = document.getElementById('applyToCluster').checked;

    let url = '/api/reply/' + currentQuestionId;
    let payload = {reply_body: reply, status: status};
    if (applyAll && currentClusterId) {
        url = '/api/cluster_reply/' + currentClusterId;
    }

    fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(r => r.json())
    .then(d => {
        if (d.ok) {
            showToast('✅ 保存成功' + (applyAll ? '，已同步到整个分类' : ''));
            closeEdit();
            setTimeout(() => location.reload(), 600);
        } else { showToast('❌ ' + (d.error || '失败'), 'error'); }
    }).catch(e => showToast('❌ 请求失败: ' + e, 'error'));
}

function setStatus(st) {
    document.getElementById('modalStatus').value = st;
    if (st === 'dismissed') {
        fetch('/api/status/' + currentQuestionId, {
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({status: st})
        })
        .then(r => r.json())
        .then(d => {
            if (d.ok) { showToast('🚫 已标记为不予处理'); closeEdit(); setTimeout(() => location.reload(), 600); }
            else { showToast('❌ 失败', 'error'); }
        });
    }
}

function openCatManager() {
    document.getElementById('catModal').classList.add('active');
    loadCategories();
}

function closeCatManager() {
    document.getElementById('catModal').classList.remove('active');
}

function loadCategories() {
    fetch('/api/categories')
        .then(r => r.json())
        .then(d => {
            let html = '';
            d.data.forEach(c => {
                html += `<tr>
                    <td><input type="text" value="${esc(c.name)}" data-id="${c.id}" class="cat-name-input"></td>
                    <td><input type="text" value="${esc(c.description||'')}" data-id="${c.id}" class="cat-desc-input"></td>
                    <td><input type="checkbox" ${c.enabled?'checked':''} onchange="toggleCat(${c.id}, this.checked)"></td>
                    <td><button class="btn-sm" onclick="delCat(${c.id})" style="color:#ff4d4f">删除</button></td>
                </tr>`;
            });
            if (!d.data.length) html = '<tr><td colspan="4" style="text-align:center;color:#ccc">暂无分类，请添加</td></tr>';
            document.getElementById('catTableBody').innerHTML = html;

            document.querySelectorAll('.cat-name-input').forEach(inp => {
                inp.addEventListener('change', () => updateCat(inp.dataset.id, inp.value, null));
            });
            document.querySelectorAll('.cat-desc-input').forEach(inp => {
                inp.addEventListener('change', () => updateCat(inp.dataset.id, null, inp.value));
            });
        });
}

function addCategory() {
    const name = document.getElementById('catName').value.trim();
    const desc = document.getElementById('catDesc').value.trim();
    if (!name) { showToast('请输入分类名称','error'); return; }
    fetch('/api/categories/add', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:name,description:desc})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { document.getElementById('catName').value=''; document.getElementById('catDesc').value=''; loadCategories(); }
        else { showToast(d.error || '添加失败','error'); }
    });
}

function updateCat(id, name, desc) {
    const payload = {};
    if (name !== null) payload.name = name;
    if (desc !== null) payload.description = desc;
    fetch('/api/categories/' + id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
        .then(r => r.json()).then(d => { if (!d.ok) showToast(d.error,'error'); });
}

function toggleCat(id, enabled) {
    fetch('/api/categories/' + id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enabled})})
        .then(r => r.json());
}

function delCat(id) {
    if (!confirm('确认删除该分类？')) return;
    fetch('/api/categories/' + id + '/delete', {method:'POST'})
        .then(r => r.json()).then(d => {
            if (d.ok) loadCategories();
            else showToast(d.error,'error');
        });
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.className = 'toast show' + (type === 'error' ? ' error' : '');
    setTimeout(() => t.classList.remove('show'), 2500);
}
</script>
</body>
</html>
"""


@app.template_filter('timestamp')
def timestamp_filter(ts):
    if ts:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    return "-"


@app.template_filter('strip_category')
def strip_category_filter(text):
    if not text:
        return ""
    _, body = parse_coze_category(text)
    return body


@app.route('/')
@login_required
def index():
    keyword = request.args.get('keyword', '')
    category = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    reply_status = request.args.get('reply_status', '')
    page = max(int(request.args.get('page', 1)), 1)
    per_page = 50

    df = float(date_from) if date_from else None
    if df:
        df = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp()
    dt = float(date_to) if date_to else None
    if dt:
        dt = datetime.datetime.strptime(date_to, "%Y-%m-%d").timestamp() + 86399

    result = stats.search_questions(
        keyword=keyword or None,
        category=category or None,
        date_from=df,
        date_to=dt,
        reply_status=reply_status or None,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    rows = result["questions"]
    total = result["total"]
    total_pages = max((total + per_page - 1) // per_page, 1)

    summary = stats.get_summary()
    categories = stats.get_categories()

    filter_query = f"keyword={keyword}&category={category}&date_from={date_from}&date_to={date_to}&reply_status={reply_status}"

    return render_template_string(
        PAGE_HTML,
        rows=rows,
        total=total,
        page=page,
        total_pages=total_pages,
        summary=summary,
        categories=categories,
        keyword=keyword,
        category=category,
        date_from=date_from,
        date_to=date_to,
        reply_status=reply_status,
        filter_query=filter_query,
    )


@app.route('/api/detail/<int:question_id>')
@login_required
def api_detail(question_id):
    row = stats.get_question_detail(question_id)
    if not row:
        return jsonify({"ok": False, "error": "not found"})
    return jsonify({
        "ok": True,
        "data": {
            "id": row[0],
            "category": row[1] or "",
            "question": row[2],
            "nickname": row[3] or "",
            "reply_body": row[4] or "",
            "timestamp": row[5],
            "cluster_id": row[6],
            "coze_reply": (row[7] or ""),
            "coze_body": parse_coze_category(row[7] or "")[1],
            "raw_text": row[8] or "",
            "status": row[9] if len(row) > 9 else "pending",
        }
    })


@app.route('/api/reply/<int:question_id>', methods=['POST'])
@login_required
def api_update_reply(question_id):
    data = request.get_json()
    reply_body = data.get('reply_body', '')
    status = data.get('status', '')
    try:
        stats.update_reply(question_id, reply_body)
        if status:
            stats.set_question_status(question_id, status)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/cluster_reply/<int:cluster_id>', methods=['POST'])
@login_required
def api_update_cluster_reply(cluster_id):
    data = request.get_json()
    reply_body = data.get('reply_body', '')
    status = data.get('status', '')
    try:
        stats.update_cluster_reply(cluster_id, reply_body)
        if status:
            stats.set_cluster_status(cluster_id, status)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/status/<int:question_id>', methods=['POST'])
@login_required
def api_set_status(question_id):
    data = request.get_json()
    status = data.get('status', 'pending')
    try:
        stats.set_question_status(question_id, status)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/categories')
@login_required
def api_categories():
    return jsonify({"ok": True, "data": stats.get_categories_detail()})


@app.route('/api/categories/add', methods=['POST'])
@login_required
def api_add_category():
    data = request.get_json()
    name = data.get('name', '').strip()
    desc = data.get('description', '')
    if not name:
        return jsonify({"ok": False, "error": "名称不能为空"})
    ok = stats.add_category(name, desc)
    return jsonify({"ok": ok, "error": "已存在" if not ok else ""})


@app.route('/api/categories/<int:cat_id>', methods=['POST'])
@login_required
def api_update_category(cat_id):
    data = request.get_json()
    name = data.get('name')
    desc = data.get('description')
    enabled = data.get('enabled')
    stats.update_category(cat_id, name=name, description=desc, enabled=enabled)
    return jsonify({"ok": True})


@app.route('/api/categories/<int:cat_id>/delete', methods=['POST'])
@login_required
def api_delete_category(cat_id):
    stats.delete_category(cat_id)
    return jsonify({"ok": True})


@app.route('/export')
@login_required
def export_excel():
    keyword = request.args.get('keyword', '')
    category = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    reply_status = request.args.get('reply_status', '')

    df = float(date_from) if date_from else None
    if df:
        df = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp()
    dt = float(date_to) if date_to else None
    if dt:
        dt = datetime.datetime.strptime(date_to, "%Y-%m-%d").timestamp() + 86399

    export_dir = os.path.join(os.path.dirname(__file__), "exports")
    os.makedirs(export_dir, exist_ok=True)
    filename = f"questions_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = os.path.join(export_dir, filename)

    stats.export_excel(filepath,
                       keyword=keyword or None,
                       category=category or None,
                       date_from=df,
                       date_to=dt,
                       reply_status=reply_status or None)

    return send_file(filepath, as_attachment=True, download_name=filename)


if __name__ == '__main__':
    print()
    print("=" * 60)
    print("  问题统计 Web 后台 v1.0.31")
    print(f"  访问地址: http://{WEB_HOST}:{WEB_PORT}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)
    print()
    app.run(host=WEB_HOST, port=WEB_PORT, debug=False)
