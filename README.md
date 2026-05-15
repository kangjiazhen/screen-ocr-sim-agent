# 微信智能机器人

基于 Windows GUI 自动化 + PaddleOCR + Coze AI 大模型的微信自动回复机器人。截图识别红点定位未读消息，OCR 提取文字，调用 AI 后端生成回复，模拟键盘输入自动发送。附带问题统计数据库 + Web 管理后台。

无需 Hook、无需注入、无需逆向微信协议。

---

## 特性总览

### 🤖 自动回复

| 功能 | 说明 |
|------|------|
| **红点聚类扫描** | 截取左侧聊天列表，双模式颜色阈值识别红色像素，Y 轴聚类合并（≤25px），同时检测多个未读会话 |
| **Y 轴最高优先** | reversed(clusters) 从列表底部往上处理，新消息优先 |
| **群聊私聊识别** | OCR 提取发送者昵称自动区分群聊/私聊，群聊走 @回复，私聊直接发送 |
| **双模式切换** | `COZE_ENABLED=True` AI 回复模式（群聊需唤醒词）；`=False` 静默采集模式（所有消息直接入库） |
| **唤醒词触发** | 群聊中 @唤醒词 触发 AI，私聊无需唤醒词直接触发 |
| **真实 @提醒** | 群聊通过微信 @面板真实 @发送者，非纯文本模拟 |
| **同聊多轮** | 同一会话最多连续处理 4 条消息，snap_hash 变化检测自动推进 |
| **AI 后端可插拔** | `AI_BACKEND` 一键切换 coze / openai / custom，签名统一 |
| **Coze 分类解析** | 自动从回复中提取"分类：内容"格式，剥离分类发送纯净答复 |
| **熔断保护** | AI 请求总超时 60s，连续 5 次失败自动退避 |
| **会话隔离** | 不同聊天使用独立 AI 上下文（wx-{clicked_y}），互不串扰 |

### 🛡️ 去重与容错

| 机制 | 说明 |
|------|------|
| **会话唯一 ID** | 用 `chat-y{clicked_y}` 生成唯一会话 ID 做去重隔离，不依赖 OCR 昵称 |
| **消息哈希去重** | md5(msg)[:8] 同会话消息去重 |
| **snap_hash 切换检测** | 截图哈希比对，防止点 A 红点读到 B 聊天内容 |
| **OCR 缓存** | 缓存最近一次 OCR 结果，连续两次相同自动跳过，避免无新消息时重复入库 |
| **昵称底部提取** | 从 OCR 末两行（倒数第二行=昵称，最后一行=消息）识别，避免上方旧消息干扰 |
| **OCR 区域优化** | X 轴 35%-79% 锁定消息气泡区域，排除左侧聊天列表干扰 |
| **自适应扫描** | 有红点 1s 间隔，60s 无活动自动降速到 3s |
| **窗口自动恢复** | 微信窗口丢失后自动重新查找并激活 |

### 📊 Web 管理后台

| 功能 | 说明 |
|------|------|
| **登录认证** | 用户名+密码登录，基于 werkzeug 密码哈希，session 持久化 |
| **角色权限** | admin（全部权限）/ user（查看+编辑，无删除/用户管理权限） |
| **最后管理员保护** | 不能删除或降级最后一个 admin 账号 |
| **问题列表** | 关键词搜索 / 分类筛选 / 日期范围 / 状态筛选 / 分页浏览 |
| **三状态管理** | 待答复 / 已答复 / 不予处理，支持单条和批量变更 |
| **高时效性标记** | 问题可标记为"高时效"，独立筛选+徽章+建议更新警告 |
| **专业答复编辑** | 点击行弹窗编辑人工标准答案，支持「采用Coze回答」一键填入 |
| **分类管理** | 可视化添加/编辑/删除/启用禁用/排序 |
| **批量操作** | 全选复选框 + 底部固定操作栏，批量状态变更 + 批量删除（admin） |
| **数据统计卡片** | 总提问数 / 分类数 / 已答复 / 待答复 / 不予处理 |
| **Excel 导出** | 明细 Sheet + 分类汇总 Sheet，支持筛选条件过滤 |
| **知识库导出** | RAG 格式（问题/答案/分类三列），可直接导入知识库系统 |
| **自动备份** | 启动时日备份 + 每小时首次写入时备份，保留 30 天 |

