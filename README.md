# 微信智能机器人 

基于 Windows GUI 自动化 + OCR + AI 大模型的微信自动回复机器人，附带问题统计与 Web 管理后台。

无需 Hook、无需注入、无需逆向微信协议 —— 截图识别红点定位未读消息，OCR 提取文字，调用 AI 后端生成回复，模拟键盘输入自动发送。所有用户提问自动存入 SQLite 数据库，Web 后台支持分类管理、专业答复编辑、状态筛选、Excel 导出。

## 特性

### 🤖 自动回复
- **AI 后端可插拔** — `ai_backends` 字典注册，`AI_BACKEND` 一键切换 Coze / OpenAI / 自定义
- **红点聚类扫描** — Y 轴像素聚类，同时检测多个未读会话，逐个击破
- **PaddleOCR 识别** — PP-OCRv5 中文识别，动态定位最新气泡，无需固定坐标
- **真实 @提醒** — 群聊中通过微信 @面板真实 @发送者，非纯文本模拟
- **昵称提取** — 从 OCR 结果自动提取发送者昵称，支持企业微信后缀清理
- **Coze 分类前缀解析** — 自动从 Coze 回复中提取分类标签（如"二手房出租咨询"），剥离后发送纯净答复
- **跨群去重** — 按 OCR 提取的昵称隔离不同聊天的消息去重
- **异常消息快速熔断** — 图片/语音/视频等无文字消息，stall+2 双倍跳过
- **自适应扫描** — 有红点 1s 间隔，60s 无活动自动降速到 3s
- **熔断保护** — AI 请求总超时 60s + 连续 5 次失败自动退避
- **会话隔离** — 不同聊天使用独立的 AI 上下文，互不串扰
- **同聊多轮** — 同一聊天最多 4 轮连续处理，应对多人同时提问
- **GPU 渲染兼容** — PrintWindow(PW_RENDERFULLCONTENT) 支持微信 4.x

### 📊 问题统计 & Web 后台
- **SQLite 持久化** — 零依赖单文件数据库，所有提问自动入库
- **分类管理** — 独立 `categories` 表，Web 界面可视化维护，支持启用/禁用/排序
- **Coze 分类自动匹配** — 冒号解析 + 管理分类子串匹配双保险
- **三状态管理** — 待答复 / 已答复 / 不予处理，支持筛选与批量操作
- **专业答复编辑** — Web 界面填写人工标准答案，支持「采用Coze回答」一键填入
- **每日自动备份** — 启动时日备份 + 每小时首次写入时备份，保留 30 天
- **密码保护** — Web 后台登录认证，支持局域网多电脑协同编辑
- **Excel 导入导出** — 带筛选条件导出明细 + 分类汇总两个 Sheet

## 工作原理

```
微信窗口 → 红点检测(像素聚类) → 点击未读会话 → 截图+OCR识别消息
    → 唤醒词匹配 → AI后端获取回复 → 分类解析 → 发送纯净答复
    → 数据入库(questions.db) → 返回聊天列表 → 循环
```

| 模块 | 技术细节 |
|------|----------|
| 窗口发现 | 多策略查找（类名 / 备选类名 / 标题枚举），DWM 精确获取矩形，DPI 感知 |
| 红点检测 | pyautogui 截取左侧聊天列表 4%-50% 水平 × 12%-88% 垂直区域，双模式颜色阈值 + Y 轴聚类（25px 间距合并） |
| 消息定位 | 像素行扫描从底部向上找到最暗行，向上扩展连续文字块，百分比坐标适配任意窗口大小 |
| OCR 识别 | PaddleOCR (PP-OCRv5)，中文识别，enable_mkldnn=False，启动时预加载 |
| AI 后端 | 可插拔架构：Coze API v3（chat → retrieve → message/list 三步流程）/ OpenAI 兼容接口 / 自定义 |
| 分类处理 | Coze 回复前缀冒号解析 → 管理分类子串匹配双保险 → 发送时剥离分类标签 |
| 消息发送 | pyperclip 粘贴 + pyautogui 模拟键盘，群聊通过 @面板真实 @发送者 |
| 去重熔断 | 按昵称隔离去重 + 图像 hash 变化检测 + stall 跟踪（3 次跳过，处理成功清零）+ 连续失败退避 |
| 数据存储 | SQLite (WAL 模式)，questions + question_clusters + categories 三表，按小时/日自动备份 |
| Web 后台 | Flask 单文件应用，内嵌 HTML/JS，支持筛选/分页/编辑/分类管理/Excel 导出 |

