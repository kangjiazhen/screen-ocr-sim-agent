# 微信智能机器人 v1.0 — 版本说明

## 项目概述

基于屏幕 OCR + AI 后端的微信智能机器人，通过截取微信 PC 客户端屏幕画面，利用 PaddleOCR 识别聊天消息，匹配唤醒词后自动调用 AI 获取回复并回传至微信窗口。无需 Hook、无需 API 接入微信，纯屏幕级自动化操作。

## 版本标识

- **版本号**: 1.0
- **分支**: v29
- **基础提交**: 5685cfb
- **核心文件**: `bot.py`（主程序）、`config.py`（配置）、`question_stats.py`（统计模块）、`web_app.py`（Web 后台）

---

## 核心功能

### 1. 屏幕 OCR 消息识别
- **OCR 引擎**: PaddleOCR（PP-OCRv5 模型，中文识别）
- **截图方式**: `PrintWindow(PW_RENDERFULLCONTENT)` 支持微信 4.x GPU 渲染
- **动态消息定位**: 像素行扫描 + 百分比坐标，自动定位最新气泡
- **发送者昵称提取**: OCR 捕获气泡上方昵称，内置企业微信后缀清理
- **图像 hash 去重**: 通过区域 MD5 hash 变化检测判断是否为新消息

### 2. 红点检测与多会话并发
- **红点扫描**: 对微信左侧导航栏区域进行像素级红色检测
- **Y 轴聚类**: 将红点像素按 Y 坐标聚类，识别多个未读会话，逐个击破
- **同聊多轮**: 同一聊天内最多 4 轮依次处理
- **stall 跟踪去重**: 3 次无消息自动跳过（异常消息双倍跳过）
- **自适应扫描间隔**: 有红点时 1s，60 秒无活动降速至 3s

### 3. AI 后端可插拔
- **后端注册表**: `ai_backends` 字典注册，`AI_BACKEND` 配置一键切换
- **内置 Coze 后端**: Coze API v3 三步流程（chat → retrieve → message/list）
  - 总超时 60s 熔断
  - 连续 5 次失败退避
  - 会话隔离（user_id 基于红点 Y 坐标）
- **OpenAI 兼容后端**: 支持 GPT / DeepSeek / Ollama 等 OpenAI 兼容 API
- **自定义后端模板**: 预留接口，按签名 `(content, chat_user_id) -> (reply_text, success)` 扩展

### 4. 真实 @提醒
- **方式**: 模拟微信 @ 面板 — 在输入框按 `@` → 粘贴昵称 → 回车选中 → 粘贴回复 → 发送
- **非纯文本**: 群聊中真实 @提醒发送者，而非文字模拟

### 5. 唤醒词过滤
- 通过正则匹配唤醒词，忽略不相关聊天消息
- 支持唤醒词在消息中任意位置匹配

### 6. 问题统计 & RAG 知识库（v30 新增）
- **SQLite 持久化**: 所有提问自动记录到 `questions.db`
- **智能分类**: Coze 回复中的分类前缀作为主聚类依据
- **语义相似度兜底**: 无分类前缀时，通过嵌入模型（BAAI/bge-small-zh-v1.5）做相似度匹配
- **聚类系统**: 相似问题自动归入同一 cluster，支持 centroid 更新
- **数据库自动备份**: 每日/每小时自动备份，30 天过期清理

### 7. Web 管理后台
- **技术栈**: Flask + SQLite + 内嵌 HTML/CSS/JS（单文件零依赖前端）
- **功能**:
  - 问题列表 + 多条件筛选（关键词/分类/日期/状态）
  - 分页浏览
  - 编辑专业答复（人工补充，存入 RAG 知识库）
  - 三状态管理: 待答复 / 已答复 / 不予处理
  - 批量应用到同一分类
  - 分类管理（CRUD）
  - Excel 导出（含分类汇总 sheet）
- **访问**: `http://0.0.0.0:5000`（局域网可访问）
- **安全**: 密码登录保护