---

## 工作原理

```
微信窗口 → 红点扫描（像素聚类）→ 点击未读会话 → 截图定位最新气泡
  → PaddleOCR 识别文字 → 群聊/私聊判断
  → [采集模式] 直接入库  /  [回复模式] 唤醒词匹配 → Coze AI 生成回复
  → 分类解析 → 发送纯净答复 → 数据入库 → 继续扫描
```

### 完整处理流程

```
main() 主循环
  │
  ├─ 恢复微信窗口
  ├─ click_red_dot() ─ 扫描左侧聊天列表红点
  │   ├─ find_red_dot_clusters() ─ 颜色阈值 + Y 轴聚类
  │   └─ reversed(clusters) ─ Y 最高优先点击
  │
  ├─ 无红点 → sleep → 继续
  │    └─ 连续 10 次无红点 → 强制检查当前聊天
  │
  └─ 有红点 → handle_current_chat()
       ├─ snap_hash 校验 ─ 聊天是否真实切换
       ├─ for 0..SAME_CHAT_MAX_PASS ─ 同聊多轮
       │   ├─ snap_hash 变化检测 ─ 有新消息则继续
       │   └─ process_one_message()
       │       ├─ get_latest_bubble_text() ─ 暗像素扫描 + PaddleOCR
       │       ├─ OCR 缓存检查 ─ 与上次相同则跳过
       │       ├─ 昵称提取（底部向上）
       │       ├─ 群聊唤醒词 / 私聊直接触发
       │       ├─ [采集] 直接入库
       │       └─ [回复] ai_reply() → send_mention_reply() / send_message()
       └─ 记录 OCR 缓存 + last_processed_chat_hash
```

---

## 环境要求

- **Windows 10/11**（依赖 win32 API 和 DWM）
- **微信桌面客户端**（3.x/4.x）
- **Python 3.8+**
- **Coze.cn 账号** + 已发布的 Bot（获取 API Token 和 Bot ID）

## 安装

```bash
# 1. 安装核心依赖
pip install pyautogui pyperclip pywin32 numpy Pillow paddleocr requests openpyxl flask

# 可选：快捷键退出
pip install keyboard

# 可选：OpenAI 后端
pip install openai

# 2. 创建配置文件
cp config.example.py config.py

# 3. 编辑 config.py，填入 Coze API Token 和 Bot ID
```

---

## 配置

编辑 `config.py`，完整配置项：

### 基础配置

```python
WAKE_WORD = "@管家"         # 唤醒词，群聊中以此词触发 AI
COZE_ENABLED = True          # True=AI回复模式 False=静默采集模式
AI_BACKEND = "coze"          # AI后端: "coze" / "openai" / "custom"
```

### Coze API

```python
COZE_API_TOKEN = "pat_xxxxxx"     # Coze API 令牌
BOT_ID = "76366xxxxxxxxxx"        # Coze Bot ID
COZE_API_BASE = "https://api.coze.cn"
```

### OCR 引擎

```python
PADDLEOCR_LANG = 'ch'                  # 识别语言: ch/en
PADDLEOCR_USE_ANGLE_CLS = True         # 文字方向校正
```

### 行为设置

```python
CHECK_INTERVAL = 1.0            # 主循环间隔（秒）
NO_RED_DOT_FALLBACK = 10        # 连续无红点 N 次后强制检查
COZE_TIMEOUT = 30               # Coze 请求超时（秒）
COZE_POLL_MAX = 30              # Coze 轮询最大次数（每次 2s）
```

### Web 后台

```python
WEB_HOST = "0.0.0.0"            # 0.0.0.0=局域网可访问 127.0.0.1=仅本机
WEB_PORT = 5000                 # Web 服务端口
WEB_PASSWORD = "admin123"       # 默认管理员密码，务必修改
```

