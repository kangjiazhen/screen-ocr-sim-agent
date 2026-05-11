# 微信智能机器人

基于 Windows GUI 自动化 + OCR + AI 大模型的微信自动回复机器人。无需 Hook、无需注入、无需逆向微信协议 —— 截图识别红点定位未读消息，OCR 提取文字，调用 AI 后端生成回复，模拟键盘输入自动发送。

## 特性

- **AI 后端可插拔**（v29） — `ai_backends` 字典注册，`AI_BACKEND` 一键切换 Coze / OpenAI / 自定义
- **红点聚类扫描** — Y 轴像素聚类，同时检测多个未读会话，逐个击破
- **PaddleOCR 识别** — PP-OCRv5 中文识别，动态定位最新气泡，无需固定坐标
- **真实 @提醒** — 群聊中通过微信 @面板真实 @发送者，非纯文本模拟
- **昵称提取** — 从 OCR 结果自动提取发送者昵称，支持企业微信后缀清理
- **异常消息快速熔断** — 图片/语音/视频等无文字消息，stall+2 双倍跳过
- **自适应扫描** — 有红点 1s 间隔，60s 无活动自动降速到 3s
- **熔断保护** — AI 请求总超时 60s + 连续 5 次失败自动退避
- **会话隔离** — 不同聊天使用独立的 AI 上下文（基于红点 Y 坐标），互不串扰
- **同聊多轮** — 同一聊天最多 4 轮连续处理，应对多人同时提问
- **GPU 渲染兼容** — PrintWindow(PW_RENDERFULLCONTENT) 支持微信 4.x
- **去重** — 图像 hash 变化检测 + 消息 MD5 去重

## 工作原理

```
微信窗口 → 红点检测(像素聚类) → 点击未读会话 → 截图+OCR识别消息
    → 唤醒词匹配 → AI后端获取回复 → 模拟输入发送(@回复/普通回复)
    → 返回聊天列表 → 循环
```

| 模块 | 技术细节 |
|------|----------|
| 窗口发现 | 多策略查找（类名 / 备选类名 / 标题枚举），DWM 精确获取矩形，DPI 感知 |
| 红点检测 | pyautogui 截取左侧聊天列表 4%-50% 水平 × 12%-88% 垂直区域，双模式颜色阈值 + Y 轴聚类（25px 间距合并） |
| 消息定位 | 像素行扫描从底部向上找到最暗行，向上扩展连续文字块，百分比坐标适配任意窗口大小 |
| OCR 识别 | PaddleOCR (PP-OCRv5)，中文识别，enable_mkldnn=False，启动时预加载 |
| AI 后端 | 可插拔架构：Coze API v3（chat → retrieve → message/list 三步流程）/ OpenAI 兼容接口 / 自定义 |
| 消息发送 | pyperclip 粘贴 + pyautogui 模拟键盘，群聊通过 @面板真实 @发送者 |
| 去重熔断 | 图像 hash 变化检测 + 消息 MD5 去重 + stall 跟踪（3 次跳过，处理成功清零）+ 连续失败退避 |

## 环境要求

- **Windows 10/11**（依赖 win32 API 和 DWM）
- **微信桌面客户端**（3.x/4.x）
- **Python 3.8+**

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/yourusername/aiWechatservice.git
cd aiWechatservice

# 2. 安装依赖
pip install pyautogui pyperclip pywin32 numpy Pillow paddleocr requests

# 可选：用于快捷键退出
pip install keyboard

# 可选：使用 OpenAI 后端时
pip install openai

# 3. 创建配置文件
cp config.example.py config.py
```

## 配置

编辑 `config.py`：

```python
# ==================== 唤醒词 ====================
WAKE_WORD = "@管家"              # 触发机器人回复的关键词

# ==================== AI 后端（v29） ====================
# 可选: "coze" / "openai" / "custom"
AI_BACKEND = "coze"

# ==================== Coze API（AI_BACKEND="coze" 时启用） ====================
COZE_API_TOKEN = "pat_xxxxxx"    # Coze 个人访问令牌
BOT_ID = "76366xxxxxxxxxx"       # Coze Bot ID
COZE_API_BASE = "https://api.coze.cn"