---

## 系统架构

```
┌──────────────────────────────────────────────────────┐
│                    微信 PC 客户端                       │
│  ┌─────────────┐  ┌──────────────────────────────┐   │
│  │ 导航栏红点区域 │  │       聊天消息区域              │   │
│  └──────┬──────┘  └──────────────┬───────────────┘   │
└─────────┼────────────────────────┼───────────────────┘
          │                        │
    ┌─────▼─────┐          ┌──────▼──────┐
    │ 红点检测模块 │          │  OCR 识别模块 │
    │ (像素扫描)  │          │ (PaddleOCR)  │
    └─────┬─────┘          └──────┬──────┘
          │                        │
          └────────┬───────────────┘
                   │
          ┌────────▼────────┐
          │   消息处理引擎     │
          │  (bot.py main)   │
          └────────┬────────┘
                   │
     ┌─────────────┼─────────────┐
     │             │             │
┌────▼────┐  ┌─────▼─────┐  ┌───▼────┐
│ AI 后端  │  │ 问题统计   │  │ Web 后台│
│ 可插拔   │  │ SQLite   │  │ Flask  │
└────┬────┘  └───────────┘  └────────┘
     │
┌────▼────┐
│  微信发送 │
│ @提醒/回复│
└─────────┘
```

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `bot.py` | 主程序，包含所有核心逻辑（红点检测、OCR、AI 调用、消息收发） |
| `config.py` | 配置文件（需从 `config.example.py` 复制后填写） |
| `config.example.py` | 配置模板，含完整注释说明 |
| `question_stats.py` | 问题统计模块，SQLite 持久化 + 聚类 + 语义匹配 |
| `web_app.py` | Web 管理后台，Flask 单文件应用 |
| `.gitignore` | Git 忽略规则 |

---

## 环境依赖

### 运行环境
- **操作系统**: Windows 10/11（依赖 Win32 API）
- **Python**: 3.10+
- **微信版本**: 微信 PC 客户端 4.x

### Python 依赖
```
paddleocr          # PP-OCRv5 中文 OCR
paddlepaddle       # PaddlePaddle 推理引擎
pyautogui          # 鼠标键盘自动化
pyperclip          # 剪贴板操作
pywin32            # Win32 API（窗口操作、截图）
Pillow             # 图像处理
numpy              # 数值计算
requests           # HTTP 请求（Coze API）
keyboard            # 热键监听（可选，退出键）
flask              # Web 后台
openpyxl           # Excel 导出
sentence-transformers  # 语义相似度（可选，问题去重）
openai             # OpenAI SDK（可选，OpenAI 后端）
```

### 外部服务
- **Coze API**: 需配置 API Token + Bot ID（AI_BACKEND="coze" 时）
- **OpenAI 兼容 API**: 需配置 API Key + Base URL + Model（AI_BACKEND="openai" 时）

---

## 快速开始

1. **复制配置文件**
   ```bash
   copy config.example.py config.py
   ```

2. **编辑 config.py**，填入：
   - `WAKE_WORD`: 唤醒词（如 `@管家小康`）
   - `COZE_API_TOKEN` + `BOT_ID`: Coze API 凭据
   - 或切换 `AI_BACKEND="openai"` 并填入 OpenAI 配置

3. **安装依赖**
   ```bash
   pip install paddleocr paddlepaddle pyautogui pyperclip pywin32 Pillow numpy requests keyboard flask openpyxl
   ```

4. **启动机器人**
   ```bash
   python bot.py
   ```

5. **(可选) 启动 Web 后台**
   ```bash
   python web_app.py
   ```
   浏览器打开 `http://127.0.0.1:5000`，密码见 config.py 中 `WEB_PASSWORD`