---

## 使用

### 启动自动回复

```bash
python bot.py
```

1. 将微信窗口放在屏幕中间（避免遮挡或贴边）
2. 运行脚本，自动找到微信窗口
3. 机器人开始循环检测红点、识别消息、自动回复
4. 按 `-` 键安全退出

### 启动 Web 管理后台

```bash
python web_app.py
```

浏览器打开 `http://127.0.0.1:5000`，用配置的用户名密码登录。

---

## 双模式详解

### 回复模式（`COZE_ENABLED = True`）

| 场景 | 条件 | 行为 |
|------|------|------|
| 群聊 @唤醒 | 有昵称 + 有唤醒词 | 调用 Coze AI → @发送者回复 → 分类入库 |
| 群聊 无@ | 有昵称 + 无唤醒词 | 跳过 |
| 私聊 | 无昵称 | 调用 Coze AI → 直接回复 → 入库 |

### 采集模式（`COZE_ENABLED = False`）

| 场景 | 行为 |
|------|------|
| 所有消息 | 直接入库，不调用 AI，不发送回复 |

适用于项目初期的用户问题收集阶段，节省 Coze token。

---

## Coze Bot 配置指南

### 回复格式约定

Coze Bot 的人设 Prompt 中**必须约定**回复格式为 `分类名：答复内容`（中文冒号），系统会自动解析分类并剥离后发送。

**正确示例**：

```
用户: @管家 你好 我想咨询离婚

Coze 返回: 婚姻家庭法律咨询：您好😊我们提供离婚协议、财产分割、
         子女抚养权等法律服务，可以为您匹配专业律师。

实际发送到微信: 您好😊我们提供离婚协议、财产分割...
入库分类: 婚姻家庭法律咨询
```

**格式要点**：
- 使用中文冒号 `：` 或英文冒号 `:` 分隔
- 分类名控制在 20 字以内，不含句号/感叹号
- 分类名要稳定一致，同一类问题用同一分类名
- Web 后台分类管理中添加同名分类后，新提问会自动匹配

---

## Web 管理后台

| 页面/功能 | 说明 |
|-----------|------|
| **登录页** | 用户名+密码登录，默认管理员 admin/admin123 |
| **问题列表** | 主页面，展示所有提问，支持多条件筛选和分页 |
| **编辑弹窗** | 点击行弹出，可编辑专业答复、切换状态、标记高时效、删除（admin） |
| **分类管理** | 弹窗维护分类列表，支持启用/禁用/排序 |
| **用户管理** | admin 专属，添加/修改/删除用户，每行独立保存按钮 |
| **批量操作** | 底部固定栏，选中问题后批量改状态/删除 |
| **Excel 导出** | 按当前筛选条件导出，含明细+分类汇总双 Sheet |
| **知识库导出** | RAG 格式导出，可直接用于知识库系统 |

### 角色权限

| 操作 | admin | user |
|------|:-----:|:----:|
| 查看问题列表 | ✅ | ✅ |
| 编辑答复/状态 | ✅ | ✅ |
| 标记高时效 | ✅ | ✅ |
| 导出 Excel | ✅ | ✅ |
| 删除问题 | ✅ | ❌ |
| 分类管理 | ✅ | ❌ |
| 用户管理 | ✅ | ❌ |
| 批量删除 | ✅ | ❌ |

---

## 项目结构

```
screen-ocr-sim-agent/
├── bot.py               # 主程序：GUI自动化 + OCR + AI回复
├── web_app.py           # Web 管理后台 (Flask 单文件)
├── question_stats.py    # 问题统计 & SQLite 数据库模块
├── config.example.py    # 配置文件模板
├── config.py            # 配置文件（.gitignore）
├── questions.db         # SQLite 数据库（.gitignore）
├── backups/             # 自动备份目录（.gitignore）
├── exports/             # Excel 导出目录（.gitignore）
├── README.md            # 项目文档
├── CLAUDE.md            # Claude Code 开发指引
└── .gitignore
```

