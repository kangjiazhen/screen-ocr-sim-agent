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

from werkzeug.security import generate_password_hash, check_password_hash
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
        <input type="text" name="username" placeholder="请输入用户名" autofocus>
        <input type="password" name="password" placeholder="请输入密码">
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

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return jsonify({"ok": False, "error": "需要管理员权限"}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username:
            error = '请输入用户名'
        else:
            user = stats.get_user_by_username(username)
            if user and check_password_hash(user['password_hash'], password):
                session['logged_in'] = True
                session['username'] = user['username']
                session['role'] = user['role']
                return redirect(url_for('index'))
            elif password == WEB_PASSWORD:
                session['logged_in'] = True
                session['username'] = username or 'admin'
                session['role'] = 'admin'
                return redirect(url_for('index'))
            else:
                error = '用户名或密码错误'
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout')
def logout():
    session.clear()
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
.badge.timely{background:#fff2f0;color:#ff4d4f}
.badge.not-timely{background:#f5f5f5;color:#999}
.badge.update-warn{background:#ff4d4f;color:#fff;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
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
.batch-bar{position:fixed;bottom:0;left:0;right:0;background:#fff;border-top:2px solid #667eea;padding:12px 32px;display:none;align-items:center;gap:16px;z-index:999;box-shadow:0 -4px 12px rgba(0,0,0,.1)}
.batch-bar.active{display:flex}
.batch-bar .count{font-weight:600;color:#667eea;font-size:14px}
.batch-bar button{padding:6px 16px;border-radius:4px;font-size:13px;cursor:pointer;border:1px solid #d9d9d9;background:#fff}
.batch-bar .btn-batch-delete{background:#ff4d4f;color:#fff;border-color:#ff4d4f}
.batch-bar .btn-batch-status{background:#667eea;color:#fff;border-color:#667eea}
</style>
</head>
<body>
<div class="header">
    <h1>📊 问题统计后台</h1>
        <div class="toolbar">
        {% if is_admin %}
        <button class="btn-cats" onclick="openCatManager()">📂 分类管理</button>
        <button class="btn-cats" onclick="openUserManager()">👥 用户管理</button>
        {% endif %}
        <a href="/export?{{ filter_query }}" class="btn-export">📥 导出 Excel</a>
        <a href="/export_rag" class="btn-export">📚 知识库导出</a>
        <span style="color:rgba(255,255,255,.7);font-size:13px;margin:0 8px">{{ username }}({{ '管理员' if is_admin else '普通用户' }})</span>
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
    <select name="is_timely">
        <option value="">🕐 全部时效性</option>
        <option value="1" {% if is_timely == '1' %}selected{% endif %}>⚠️ 高时效性</option>
    </select>
    <button type="submit" class="btn-search">搜索</button>
    <a href="/" class="btn-reset" style="text-decoration:none;display:inline-block;line-height:1.5">重置</a>
</form>

<div class="table-wrap">
    <table>
        <thead>
            <tr>
                <th><input type="checkbox" id="selectAll" onclick="toggleSelectAll()" title="全选/取消全选"></th>
                <th>ID</th>
                <th>分类</th>
                <th>提问内容</th>
                <th>昵称</th>
                <th>Coze答复</th>
                <th>专业答复</th>
                <th>状态</th>
                <th>时效性</th>
                <th>时间</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            {% if rows %}
            {% for row in rows %}
            <tr class="{% if row[8] == 'dismissed' %}dismissed{% endif %}">
                <td><input type="checkbox" class="row-check" value="{{ row[0] }}" onchange="onCheckChange()"></td>
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
                <td>
                    {% if row[9] %}
                    <span class="badge timely">高时效</span>
                    {% if row[10] and (now - row[10]) > 604800 %}<span class="badge update-warn" title="最后编辑于 {{ row[10]|timestamp }}">建议更新</span>{% endif %}
                    {% else %}<span class="badge not-timely">普通</span>{% endif %}
                </td>
                <td>{{ row[5]|timestamp }}</td>
                <td>
                    <button class="btn-edit" onclick="openEdit({{ row[0] }})">编辑</button>
                    {% if is_admin %}
                    <button class="btn-edit" style="background:#ff4d4f;margin-left:4px" onclick="deleteQuestion({{ row[0] }})">删除</button>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
            {% else %}
            <tr><td colspan="11" class="empty-state"><div class="icon">📭</div>暂无数据 — bot.py 运行后会自动采集</td></tr>
            {% endif %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<div class="pagination">
    {% if page > 1 %}
    <a href="?page=1&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}&is_timely={{ is_timely }}">首页</a>
    <a href="?page={{ page-1 }}&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}&is_timely={{ is_timely }}">上一页</a>
    {% else %}
    <span class="disabled">首页</span><span class="disabled">上一页</span>
    {% endif %}
    <span>第 {{ page }} / {{ total_pages }} 页 (共 {{ total }} 条)</span>
    {% if page < total_pages %}
    <a href="?page={{ page+1 }}&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}&is_timely={{ is_timely }}">下一页</a>
    <a href="?page={{ total_pages }}&keyword={{ keyword }}&category={{ category }}&date_from={{ date_from }}&date_to={{ date_to }}&reply_status={{ reply_status }}&is_timely={{ is_timely }}">末页</a>
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
            <div class="field"><label>分类</label><select id="modalCategory" style="width:100%;padding:8px 12px;border:1px solid #d9d9d9;border-radius:6px;font-size:13px"><option value="">加载中...</option></select></div>
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
            <div class="field" style="margin-top:8px">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-weight:400;color:#555">
                    <input type="checkbox" id="modalIsTimely" onchange="toggleTimely()"> 高时效性问题
                </label>
                <span id="modalUpdateWarn" style="display:none;color:#ff4d4f;font-size:12px;margin-left:8px">⚠️ 建议更新 — 已超7天未编辑</span>
                <span id="modalLastEdited" style="color:#999;font-size:12px;margin-left:8px"></span>
            </div>
        </div>
        <div class="modal-footer">
            {% if is_admin %}
            <button class="btn-dismiss" style="color:#ff4d4f;border-color:#ff4d4f" onclick="deleteCurrentQuestion()">🗑 删除此问题</button>
            {% endif %}
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

<div class="modal-overlay" id="userModal">
    <div class="modal">
        <div class="modal-header">
            <span>👥 用户管理</span>
            <button class="close" onclick="closeUserManager()">&times;</button>
        </div>
        <div class="modal-body">
            <div class="cat-add-row">
                <input type="text" id="newUsername" placeholder="用户名">
                <input type="password" id="newPassword" placeholder="密码">
                <select id="newUserRole">
                    <option value="user">普通用户</option>
                    <option value="admin">管理员</option>
                </select>
                <button onclick="addUser()">+ 添加</button>
            </div>
            <table class="category-table" style="margin-top:12px">
                <thead><tr><th>用户名</th><th>角色</th><th>新密码</th><th>操作</th></tr></thead>
                <tbody id="userTableBody"><tr><td colspan="4" style="text-align:center;color:#ccc">加载中...</td></tr></tbody>
            </table>
        </div>
    </div>
</div>

<div class="batch-bar" id="batchBar">
    <span class="count" id="batchCount">已选 0 项</span>
    <button onclick="batchStatus('replied')">✅ 标记已答复</button>
    <button onclick="batchStatus('pending')">⏳ 标记待答复</button>
    <button onclick="batchStatus('dismissed')">🚫 不予处理</button>
    {% if is_admin %}
    <button class="btn-batch-delete" onclick="batchDelete()">🗑 批量删除</button>
    {% endif %}
    <button onclick="clearSelection()">✕ 取消选择</button>
</div>

<div class="toast" id="toast"></div>

<script>
let currentQuestionId = null;
let currentClusterId = null;
let currentCozeReply = '';
let currentCategory = '';
const isAdmin = {{ 'true' if is_admin else 'false' }};

function openEdit(id) {
    currentQuestionId = id;
    document.getElementById('editModal').classList.add('active');
    Promise.all([
        fetch('/api/detail/' + id).then(r => r.json()),
        fetch('/api/categories').then(r => r.json())
    ]).then(([d, catData]) => {
        if (d.ok) {
            currentClusterId = d.data.cluster_id;
            currentCozeReply = d.data.coze_body || d.data.coze_reply || '';
            const catSelect = document.getElementById('modalCategory');
            catSelect.innerHTML = '<option value="">未分类</option>';
            const cats = catData.data || [];
            cats.forEach(c => {
                if (c.enabled) {
                    catSelect.innerHTML += '<option value="' + esc(c.name) + '">' + esc(c.name) + '</option>';
                }
            });
            catSelect.value = d.data.category || '';
            currentCategory = d.data.category || '';
            document.getElementById('modalQuestion').innerText = d.data.question;
            document.getElementById('modalCozeReply').innerText = currentCozeReply || '(无)';
            document.getElementById('modalReply').value = d.data.reply_body || '';
            document.getElementById('modalStatus').value = d.data.status || 'pending';
            document.getElementById('applyToCluster').checked = false;
            document.getElementById('modalIsTimely').checked = d.data.is_timely || false;
            const lastEdited = d.data.last_edited;
            const warnEl = document.getElementById('modalUpdateWarn');
            const editedEl = document.getElementById('modalLastEdited');
            if (lastEdited) {
                const dt = new Date(lastEdited * 1000);
                editedEl.innerText = '最后编辑: ' + dt.toLocaleString('zh-CN');
                editedEl.style.display = '';
                const now = Date.now() / 1000;
                if (d.data.is_timely && (now - lastEdited) > 604800) {
                    warnEl.style.display = '';
                } else {
                    warnEl.style.display = 'none';
                }
            } else {
                warnEl.style.display = 'none';
                editedEl.style.display = 'none';
            }
        }
    }).catch(e => showToast('加载失败: '+e,'error'));
}

function toggleTimely() {
    if (!currentClusterId) return;
    fetch('/api/timely/' + currentClusterId, {method:'POST'})
        .then(r => r.json())
        .then(d => {
            if (d.ok) {
                const isTimely = d.is_timely;
                document.getElementById('modalIsTimely').checked = isTimely;
                const label = isTimely ? '已标记为高时效性' : '已取消高时效性';
                showToast(label);
                setTimeout(() => location.reload(), 600);
            } else { showToast('操作失败', 'error'); }
        });
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
    const newCategory = document.getElementById('modalCategory').value;

    let url = '/api/reply/' + currentQuestionId;
    let payload = {reply_body: reply, status: status};
    if (applyAll && currentClusterId) {
        url = '/api/cluster_reply/' + currentClusterId;
    }

    fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
    .then(r => r.json())
    .then(d => {
        if (!d.ok) { showToast('❌ ' + (d.error || '失败'), 'error'); return; }
        if (newCategory && newCategory !== currentCategory) {
            fetch('/api/question/' + currentQuestionId + '/category', {
                method:'POST',headers:{'Content-Type':'application/json'},
                body:JSON.stringify({category: newCategory})
            }).then(r2 => r2.json()).then(d2 => {
                if (d2.ok) {
                    showToast('✅ 保存成功' + (applyAll ? '，已同步到整个分类' : ''));
                } else {
                    showToast('⚠️ 答复已保存，但分类更新失败: ' + (d2.error || ''));
                }
                closeEdit();
                setTimeout(() => location.reload(), 600);
            });
        } else {
            showToast('✅ 保存成功' + (applyAll ? '，已同步到整个分类' : ''));
            closeEdit();
            setTimeout(() => location.reload(), 600);
        }
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
        if (d.ok) { showToast('✅ 分类已添加'); setTimeout(() => location.reload(), 600); }
        else { showToast(d.error || '添加失败','error'); }
    });
}

function updateCat(id, name, desc) {
    const payload = {};
    if (name !== null) payload.name = name;
    if (desc !== null) payload.description = desc;
    fetch('/api/categories/' + id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
        .then(r => r.json()).then(d => {
            if (d.ok) { setTimeout(() => location.reload(), 400); }
            else { showToast(d.error,'error'); }
        });
}

function toggleCat(id, enabled) {
    fetch('/api/categories/' + id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:enabled})})
        .then(r => r.json()).then(d => {
            if (d.ok) { setTimeout(() => location.reload(), 400); }
        });
}

function delCat(id) {
    if (!confirm('确认删除该分类？')) return;
    fetch('/api/categories/' + id + '/delete', {method:'POST'})
        .then(r => r.json()).then(d => {
            if (d.ok) { showToast('✅ 已删除'); setTimeout(() => location.reload(), 600); }
            else { showToast(d.error,'error'); }
        });
}

function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function openUserManager() {
    document.getElementById('userModal').classList.add('active');
    loadUsers();
}

function closeUserManager() {
    document.getElementById('userModal').classList.remove('active');
}

function loadUsers() {
    fetch('/api/users')
        .then(r => r.json())
        .then(d => {
            let html = '';
            d.data.forEach(u => {
                html += '<tr>' +
                    '<td><input type="text" value="' + esc(u.username) + '" data-id="' + u.id + '" class="user-name-input"></td>' +
                    '<td><select data-id="' + u.id + '" class="user-role-input">' +
                    '<option value="user"' + (u.role==='user'?' selected':'') + '>普通用户</option>' +
                    '<option value="admin"' + (u.role==='admin'?' selected':'') + '>管理员</option>' +
                    '</select></td>' +
                    '<td><input type="password" data-id="' + u.id + '" class="user-pw-input" placeholder="留空不修改" style="width:120px"></td>' +
                    '<td>' +
                    '<button class="btn-sm" onclick="saveUserRow(' + u.id + ')" style="color:#667eea;margin-right:4px">保存</button>' +
                    '<button class="btn-sm" onclick="delUser(' + u.id + ')" style="color:#ff4d4f">删除</button>' +
                    '</td>' +
                    '</tr>';
            });
            if (!d.data.length) html = '<tr><td colspan="4" style="text-align:center;color:#ccc">暂无用户</td></tr>';
            document.getElementById('userTableBody').innerHTML = html;
        });
}

function addUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const role = document.getElementById('newUserRole').value;
    if (!username || !password) { showToast('用户名和密码不能为空','error'); return; }
    fetch('/api/users/add', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({username:username, password:password, role:role})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { showToast('用户已添加'); loadUsers(); }
        else { showToast(d.error || '添加失败','error'); }
    });
}

function saveUserRow(id) {
    const nameEl = document.querySelector('.user-name-input[data-id="' + id + '"]');
    const pwEl = document.querySelector('.user-pw-input[data-id="' + id + '"]');
    const roleEl = document.querySelector('.user-role-input[data-id="' + id + '"]');
    const payload = {};
    if (nameEl) payload.username = nameEl.value.trim();
    if (roleEl) payload.role = roleEl.value;
    if (pwEl && pwEl.value) payload.password = pwEl.value;
    fetch('/api/users/' + id, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)})
        .then(r => r.json()).then(d => {
            if (d.ok) { showToast('已保存'); if (pwEl) pwEl.value = ''; loadUsers(); }
            else { showToast(d.error,'error'); }
        });
}

function delUser(id) {
    if (!confirm('确认删除该用户？')) return;
    fetch('/api/users/' + id + '/delete', {method:'POST'})
        .then(r => r.json()).then(d => {
            if (d.ok) { showToast('已删除'); loadUsers(); }
            else { showToast(d.error,'error'); }
        });
}

function deleteQuestion(id) {
    if (!confirm('确认删除该问题？此操作不可撤销。')) return;
    fetch('/api/question/' + id + '/delete', {method:'POST'})
        .then(r => r.json())
        .then(d => {
            if (d.ok) { showToast('已删除'); setTimeout(function() { location.reload(); }, 600); }
            else { showToast(d.error || '删除失败','error'); }
        });
}

function deleteCurrentQuestion() {
    if (currentQuestionId) {
        closeEdit();
        deleteQuestion(currentQuestionId);
    }
}

// === 全选 & 批量操作 ===
function toggleSelectAll() {
    const checked = document.getElementById('selectAll').checked;
    document.querySelectorAll('.row-check').forEach(cb => { cb.checked = checked; });
    updateBatchBar();
}

function onCheckChange() {
    const all = document.querySelectorAll('.row-check');
    const checked = document.querySelectorAll('.row-check:checked');
    document.getElementById('selectAll').checked = all.length > 0 && checked.length === all.length;
    updateBatchBar();
}

function updateBatchBar() {
    const checked = document.querySelectorAll('.row-check:checked');
    const bar = document.getElementById('batchBar');
    document.getElementById('batchCount').innerText = '已选 ' + checked.length + ' 项';
    if (checked.length > 0) { bar.classList.add('active'); }
    else { bar.classList.remove('active'); }
}

function clearSelection() {
    document.querySelectorAll('.row-check').forEach(cb => { cb.checked = false; });
    document.getElementById('selectAll').checked = false;
    updateBatchBar();
}

function getSelectedIds() {
    return Array.from(document.querySelectorAll('.row-check:checked')).map(cb => parseInt(cb.value));
}

function batchStatus(status) {
    const ids = getSelectedIds();
    if (!ids.length) return;
    const labels = {pending:'待答复', replied:'已答复', dismissed:'不予处理'};
    if (!confirm('将 ' + ids.length + ' 条问题标记为「' + (labels[status]||status) + '」？')) return;
    fetch('/api/batch/status', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ids: ids, status: status})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { showToast('已更新 ' + d.count + ' 条'); setTimeout(function() { location.reload(); }, 600); }
        else { showToast(d.error || '操作失败','error'); }
    });
}

function batchDelete() {
    const ids = getSelectedIds();
    if (!ids.length) return;
    if (!confirm('确认永久删除 ' + ids.length + ' 条问题？此操作不可撤销！')) return;
    fetch('/api/batch/delete', {
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({ids: ids})
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { showToast('已删除 ' + d.count + ' 条'); setTimeout(function() { location.reload(); }, 600); }
        else { showToast(d.error || '删除失败','error'); }
    });
}

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
    import time as _time
    keyword = request.args.get('keyword', '')
    category = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    reply_status = request.args.get('reply_status', '')
    is_timely = request.args.get('is_timely', '')
    page = max(int(request.args.get('page', 1)), 1)
    per_page = 50

    df = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp() if date_from else None
    dt = datetime.datetime.strptime(date_to, "%Y-%m-%d").timestamp() + 86399 if date_to else None

    result = stats.search_questions(
        keyword=keyword or None,
        category=category or None,
        date_from=df,
        date_to=dt,
        reply_status=reply_status or None,
        is_timely=True if is_timely == '1' else None,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    rows = result["questions"]
    total = result["total"]
    total_pages = max((total + per_page - 1) // per_page, 1)

    summary = stats.get_summary()
    categories = stats.get_categories()

    filter_query = f"keyword={keyword}&category={category}&date_from={date_from}&date_to={date_to}&reply_status={reply_status}&is_timely={is_timely}"

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
        is_timely=is_timely,
        filter_query=filter_query,
        now=_time.time(),
        username=session.get('username', ''),
        role=session.get('role', 'user'),
        is_admin=(session.get('role') == 'admin'),
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
            "is_timely": bool(row[10]) if len(row) > 10 else False,
            "last_edited": row[11] if len(row) > 11 else 0,
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


@app.route('/api/timely/<int:cluster_id>', methods=['POST'])
@login_required
def api_toggle_timely(cluster_id):
    try:
        val = stats.toggle_timely(cluster_id)
        return jsonify({"ok": True, "is_timely": bool(val)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/users')
@login_required
def api_users():
    return jsonify({"ok": True, "data": stats.get_users()})


@app.route('/api/users/add', methods=['POST'])
@admin_required
def api_add_user():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'user')
    if not username or not password:
        return jsonify({"ok": False, "error": "用户名和密码不能为空"})
    if role not in ('admin', 'user'):
        return jsonify({"ok": False, "error": "无效的角色"})
    pw_hash = generate_password_hash(password)
    ok, err = stats.add_user(username, pw_hash, role)
    return jsonify({"ok": ok, "error": err if not ok else ""})


@app.route('/api/users/<int:user_id>', methods=['POST'])
@admin_required
def api_update_user(user_id):
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')
    pw_hash = generate_password_hash(password) if password else None
    ok, err = stats.update_user(user_id, username=username, password_hash=pw_hash, role=role)
    return jsonify({"ok": ok, "error": err if not ok else ""})


@app.route('/api/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def api_delete_user(user_id):
    ok, err = stats.delete_user(user_id)
    return jsonify({"ok": ok, "error": err if not ok else ""})


@app.route('/api/question/<int:question_id>/delete', methods=['POST'])
@admin_required
def api_delete_question(question_id):
    try:
        stats.delete_question(question_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/question/<int:question_id>/category', methods=['POST'])
@login_required
def api_update_question_category(question_id):
    data = request.get_json()
    new_category = data.get('category', '').strip()
    if not new_category:
        return jsonify({"ok": False, "error": "分类不能为空"})
    ok = stats.update_question_category(question_id, new_category)
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "问题不存在"})


@app.route('/api/categories')
@login_required
def api_categories():
    return jsonify({"ok": True, "data": stats.get_categories_detail()})


@app.route('/api/categories/add', methods=['POST'])
@admin_required
def api_add_category():
    data = request.get_json()
    name = data.get('name', '').strip()
    desc = data.get('description', '')
    if not name:
        return jsonify({"ok": False, "error": "名称不能为空"})
    ok = stats.add_category(name, desc)
    return jsonify({"ok": ok, "error": "已存在" if not ok else ""})


@app.route('/api/categories/<int:cat_id>', methods=['POST'])
@admin_required
def api_update_category(cat_id):
    data = request.get_json()
    name = data.get('name')
    desc = data.get('description')
    enabled = data.get('enabled')
    stats.update_category(cat_id, name=name, description=desc, enabled=enabled)
    return jsonify({"ok": True})


@app.route('/api/categories/<int:cat_id>/delete', methods=['POST'])
@admin_required
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
    is_timely = request.args.get('is_timely', '')

    df = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp() if date_from else None
    dt = datetime.datetime.strptime(date_to, "%Y-%m-%d").timestamp() + 86399 if date_to else None

    export_dir = os.path.join(os.path.dirname(__file__), "exports")
    os.makedirs(export_dir, exist_ok=True)
    filename = f"questions_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = os.path.join(export_dir, filename)

    stats.export_excel(filepath,
                       keyword=keyword or None,
                       category=category or None,
                       date_from=df,
                       date_to=dt,
                       reply_status=reply_status or None,
                       is_timely=True if is_timely == '1' else None)

    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/api/batch/status', methods=['POST'])
@login_required
def api_batch_status():
    data = request.get_json()
    ids = data.get('ids', [])
    status = data.get('status', 'pending')
    if not ids or status not in ('pending', 'replied', 'dismissed'):
        return jsonify({"ok": False, "error": "参数无效"})
    try:
        count = stats.batch_set_status(ids, status)
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/api/batch/delete', methods=['POST'])
@admin_required
def api_batch_delete():
    data = request.get_json()
    ids = data.get('ids', [])
    if not ids:
        return jsonify({"ok": False, "error": "未选择任何问题"})
    try:
        count = stats.batch_delete_questions(ids)
        return jsonify({"ok": True, "count": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route('/export_rag')
@login_required
def export_rag():
    export_dir = os.path.join(os.path.dirname(__file__), "exports")
    os.makedirs(export_dir, exist_ok=True)
    filename = f"rag_knowledge_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = os.path.join(export_dir, filename)
    stats.export_rag_excel(filepath)
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