---

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `WAKE_WORD` | `@管家小康` | 唤醒词，消息包含此词才会触发 AI |
| `AI_BACKEND` | `coze` | AI 后端选择: coze / openai |
| `COZE_API_TOKEN` | - | Coze API 访问令牌 |
| `BOT_ID` | - | Coze Bot ID |
| `CHECK_INTERVAL` | `1.0` | 活跃扫描间隔（秒） |
| `NO_RED_DOT_FALLBACK` | `10` | 连续无红点 N 次后强制检查当前聊天 |
| `COZE_TIMEOUT` | `30` | Coze 单次请求超时（秒） |
| `COZE_POLL_MAX` | `30` | Coze 轮询最大次数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `PADDLEOCR_LANG` | `ch` | OCR 语言 |
| `STATS_DB_PATH` | `questions.db` | 统计数据库路径 |
| `WEB_HOST` | `0.0.0.0` | Web 后台监听地址 |
| `WEB_PORT` | `5000` | Web 后台端口 |
| `WEB_PASSWORD` | `admin123` | Web 后台登录密码 |

---

## 技术要点

### 红点检测算法
- 扫描微信左侧导航栏区域（左 4%~50%，上 12%~88%）
- 每 2 像素采样，判断 R>160 且 G<0.55R 且 B<0.55R（红色点）或 R>130 且 G<100 且 B<100（徽章色）
- Y 轴聚类（间距 ≤25px 视为同一簇），过滤孤立噪点（≤2 像素）

### 消息定位算法
- 截取聊天文本区域（右 40%~77%，上 6%~85%）
- 逐行扫描暗色像素（R/G/B < 80），从底部向上找文字块
- 连续 15 行无暗色像素视为文字块边界
- 裁剪区域限制在 700px 高度以内

### 昵称提取算法
- OCR 结果的第 1 行如果短（≤8 字符）且第 2 行正常（≥3 个汉字/字母或 >10 字符或含唤醒词），则第 1 行为昵称
- 自动清理企业微信后缀（`@xxx`）

### Coze API 流程
1. `POST /v3/chat` — 发起对话，获得 `chat_id` + `conversation_id`
2. `GET /v3/chat/retrieve` — 轮询等待完成（最多 30 次 × 2s）
3. `GET /v3/chat/message/list` — 获取消息列表，提取 `role=assistant` 的回复

### 熔断与退避
- Coze 总超时 60s（跨 chat/retrieve/message 三阶段统一计时）
- 连续失败计数，达到 5 次后暂停请求
- 单次成功后计数器清零

### 问题分类策略
1. Coze 回复中的 `分类：xxx` 或 `分类: xxx` 格式作为主分类依据
2. 无前缀时，使用 BAAI/bge-small-zh-v1.5 嵌入模型计算语义相似度
3. 余弦相似度 ≥ 0.85 归入已有 cluster，否则新建

---

## 版本演进历程

| 版本 | 主要变更 |
|------|---------|
| v24 | 初始版本：PrintWindow + 动态 OCR 定位 + 唤醒词过滤 |
| v25 | OCR 引擎 Tesseract → PaddleOCR（PP-OCRv5） |
| v26 | 红点检测 v3 + Coze API v3 三步流程 + 配置抽离 |
| v27 | Y轴聚类多红点并发 + 同聊多轮 + stall 跟踪 |
| v28 | @提醒 + OCR 昵称 + 异常消息熔断 + 自适应间隔 + Coze 会话隔离 + 总超时熔断 |
| v29 | AI 后端可插拔（Coze / OpenAI / 自定义） |
| v30 | 问题统计 SQLite + 聚类 + 语义相似度 + Web 后台 + Excel 导出 |

---

## 已知限制

1. **仅 Windows**: 依赖 Win32 API（`win32gui`、`PrintWindow`、`DwmGetWindowAttribute`）
2. **仅微信 4.x**: 窗口类名 `WeChatMainWndForPC`，其他版本可能需调整
3. **前台运行**: 需要微信窗口可见（可最小化后恢复），截图期间不能遮挡
4. **单显示器**: 多显示器未充分测试
5. **中文优化**: OCR 和语义匹配主要针对中文场景

---

## 许可

内部项目，未指定开源许可。
