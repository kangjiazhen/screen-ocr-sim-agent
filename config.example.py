# -*- coding: utf-8 -*-
"""
配置文件（模板）
========================================
复制为 config.py 并填入你的真实配置
"""

WAKE_WORD = "@管家"

COZE_API_TOKEN = "pat_YOUR_TOKEN_HERE"
BOT_ID = "YOUR_BOT_ID"
COZE_API_BASE = "https://api.coze.cn"
COZE_CHAT_URL = f"{COZE_API_BASE}/v3/chat"
COZE_RETRIEVE_URL = f"{COZE_API_BASE}/v3/chat/retrieve"
COZE_MESSAGE_LIST_URL = f"{COZE_API_BASE}/v3/chat/message/list"

TESSERACT_PATHS = [
    r'C:\Program Files\Tesseract-OCR\tesseract.exe',
    r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    r'%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe',
    r'%APPDATA%\Tesseract-OCR\tesseract.exe',
    r'C:\Tesseract-OCR\tesseract.exe',
]

PADDLEOCR_LANG = 'ch'
PADDLEOCR_USE_ANGLE_CLS = True

CHECK_INTERVAL = 1.0
NO_RED_DOT_FALLBACK = 10
COZE_TIMEOUT = 30
COZE_POLL_MAX = 30

LOG_LEVEL = "INFO"