## 环境要求

- **Windows 10/11**（依赖 win32 API 和 DWM）
- **微信桌面客户端**（3.x/4.x）
- **Python 3.8+**

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
COZE_API_TOKEN = "pat_xxxxxx"
BOT_ID = "76366xxxxxxxxxx"
COZE_API_BASE = "https://api.coze.cn"

# ==================== OCR ====================
PADDLEOCR_LANG = 'ch'
PADDLEOCR_USE_ANGLE_CLS = True

# ==================== 行为设置 ====================
CHECK_INTERVAL = 1.0
NO_RED_DOT_FALLBACK = 10
COZE_TIMEOUT = 30
COZE_POLL_MAX = 30

# ==================== 问题统计 ====================
STATS_DB_PATH = "questions.db"

# ==================== Web 后台 ====================
WEB_HOST = "0.0.0.0"             # 0.0.0.0=局域网可访问, 127.0.0.1=仅本机
WEB_PORT = 5000
WEB_PASSWORD = "admin123"        # ⚠️ 务必修改！
```

> **获取 Coze API Token**：前往 [coze.cn](https://www.coze.cn) 创建 Bot → 发布 → API 页面获取令牌。

## 使用

### 启动自动回复（7×24 常驻）

```bash
python bot.py
```

1. 确保微信已登录并打开
2. 运行脚本，自动找到微信窗口
3. 机器人开始循环检测红点、识别消息、自动回复
4. 按 `-` 键安全退出

### 启动 Web 管理后台（按需）

```bash
python web_app.py
```

浏览器打开 `http://127.0.0.1:5000`，输入配置的密码即可访问。

Web 后台功能：
- **问题列表** — 关键词搜索、分类筛选、时间范围、状态筛选、分页
- **编辑答复** — 点击任意行打开编辑弹窗，填写专业答复，支持「采用Coze回答」一键填入
- **状态管理** — 待答复 / 已答复 / 不予处理，支持批量应用到整个分类
- **分类管理** — 可视化添加/编辑/删除/启用禁用分类
- **Excel 导出** — 当前筛选条件导出明细 Sheet + 分类汇总 Sheet

### 添加自定义 AI 后端

```python
def my_backend(content, chat_user_id):
    return reply_text, True

ai_backends = {
    "coze": _coze_reply,
    "openai": _openai_reply,
    "custom": my_backend,
}
```

## 项目结构

```
screen-ocr-sim-agent/
├── bot.py               # 主程序 v1.0.31
├── web_app.py           # Web 管理后台
├── question_stats.py    # 问题统计 & 数据库模块
├── config.example.py    # 配置文件模板
├── config.py            # 配置文件（.gitignore）
├── questions.db         # SQLite 数据库（.gitignore）
├── backups/             # 自动备份目录（.gitignore）
├── exports/             # Excel 导出目录（.gitignore）
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
</details>

<details>
<summary><b>分类显示"未分类"</b></summary>

1. 确保 Coze Bot 的回复格式为 "分类名：答复内容"（中文冒号）
2. 在 Web 后台 → 分类管理 → 添加对应的分类名，系统会自动子串匹配
3. 将 `LOG_LEVEL` 设为 `DEBUG` 查看 `[Stats]` 日志排查
</details>

<details>
<summary><b>两个群相同问题只回了一个</b></summary>

v1.0.31 已修复，去重改为按聊天昵称隔离，不同群互不干扰。
</details>

<details>
<summary><b>数据库误删了怎么办</b></summary>

`backups/` 目录每天自动备份，复制最近一份回来：
```powershell
copy backups\questions_20260512.db questions.db
```
</details>

## 免责声明

- 本项目仅供学习和研究使用
- 使用 GUI 自动化方式与微信交互，不涉及逆向工程或协议破解
- 使用者需自行承担使用风险，包括但不限于微信账号被限制等
- 请遵守微信用户协议及相关法律法规

## License

MIT