### 模块职责

| 文件 | 行数 | 职责 |
|------|------|------|
| `bot.py` | ~1000 | GUI 自动化、OCR、AI 调用、消息发送、主循环 |
| `question_stats.py` | ~800 | SQLite 数据库操作、分类聚类、统计查询、备份 |
| `web_app.py` | ~1200 | Flask 应用、API 路由、内嵌 HTML/CSS/JS 前端 |
| `config.example.py` | ~70 | 配置项模板 |

---

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 语言 | Python 3.8+ | 主程序 |
| GUI 自动化 | pyautogui / pyperclip / pywin32 | 鼠标点击、键盘输入、剪贴板、窗口操控 |
| 截图 | PrintWindow(PW_RENDERFULLCONTENT) | 微信 4.x GPU 渲染兼容 |
| OCR | PaddleOCR (PP-OCRv5) | 中文文字识别 |
| AI 后端 | Coze API v3 / OpenAI 兼容 | 消息回复生成 |
| 数据库 | SQLite (WAL 模式) | 问题持久化、自动备份 |
| Web 后台 | Flask + 内嵌 HTML/CSS/JS | 单文件零外部前端依赖 |
| Excel | openpyxl | 明细 + 汇总 + 知识库导出 |
| 图像处理 | Pillow / numpy | 截图裁剪、红点像素检测、文字区域定位 |
| 密码 | werkzeug.security | 密码哈希与验证 |

---

## 常见问题

<details>
<summary><b>PaddleOCR 初始化报错 / 崩溃</b></summary>

已禁用 oneDNN（`enable_mkldnn=False` + 环境变量三重禁用）。如仍有问题：
```bash
pip install paddlepaddle==3.0.0 paddleocr==2.9.1
```
</details>

<details>
<summary><b>Coze API 返回 4000 "Missing required parameters"</b></summary>

`additional_messages` 中每条消息需包含 `"type": "question"` 字段。当前版本已修复。
</details>

<details>
<summary><b>截图黑屏 / 无法识别微信窗口</b></summary>

- 已使用 `PrintWindow(PW_RENDERFULLCONTENT)` 兼容微信 4.x GPU 渲染
- 确保微信窗口未被最小化、未被完全遮挡
- 多显示器确保微信在主显示器上
</details>

<details>
<summary><b>分类显示"未分类"</b></summary>

1. 确保 Coze Bot 回复格式为 `分类名：答复内容`（中文冒号）
2. 在 Web 后台 → 分类管理 → 添加对应的分类名
3. 将 `LOG_LEVEL` 设为 `DEBUG` 查看 `[Stats]` 日志
</details>

<details>
<summary><b>OCR 识别内容混乱/昵称识别错误</b></summary>

已优化 OCR 区域（X 轴 35%-79%）避免扫描侧边栏，昵称改为底部向上提取（倒数第二行）。
确保微信窗口放在屏幕中间，不要贴边或最大化。
</details>

<details>
<summary><b>同一消息被重复入库</b></summary>

三重防护：会话唯一 ID 去重 + OCR 缓存去重 + 消息哈希去重。如果仍有重复，检查 `LOG_LEVEL=DEBUG` 日志。
</details>

<details>
<summary><b>数据库误删了怎么办</b></summary>

`backups/` 目录自动备份，复制最近一份：
```powershell
copy backups\questions_20260515.db questions.db
```
</details>

<details>
<summary><b>如何添加自定义 AI 后端</b></summary>

```python
def my_backend(content, chat_user_id):
    # 返回 (reply_text, success)
    return "分类名：答复内容", True

# 在 bot.py ai_backends 字典中注册
ai_backends = {
    "coze": _coze_reply,
    "openai": _openai_reply,
    "custom": my_backend,
}
```
</details>

---

## 免责声明

- 本项目仅供学习和研究使用
- 使用 GUI 自动化方式与微信交互，不涉及逆向工程或协议破解
- 使用者需自行承担使用风险，包括但不限于微信账号被限制等
- 请遵守微信用户协议及相关法律法规

## License

MIT
