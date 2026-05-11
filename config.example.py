# -*- coding: utf-8 -*-
"""
配置文件（模板）
========================================
复制为 config.py 并填入你的真实配置
"""

WAKE_WORD = "@管家小康"

# ==================== AI 后端（v29：可插拔） ====================
# 可选: "coze" / "openai" / "custom"
# 切换后端只需改此值，然后在 bot.py 的 ai_backends 字典里注册对应函数
AI_BACKEND = "coze"

# ==================== Coze API ====================
COZE_API_TOKEN = "pat_YOUR_TOKEN_HERE"
BOT_ID = "YOUR_BOT_ID"
COZE_API_BASE = "https://api.coze.cn"
COZE_CHAT_URL = f"{COZE_API_BASE}/v3/chat"
COZE_RETRIEVE_URL = f"{COZE_API_BASE}/v3/chat/retrieve"
COZE_MESSAGE_LIST_URL = f"{COZE_API_BASE}/v3/chat/message/list"

# ==================== OpenAI API（AI_BACKEND="openai" 时启用） ====================
# OPENAI_API_KEY = "sk-xxxxxxxx"
# OPENAI_BASE_URL = "https://api.openai.com/v1"
# OPENAI_MODEL = "gpt-4o-mini"

# ==================== Tesseract OCR（v25 使用） ====================
TESSERACT_PATHS = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    r'%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe',
    r'%APPDATA%\Tesseract-OCR\tesseract.exe',
    r'C:\Tesseract-OCR\tesseract.exe',
]

# ==================== PaddleOCR ====================
PADDLEOCR_LANG = 'ch'
PADDLEOCR_USE_ANGLE_CLS = True

CHECK_INTERVAL = 1.0
NO_RED_DOT_FALLBACK = 10
COZE_TIMEOUT = 30
COZE_POLL_MAX = 30

LOG_LEVEL = "INFO"

# ==================== v30 问题统计 ====================
# SQLite 数据库路径，自动创建
STATS_DB_PATH = "questions.db"
# 是否启用嵌入模型语义去重（需 pip install sentence-transformers）
# Coze 分类前缀优先，此选项仅在没有分类前缀时作为降级兜底
STATS_EMBEDDING_ENABLED = False

# ==================== Web 后台 ====================
# 0.0.0.0 = 局域网内其他电脑可访问, 127.0.0.1 = 仅本机访问
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000
WEB_PASSWORD = "admin123"     # 请修改为你的密码！
