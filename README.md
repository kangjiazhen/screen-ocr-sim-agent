# 微信智能机器人 🤖

基于 Windows GUI 自动化 + OCR + [Coze API](https://www.coze.cn) 的微信 AI 自动回复机器人。

无需 Hook、无需注入、无需逆向微信协议 —— 通过截图识别红点定位未读消息，OCR 提取文字，调用 AI 大模型生成回复，模拟键盘输入自动发送。

## 特性

- **🔴 红点聚类扫描** — Y 轴像素聚类，同时检测多个未读会话，逐个击破
- **👁️ PaddleOCR 识别** — 中文文字识别，动态定位最新气泡，无需固定坐标
- **🧠 Coze 大模型** — 接入 Coze API v3 三步流程，支持自定义 Bot 人格
- **💬 真实 @提醒** — 群聊中通过微信 @面板 真实 @发送者，非纯文本模拟
- **👤 昵称提取** — 从 OCR 结果中自动提取发送者昵称，支持企业微信后缀清理
- **⚡ 自适应扫描** — 有红点 1s 间隔，60s 无活动自动降速到 3s
- **🛡️ 熔断保护** — Coze 60s 总超时 + 连续 5 次失败自动退避
- **🔗 会话隔离** — 不同聊天使用独立的 Coze 上下文，互不串扰
- **🔄 同聊多轮** — 同一聊天最多 4 轮连续处理，应对多人同时提问
- **📸 GPU 渲染兼容** — PrintWindow(PW_RENDERFULLCONTENT) 支持微信 4.x

## 工作原理

```
微信窗口 → 红点检测(像素聚类) → 点击未读会话 → 截图+OCR识别消息
    → 唤醒词匹配 → Coze API 获取AI回复 → 模拟输入发送(@回复/普通回复)
    → 返回聊天列表 → 循环
```

| 模块 | 说明 |
|------|------|
| 窗口发现 | 多策略查找微信窗口，DWM 精确获取矩形，DPI 感知 |
| 红点检测 | pyautogui 截取左侧聊天列表，像素阈值 + Y 轴聚类定位未读 |
| 消息定位 | 像素行扫描找到最新气泡边界，百分比坐标适配任意窗口大小 |
| OCR 识别 | PaddleOCR (PP-OCRv5)，中文识别，启动时预加载模型 |
| AI 回复 | Coze API v3：chat → 轮询 retrieve → message/list 获取回复 |
| 消息发送 | pyperclip 粘贴 + pyautogui 模拟键盘，群聊通过 @面板 @发送者 |
| 去重熔断 | MD5 去重 + stall 跟踪 + Coze 连续失败退避 |

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

# 3. 创建配置文件
cp config.example.py config.py
```

## 配置

编辑 `config.py`，填入你的 Coze 凭证：

```python
# ==================== 唤醒词 ====================
WAKE_WORD = "@管家"          # 触发机器人回复的关键词

# ==================== Coze API ====================
COZE_API_TOKEN = "pat_xxxxxx"    # Coze 个人访问令牌
BOT_ID = "76366xxxxxxxxxx"       # Coze Bot ID

# ==================== 行为设置 ====================
CHECK_INTERVAL = 1.0             # 主循环间隔（秒）
COZE_TIMEOUT = 30                # Coze 请求超时（秒）
COZE_POLL_MAX = 30               # 最大轮询次数
NO_RED_DOT_FALLBACK = 10         # 连续无红点多少次后强制检查
LOG_LEVEL = "INFO"               # DEBUG / INFO / WARNING / ERROR
```

> **获取 Coze API Token**：前往 [coze.cn](https://www.coze.cn) 创建 Bot → 发布 → API 页面获取令牌。

## 使用

```bash
python "bot v28.py"
```

1. 确保微信已登录并打开
2. 运行脚本，它将自动找到微信窗口
3. 机器人开始循环检测红点、识别消息、自动回复
4. 按 `-` 键安全退出

### 使用场景

- **群聊客服**：在公寓/社区/公司群中设置 Coze Bot 自动回答常见问题
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
- 可调整 `find_red_dot_clusters()` 中的 RGB 阈值
- 将 `LOG_LEVEL` 设为 `DEBUG` 查看详细日志
</details>

## 项目结构

```
aiWechatservice/
├── bot v28.py          # 当前版本，生产使用
├── bot v27.py          # 历史版本（参考用）
├── bot v26.py          # 历史版本（参考用）
├── bot v25.py          # 历史版本（参考用）
├── config.example.py   # 配置文件模板
├── config.py           # 配置文件（已 gitignore，不提交）
└── .gitignore
```

## 免责声明

- 本项目仅供学习和研究使用
- 使用 GUI 自动化方式与微信交互，不涉及逆向工程或协议破解
- 使用者需自行承担使用风险，包括但不限于微信账号被限制等
- 请遵守微信用户协议及相关法律法规

## License

MIT