# ==================== OpenAI（AI_BACKEND="openai" 时启用） ====================
# OPENAI_API_KEY = "sk-xxxxxxxx"
# OPENAI_BASE_URL = "https://api.openai.com/v1"
# OPENAI_MODEL = "gpt-4o-mini"

# ==================== OCR 引擎 ====================
PADDLEOCR_LANG = 'ch'            # ch=中文，en=英文
PADDLEOCR_USE_ANGLE_CLS = True  # 文字方向校正

# ==================== 行为设置 ====================
CHECK_INTERVAL = 1.0             # 主循环间隔（秒）
NO_RED_DOT_FALLBACK = 10         # 连续无红点多少次后降级强制检查
COZE_TIMEOUT = 30                # Coze 请求超时（秒）
COZE_POLL_MAX = 30               # 最大轮询次数（每次 2 秒）
LOG_LEVEL = "INFO"               # DEBUG / INFO / WARNING / ERROR
```

> **获取 Coze API Token**：前往 [coze.cn](https://www.coze.cn) 创建 Bot → 发布 → API 页面获取令牌。

## 使用

```bash
python bot.py
```

1. 确保微信已登录并打开
2. 运行脚本，自动找到微信窗口
3. 机器人开始循环检测红点、识别消息、自动回复
4. 按 `-` 键安全退出

### 添加自定义 AI 后端

在 `bot.py` 的 `ai_backends` 字典中注册你的函数，然后在 `config.py` 设置 `AI_BACKEND = "custom"`：

```python
def my_backend(content, chat_user_id):
    # 你的 AI 调用逻辑
    return reply_text, True   # 返回 (回复内容, 是否已处理)

ai_backends = {
    "coze": _coze_reply,
    "openai": _openai_reply,
    "custom": my_backend,     # ← 添加这一行
}
```

所有后端函数签名统一为 `(content: str, chat_user_id: str) -> (reply_text: str | None, handled: bool)`。

## 使用场景

- **群聊客服**：在公寓/社区/公司群中自动回答常见问题
- **消息中转**：将微信消息转发到 AI 处理后回复
- **自动应答**：离开时自动回复 @你的消息

## 常见问题

<details>
<summary><b>PaddleOCR 初始化报错 / 崩溃</b></summary>

本项目已禁用 oneDNN（`enable_mkldnn=False` + 环境变量三重禁用），以规避 Paddle 3.3.x 的 PIR 属性转换 BUG。如仍有问题，尝试：
```bash
pip install paddlepaddle==3.0.0 paddleocr==2.9.1
```
</details>

<details>
<summary><b>截图黑屏 / 无法识别微信窗口</b></summary>

- 微信 4.x 使用 GPU 渲染，已使用 `PrintWindow(PW_RENDERFULLCONTENT)` 兼容
- 确保微信窗口未被最小化、未被其他窗口完全遮挡
- 如果是多显示器，确保微信在主显示器上
</details>

<details>
<summary><b>红点检测不到</b></summary>

- 确认微信聊天列表中有未读红点
- 可调整 `find_red_dot_clusters()` 中的 RGB 阈值（第 167-168 行）
- 将 `LOG_LEVEL` 设为 `DEBUG` 查看详细日志
</details>

<details>
<summary><b>如何切换 AI 后端？</b></summary>

在 `config.py` 中修改 `AI_BACKEND` 的值：
- `"coze"` — 使用 Coze API v3（默认）
- `"openai"` — 使用 OpenAI 兼容接口（需先 `pip install openai` 并填写 `OPENAI_API_KEY` 等配置）
- 自定义名称 — 需先在 `bot.py` 的 `ai_backends` 字典注册对应函数
</details>

## 项目结构

```
screen-ocr-sim-agent/
├── bot.py               # 主程序 v29
├── config.example.py    # 配置文件模板
├── config.py            # 配置文件（已在 .gitignore，不提交）
├── .gitignore
└── README.md
```

## 免责声明

- 本项目仅供学习和研究使用
- 使用 GUI 自动化方式与微信交互，不涉及逆向工程或协议破解
- 使用者需自行承担使用风险，包括但不限于微信账号被限制等
- 请遵守微信用户协议及相关法律法规

## License

MIT
