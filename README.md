# 微信智能机器人 v1.0.32

基于 Windows GUI 自动化 + PaddleOCR + **Coze AI 大模型**的微信自动回复机器人，附带问题统计与 Web 管理后台。

无需 Hook、无需注入、无需逆向微信协议 —— 截图识别红点定位未读消息，OCR 提取文字，调用 Coze Bot 生成回复，模拟键盘输入自动发送。所有用户提问自动存入 SQLite 数据库，Web 后台支持分类管理、专业答复编辑、状态筛选、知识库导出。

## Coze 依赖

本项目**默认依赖 Coze 平台**作为 AI 后端。你需要：

1. 前往 [coze.cn](https://www.coze.cn) 创建 Bot 并配置人设/知识库/插件
2. 发布 Bot → 在 API 页面获取 `API Token` 和 `Bot ID`
3. 填入 `config.py` 中对应的配置项

```
用户提问 → OCR 识别 → 发送到 Coze Bot → Coze 返回分类答复 → 分类入库 + 纯净答复发送
```

## 特性

### 🤖 自动回复
- **AI 后端可插拔** — `ai_backends` 字典注册，`AI_BACKEND` 一键切换 Coze / OpenAI / 自定义，默认 Coze
- **红点聚类扫描** — Y 轴像素聚类，同时检测多个未读会话，逐个击破（Y 最高优先）
- **PaddleOCR 识别** — PP-OCRv5 中文识别，动态定位最新气泡，无需固定坐标
- **真实 @提醒** — 群聊中通过微信 @面板真实 @发送者，非纯文本模拟
- **昵称提取** — 从 OCR 结果自动提取发送者昵称，支持企业微信后缀清理
- **Coze 分类前缀解析** — 自动从 Coze 回复中提取分类标签，剥离后发送纯净答复
- **跨群去重** — 按 OCR 提取的昵称隔离不同聊天的消息去重
- **异常消息快速熔断** — 图片/语音/视频等无文字消息，stall+2 双倍跳过
- **自适应扫描** — 有红点 1s 间隔，60s 无活动自动降速到 3s
- **熔断保护** — AI 请求总超时 60s + 连续 5 次失败自动退避
- **会话隔离** — 不同聊天使用独立的 AI 上下文，互不串扰
- **同聊多轮** — 同一聊天最多 4 轮连续处理
- **GPU 渲染兼容** — PrintWindow(PW_RENDERFULLCONTENT) 支持微信 4.x

### 📊 问题统计 & Web 后台
- **登录认证** — 密码保护，支持局域网多电脑协同
- **问题列表** — 关键词搜索 / 分类筛选 / 时间范围 / 状态筛选 / 分页浏览
- **分类管理** — 独立 `categories` 表，可视化添加/编辑/删除/启用禁用/排序
- **三状态管理** — 待答复 / 已答复 / 不予处理，支持单条及分类批量操作
- **专业答复编辑** — 点击任意行弹出编辑窗，填写人工标准答案，支持「采用Coze回答」一键填入
- **数据统计卡片** — 总提问数 / 分类数 / 已答复 / 待答复 / 不予处理 一目了然
- **双格式 Excel 导出** — 明细 Sheet + 分类汇总 Sheet，支持筛选条件
- **知识库导出** — RAG 格式（问题 / 答案 / 分类），直接用于知识库系统
- **Coze 分类双保险** — 冒号解析 + 管理分类子串匹配
- **SQLite 持久化** — 零依赖单文件数据库，WAL 模式
- **自动备份** — 启动时日备份 + 每小时首次写入时备份，保留 30 天

## Coze Bot 配置指南

### 回复格式约定

Coze Bot 的人设 Prompt 中**必须约定**回复格式为 `分类名：答复内容`（中文冒号），系统会自动解析分类并剥离后发送。

**正确示例**：

```
用户: @管家 你好 我想咨询离婚

Coze 返回: 婚姻家庭法律咨询：您好😊我们提供离婚协议、财产分割、子女抚养权等法律服务，可以为您匹配专业律师。您可以留下联系方式，我安排律师回电，或拨打400-XXX-XXXX详询哦

实际发送到微信: 您好😊我们提供离婚协议、财产分割...
入库分类: 婚姻家庭法律咨询
```

```
用户: @管家 你好 我要投诉律师服务

Coze 返回: 投诉处理：非常抱歉给您带来不便！请您描述具体问题，我会立即记录并转交律所质量管理部处理。您也可以直接拨打客服热线400-XXX-XXXX，我们会优先为您解决🙏

实际发送到微信: 非常抱歉给您带来不便！请您描述具体问题...
入库分类: 投诉处理
```

```
用户: @管家 你们所有哪些业务

Coze 返回: 业务咨询：我们提供婚姻家事、刑事辩护、合同纠纷、知识产权、劳动仲裁、企业法律顾问等一站式法律服务。您有具体需求我可以详细为您介绍～

实际发送到微信: 我们提供婚姻家事、刑事辩护...
入库分类: 业务咨询
```

**格式要点**：
- 使用**中文冒号** `：` 或英文冒号 `:` 分隔
- 分类名控制在 20 字以内，不含句号/感叹号
- 分类名要稳定一致，同一类问题用同一分类名
- Web 后台分类管理中添加同名分类后，新提问会自动匹配

## Web 管理后台

```bash
python web_app.py
```

浏览器打开 `http://127.0.0.1:5000`，输入 `config.py` 中 `WEB_PASSWORD` 配置的密码。

### 功能概览

| 功能 | 说明 |
|------|------|
| 📊 统计卡片 | 顶部展示总提问数、分类数、已答复/待答复/不予处理数量 |
| 🔍 多条件筛选 | 关键词搜索 + 分类下拉 + 日期范围 + 状态筛选 |
| ✏️ 编辑答复 | 点击行 → 弹窗编辑专业答复 → 保存，支持「采用Coze回答」一键填入原始AI回复 |
| 📂 分类管理 | 弹窗中可视化维护分类（添加/编辑/启用禁用/删除/排序） |
| 🔄 状态切换 | 单条修改：待答复 / 已答复 / 不予处理；支持一键批量应用到整个分类 |
| 📥 导出 Excel | 按当前筛选条件导出，包含「问题统计」明细 Sheet 和「分类汇总」Sheet |
| 📚 知识库导出 | 导出 RAG 格式（问题/答案/分类三列），可直接导入知识库系统 |
| 🔒 登录认证 | 密码保护，配置 `0.0.0.0` 可在局域网其他电脑协同编辑 |
| 💾 自动备份 | 启动时日备份 + 每小时首次写入时备份，`backups/` 目录保留 30 天 |

## 工作原理

```
微信窗口 → 红点检测(像素聚类) → 点击未读会话 → 截图+OCR识别消息
    → 唤醒词匹配 → Coze获取回复 → 分类解析 → 发送纯净答复
    → 数据入库(questions.db) → 回到顶部重新扫描 → 循环
```

| 模块 | 技术细节 |
|------|----------|
| 窗口发现 | 多策略查找（类名 / 备选类名 / 标题枚举），DWM 精确获取矩形，DPI 感知 |
| 红点检测 | pyautogui 截取左侧聊天列表 4%-50% 水平 × 12%-88% 垂直区域，双模式颜色阈值 + Y 轴聚类（25px 间距合并），Y 最高优先点击 |
| 消息定位 | 像素行扫描从底部向上找到最暗行，向上扩展连续文字块，百分比坐标适配任意窗口大小 |
| OCR 识别 | PaddleOCR (PP-OCRv5)，中文识别，enable_mkldnn=False，启动时预加载 |
| AI 后端 | 可插拔架构：Coze API v3（chat → retrieve → message/list 三步流程）/ OpenAI 兼容接口 / 自定义 |
| 分类处理 | Coze 回复前缀冒号解析 → 管理分类子串匹配双保险 → 发送时剥离分类标签 |
| 消息发送 | pyperclip 粘贴 + pyautogui 模拟键盘，群聊通过 @面板真实 @发送者 |
| 去重熔断 | 三层防护：按昵称隔离去重 + 图像 hash 变化检测 + stall 跟踪（成功=99永久标记，失败+2双倍跳过）+ ±8px Y 容差 |
| 数据存储 | SQLite (WAL 模式)，questions + question_clusters + categories 三表，按小时/日自动备份 |
| Web 后台 | Flask 单文件应用，内嵌 HTML/CSS/JS，零外部前端依赖 |

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 语言 | Python 3.8+ | 主程序 |
| GUI 自动化 | pyautogui / pyperclip / pywin32 | 鼠标点击、键盘输入、剪贴板粘贴、窗口操控 |
| 截图 | PrintWindow(PW_RENDERFULLCONTENT) | 微信 4.x GPU 渲染截图 |
| OCR | PaddleOCR (PP-OCRv5) | 中文文字识别 |
| AI 后端 | Coze API v3 / OpenAI 兼容 | 消息回复生成 |
| 数据库 | SQLite (WAL 模式) | 问题持久化、自动备份 |
| Web 后台 | Flask + 内嵌 HTML/CSS/JS | 单文件零外部前端依赖 |
| Excel | openpyxl | 明细 + 汇总 + 知识库导出 |
| 图像处理 | Pillow / numpy | 截图裁剪、红点像素检测、文字区域定位 |

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

# 可选：语义聚类（Coze分类失败时兜底）
pip install sentence-transformers

# 2. 创建配置文件
cp config.example.py config.py
```

## 配置

编辑 `config.py`：

```python
# ==================== 唤醒词 ====================
WAKE_WORD = "@管家"

# ==================== AI 后端 ====================
AI_BACKEND = "coze"              # "coze" / "openai" / "custom"

# ==================== Coze API ====================
COZE_API_TOKEN = "pat_xxxxxx"    # Coze API 令牌
BOT_ID = "76366xxxxxxxxxx"       # Coze Bot ID
COZE_API_BASE = "https://api.coze.cn"

# ==================== OCR ====================
PADDLEOCR_LANG = 'ch'
PADDLEOCR_USE_ANGLE_CLS = True

# ==================== 行为设置 ====================
CHECK_INTERVAL = 1.0             # 主循环间隔（秒）
NO_RED_DOT_FALLBACK = 10         # 连续无红点多少次后强制检查
COZE_TIMEOUT = 30                # Coze 请求超时（秒）
COZE_POLL_MAX = 30               # Coze 轮询最大次数

# ==================== 问题统计 ====================
STATS_DB_PATH = "questions.db"

# ==================== Web 后台 ====================
WEB_HOST = "0.0.0.0"             # 0.0.0.0=局域网可访问, 127.0.0.1=仅本机
WEB_PORT = 5000
WEB_PASSWORD = "admin123"        # ⚠️ 务必修改！
WEB_EXPORT_PASSWORD = ""         # 导出文件密码（留空不加密）
```

## 使用

### 启动自动回复（7×24 常驻）

```bash
python bot.py
```

1. 将微信窗口放在**屏幕中间**（避免被遮挡或贴边）
2. 运行脚本，自动找到微信窗口
3. 机器人开始循环检测红点、识别消息、自动回复
4. 按 `-` 键安全退出

### 启动 Web 管理后台（按需）

```bash
python web_app.py
```

浏览器打开 `http://127.0.0.1:5000`。

### 添加自定义 AI 后端

```python
def my_backend(content, chat_user_id):
    # 返回 (reply_text, success)
    return "分类名：答复内容", True

ai_backends = {
    "coze": _coze_reply,
    "openai": _openai_reply,
    "custom": my_backend,
}
```

## 项目结构

```
screen-ocr-sim-agent/
├── bot.py               # 主程序 v1.0.32
├── web_app.py           # Web 管理后台
├── question_stats.py    # 问题统计 & 数据库模块
├── config.example.py    # 配置文件模板
├── config.py            # 配置文件（.gitignore）
├── questions.db         # SQLite 数据库（.gitignore）
├── backups/             # 自动备份目录（.gitignore）
├── exports/             # Excel 导出目录（.gitignore）
├── CLAUDE.md            # Claude Code 指引
├── CHANGELOG.md         # 版本变更记录
├── README.md
└── .gitignore
```

## 常见问题

<details>
<summary><b>PaddleOCR 初始化报错 / 崩溃</b></summary>

已禁用 oneDNN（`enable_mkldnn=False` + 环境变量三重禁用）。如仍有问题：
```bash
pip install paddlepaddle==3.0.0 paddleocr==2.9.1
```
</details>

<details>
<summary><b>截图黑屏 / 无法识别微信窗口</b></summary>

- 已使用 `PrintWindow(PW_RENDERFULLCONTENT)` 兼容微信 4.x GPU 渲染
- 确保微信窗口未被最小化、未被完全遮挡
- 多显示器确保微信在主显示器上
- 将微信窗口放在屏幕中间位置
</details>

<details>
<summary><b>分类显示"未分类"</b></summary>

1. 确保 Coze Bot 的回复格式为 "分类名：答复内容"（中文冒号）
2. 在 Web 后台 → 分类管理 → 添加对应的分类名，系统会自动子串匹配
3. 将 `LOG_LEVEL` 设为 `DEBUG` 查看 `[Stats]` 日志排查
</details>

<details>
<summary><b>多个红点只处理了一个</b></summary>

v1.0.32 已修复。确保微信窗口放在屏幕中间，左侧边栏不被遮挡。红点检测依赖屏幕截图，窗口贴边或有遮挡物会导致漏检。
</details>

<details>
<summary><b>同一消息被回复多次</b></summary>

v1.0.32 已修复。
</details>

<details>
<summary><b>Web 端按时间搜索报错</b></summary>

v1.0.32 已修复 `float(date_string)` 的 bug。
</details>

<details>
<summary><b>数据库误删了怎么办</b></summary>

`backups/` 目录每天自动备份，复制最近一份回来：
```powershell
copy backups\questions_20260512.db questions.db
```
</details>

<details>
<summary><b>如何让 Coze 回复更精准</b></summary>

1. 在 Coze Bot 人设中明确角色、服务范围、回复格式
2. 添加上下文知识库（常见问题 FAQ）
3. 配置分类名须稳定一致，避免同一类问题使用不同分类名
4. 定期检查 Web 后台统计 → 查看各分类命中情况 → 调整 Prompt
</details>

## 免责声明

- 本项目仅供学习和研究使用
- 使用 GUI 自动化方式与微信交互，不涉及逆向工程或协议破解
- 使用者需自行承担使用风险，包括但不限于微信账号被限制等
- 请遵守微信用户协议及相关法律法规

## License

MIT
