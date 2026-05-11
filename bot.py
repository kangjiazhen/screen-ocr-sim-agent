# -*- coding: utf-8 -*-
"""
微信智能机器人 v26
========================================
基于 v25 的文字扫描定位 + 红点检测触发 + Coze AI
升级：
  - OCR 引擎 Tesseract → PaddleOCR（PP-OCRv5 模型，enable_mkldnn=False 规避 3.3.x PIR/oneDNN BUG）
  - PaddleOCR 启动预加载（首条消息无需等待）
  - 红点检测 v3 扩大扫描范围 + 双模式颜色检测
  - Coze API v3 三步流程（chat → retrieve → message/list）
  - 配置抽离到 config.py
保留：
  - PrintWindow(PW_RENDERFULLCONTENT) 支持微信4.x GPU渲染
  - 动态消息定位（像素行扫描 + 百分比坐标）
  - 图像 hash 变化检测去重
  - 唤醒词正则过滤

========================================
"""
import sys
import os as _os

# ★ oneDNN 禁用（避免 Paddle 3.3.x PIR 属性转换崩溃）
_os.environ["FLAGS_use_onednn"] = "0"
_os.environ["FLAGS_onednn_use_cache"] = "0"
_os.environ["KMP_AFFINITY"] = "disabled"
_os.environ["KMP_BLOCKTIME"] = "0"
import re
import json
import time
import hashlib
import logging
import ctypes
import ctypes.wintypes

import pyperclip
import pyautogui
import win32gui
import win32con
import win32ui
import win32api
import numpy as np
from PIL import Image
from paddleocr import PaddleOCR
import requests

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    KEYBOARD_AVAILABLE = False

# ==================== 配置区（从 config.py 导入） ====================
from config import (
    WAKE_WORD,
    COZE_API_TOKEN, BOT_ID,
    COZE_CHAT_URL, COZE_RETRIEVE_URL, COZE_MESSAGE_LIST_URL,
    CHECK_INTERVAL, NO_RED_DOT_FALLBACK,
    COZE_TIMEOUT, COZE_POLL_MAX,
    LOG_LEVEL,
    PADDLEOCR_LANG, PADDLEOCR_USE_ANGLE_CLS,
)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# DPI 感知
ctypes.windll.shcore.SetProcessDpiAwareness(2)

# PaddleOCR 全局实例（启动时预加载，后续复用）
_ocr = None

def init_ocr():
    global _ocr
    logging.info("[OCR] 正在预加载 PaddleOCR 模型...")
    _ocr = PaddleOCR(use_textline_orientation=PADDLEOCR_USE_ANGLE_CLS, lang=PADDLEOCR_LANG, enable_mkldnn=False)
    logging.info("[OCR] PaddleOCR 预加载完成")
    return _ocr


# ==================== 窗口工具 ====================
def get_wechat_hwnd():
    hwnd = win32gui.FindWindow("WeChatMainWndForPC", None)
    if hwnd:
        return hwnd
    hwnd = win32gui.FindWindow("Qt51514QWindowIcon", None)
    if hwnd:
        return hwnd
    results = []
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if '微信' in title:
                extra.append(hwnd)
    win32gui.EnumWindows(callback, results)
    return results[0] if results else None


def get_rect(hwnd):
    rect = ctypes.wintypes.RECT()
    ctypes.windll.dwmapi.DwmGetWindowAttribute(
        hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def ensure_visible(hwnd):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.4)


def bring_to_front(hwnd):
    ensure_visible(hwnd)
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except:
        pass


# ==================== 截图（支持GPU渲染） ====================
def capture_window(hwnd):
    """PrintWindow + PW_RENDERFULLCONTENT"""
    ensure_visible(hwnd)
    l, t, r, b = get_rect(hwnd)
    w, h = r - l, b - t
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    # ★ 关键：第3参数 = 2 (PW_RENDERFULLCONTENT)
    ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer('RGB', (info['bmWidth'], info['bmHeight']),
                           bits, 'raw', 'BGRX', 0, 1)
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    win32gui.DeleteObject(bmp.GetHandle())
    return img


# ==================== 红点检测（v3：适配企业微信 + 多色系） ====================
def find_red_dot(hwnd):
    """扫描左侧聊天列表区，检测未读消息标记（红点/数字徽章）"""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w = r - l
        h = b - t
        # 聊天列表区：左侧 0~50% 宽度，顶部 12%~88% 高度
        scan_l = l + int(w * 0.04)
        scan_t = t + int(h * 0.12)
        scan_r = l + int(w * 0.50)
        scan_b = t + int(h * 0.88)
        region_w = scan_r - scan_l
        region_h = scan_b - scan_t
        screenshot = pyautogui.screenshot(region=(scan_l, scan_t, region_w, region_h))
        sw, sh = screenshot.size
        red_pixels = []
        for y in range(0, sh, 2):
            for x in range(0, sw, 2):
                r_pix, g_pix, b_pix = screenshot.getpixel((x, y))
                # 模式1：纯红/粉红（未读红点） R主导，G和B明显低
                # 模式2：暗红徽章（数字背景） R>130，G和B都较低
                is_red_dot = (r_pix > 160 and g_pix < r_pix * 0.55 and b_pix < r_pix * 0.55)
                is_badge = (r_pix > 130 and g_pix < 100 and b_pix < 100 and r_pix > g_pix + 40)
                if is_red_dot or is_badge:
                    red_pixels.append((scan_l + x, scan_t + y))
        logging.info(f"[红点扫描] 区域={region_w}x{region_h}  红/徽章像素数={len(red_pixels)}")
        if len(red_pixels) > 2:
            avg_x = sum(p[0] for p in red_pixels) // len(red_pixels)
            avg_y = sum(p[1] for p in red_pixels) // len(red_pixels)
            logging.info(f"[红点] 检测到 @ ({avg_x}, {avg_y}) 共{len(red_pixels)}像素")
            return avg_x, avg_y
        else:
            logging.info(f"[红点] 未检测到（仅{len(red_pixels)}像素）")
    except Exception as e:
        logging.error(f"[红点] 扫描异常: {e}")
    return None


def click_red_dot(hwnd):
    pos = find_red_dot(hwnd)
    if pos:
        pyautogui.moveTo(pos, duration=0.1)
        pyautogui.click()
        logging.info(f"[红点] 点击成功: {pos}")
        time.sleep(0.8)
        return True
    return False


def click_first_chat(hwnd):
    """点击聊天列表第一个会话（红点检测失败时的降级方案）"""
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    w = r - l
    h = b - t
    chat_x = l + int(w * 0.12)
    chat_y = t + int(h * 0.18)
    logging.info(f"[降级] 点击第一个聊天位置 ({chat_x}, {chat_y})")
    pyautogui.click(chat_x, chat_y)
    time.sleep(0.6)
    return True


# ==================== 动态定位最新气泡（v24方案） ====================
def snap_hash(hwnd):
    """截取聊天记录区域，返回 MD5 hash。用于检测消息变化"""
    img = capture_window(hwnd)
    w, h = img.size
    crop = img.crop((int(w * 0.40), int(h * 0.06), int(w * 0.79), int(h * 0.85)))
    return hashlib.md5(crop.tobytes()).hexdigest()


def get_latest_bubble_text(hwnd):
    """基于文字行扫描，动态截取最新一条对方消息，返回清洗后的文本"""
    img = capture_window(hwnd)
    w, h = img.size
    arr = np.array(img)
    logging.info(f"  [OCR] 窗口尺寸: {w}x{h}")

    # 聊天记录区 Y 范围（6% ~ 85%）
    chat_y1 = int(h * 0.06)
    chat_y2 = int(h * 0.85)

    # 对方气泡文字列扫描范围（参考：头像~40px+昵称，0.40跳过头像，0.77覆盖文字最右）
    text_x1 = int(w * 0.40)
    text_x2 = int(w * 0.77)

    region = arr[chat_y1:chat_y2, text_x1:text_x2]
    rh = region.shape[0]

    # 深色文字检测（R,G,B均<80）
    dark = (region[:, :, 0] < 80) & (region[:, :, 1] < 80) & (region[:, :, 2] < 80)
    row_dark = dark.sum(axis=1)

    # 从底部向上找第一条有文字的行
    bottom_y = -1
    for yi in range(rh - 1, -1, -1):
        if row_dark[yi] > 2:
            bottom_y = yi
            break

    if bottom_y < 0:
        logging.info("[OCR] 未找到对方文字行")
        return ""

    # 向上找气泡顶（连续15行无文字则认为是气泡顶部）
    top_y = bottom_y
    gap = 0
    for yi in range(bottom_y, max(bottom_y - 800, -1), -1):
        if row_dark[yi] > 2:
            top_y = yi
            gap = 0
        else:
            gap += 1
            if gap >= 15:
                break

    # 裁剪坐标（留内边距）
    crop_y1 = chat_y1 + max(top_y - 15, 0)
    crop_y2 = chat_y1 + bottom_y + 20
    crop_x1 = int(w * 0.40)   # 跳过头像
    crop_x2 = int(w * 0.79)

    crop_h = crop_y2 - crop_y1
    logging.info(f"  [OCR] 文字块: y={crop_y1}~{crop_y2}({crop_h}px), x={crop_x1}~{crop_x2}")
    if crop_h < 10:
        logging.info("[OCR] 文字块高度过小，跳过")
        return ""
    if crop_h > 700:
        crop_y1 = crop_y2 - 400
        crop_h = 400

    crop = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))

    # PaddleOCR 直接识别（无需手动放大/对比度增强，模型自带预处理）
    crop_arr = np.array(crop)
    try:
        if _ocr is None:
            logging.error("[OCR] PaddleOCR 未初始化，尝试重新加载...")
            init_ocr()
            if _ocr is None:
                return ""
        ocr = _ocr
        ocr_result = ocr.predict(crop_arr)
    except Exception as e:
        logging.error(f"[OCR] PaddleOCR 识别失败: {e}")
        return ""

    if not ocr_result or len(ocr_result) == 0:
        logging.info("[OCR] PaddleOCR 未检测到文字")
        return ""

    lines = []
    for item in ocr_result:
        rec_texts = item.get("rec_texts", [])
        rec_scores = item.get("rec_scores", [])
        for i, text in enumerate(rec_texts):
            conf = rec_scores[i] if i < len(rec_scores) else 0
            if text.strip():
                lines.append(text.strip())
                logging.info(f"  [OCR] 片段: 「{text}」 置信度={conf:.2f}")

    raw = "\n".join(lines)

    logging.info(f"  [OCR] 原始输出: 「{raw[:80]}...」" if len(raw) > 80 else f"  [OCR] 原始输出: 「{raw}」")

    # 后处理：参考v24
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    clean = []
    for ln in lines:
        zh = sum(1 for c in ln if '\u4e00' <= c <= '\u9fff')
        alpha = sum(1 for c in ln if c.isalpha())
        digit = sum(1 for c in ln if c.isdigit())
        if zh >= 1 or alpha >= 3 or (alpha >= 1 and digit >= 1):
            if zh >= 1:
                ln = re.sub(r'^[a-zA-Z0-9 _\-\.]+(?=[\u4e00-\u9fff])', '', ln).strip()
            if ln:
                clean.append(ln)

    def is_nickname_line(s):
        stripped = s.strip()
        return (len(stripped) <= 6
                and all(c.isalpha() or c.isspace() for c in stripped)
                and sum(1 for c in stripped if '\u4e00' <= c <= '\u9fff') == 0)
    clean = [ln for ln in clean if not is_nickname_line(ln)]

    merged = []
    for ln in clean:
        if re.match(r'^([^\u4e00-\u9fff]{0,3})([\u4e00-\u9fff]{1,2})(\s+[\u4e00-\u9fff])', ln) and merged:
            merged.append(ln)
        elif re.match(r'^[上丨|][\u4e00-\u9fff]', ln) and merged:
            fixed = re.sub(r'^[上丨|]', '', ln)
            merged[-1] = merged[-1] + fixed
        else:
            merged.append(ln)

    result = " ".join(merged).strip()
    result = re.sub(r'\s+', ' ', result)
    result = re.sub(r'[》《|丨]{1,3}', '', result).strip()

    # ★ 合并 CJK 字符间空格：OCR 常把 "机器人" 识别成 "机 器 人"
    #   ". @ 机 器 人 你 好 2" → ". @机器人你好2"
    result = re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])', '', result)
    result = re.sub(r'@\s+(?=[\u4e00-\u9fff])', '@', result)

    return result


# ==================== Coze 回复 ====================
def coze_reply(content):
    headers = {
        "Authorization": f"Bearer {COZE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "bot_id": BOT_ID,
        "user_id": "wx-user-001",
        "stream": False,
        "auto_save_history": True,
        "additional_messages": [{"role": "user", "content": content, "content_type": "text"}]
    }
    try:
        # Step 1: 发起对话
        resp = requests.post(COZE_CHAT_URL, headers=headers, json=payload, timeout=COZE_TIMEOUT)
        logging.info(f"[Coze] POST 状态码: {resp.status_code}")
        if resp.status_code != 200:
            logging.error(f"[Coze] POST 失败: {resp.status_code} {resp.text[:300]}")
            return None

        data = resp.json()
        if "data" not in data:
            logging.error(f"[Coze] POST 响应无data: {json.dumps(data, ensure_ascii=False)[:500]}")
            return None

        chat_id = data["data"]["id"]
        conv_id = data["data"]["conversation_id"]
        logging.info(f"[Coze] chat_id={chat_id[:20]}... 开始轮询")

        # Step 2: 轮询等待对话完成
        for attempt in range(COZE_POLL_MAX):
            time.sleep(2)
            r = requests.get(COZE_RETRIEVE_URL, headers=headers,
                             params={"chat_id": chat_id, "conversation_id": conv_id}, timeout=COZE_TIMEOUT)
            if r.status_code != 200:
                logging.error(f"[Coze] 轮询失败: {r.status_code}")
                continue
            d = r.json()
            if "data" not in d:
                continue
            status = d["data"]["status"]
            if status == "completed":
                break
            elif status == "failed":
                logging.error(f"[Coze] 对话失败: {d['data'].get('last_error', '')}")
                return None
        else:
            logging.warning("[Coze] 轮询超时")
            return None

        # Step 3: 获取消息列表（真正包含回复内容的端点）
        logging.info(f"[Coze] 对话完成，获取消息列表...")
        r = requests.get(COZE_MESSAGE_LIST_URL, headers=headers,
                         params={"chat_id": chat_id, "conversation_id": conv_id}, timeout=COZE_TIMEOUT)
        if r.status_code != 200:
            logging.error(f"[Coze] 消息列表请求失败: {r.status_code}")
            return None

        msg_data = r.json()
        messages = msg_data.get("data", [])

        if not messages:
            logging.info(f"[Coze] data 为列表形式，直接检查...")
            messages = msg_data if isinstance(msg_data, list) else []

        logging.info(f"[Coze] 获取到 {len(messages)} 条消息")

        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            msg_type = msg.get("type", "?")
            logging.info(f"[Coze] msg[{i}] role={role} type={msg_type}")
            content_raw = msg.get("content", "")
            if role == "assistant" or msg_type == "answer":
                if isinstance(content_raw, str) and content_raw.strip():
                    logging.info(f"[Coze] 提取回复: {content_raw[:100]}")
                    return content_raw
                elif isinstance(content_raw, list):
                    texts = []
                    for block in content_raw:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                    if texts:
                        result = "".join(texts)
                        logging.info(f"[Coze] 提取回复(列表): {result[:100]}")
                        return result

        logging.warning(f"[Coze] 未找到assistant回复，消息列表: {json.dumps(messages, ensure_ascii=False)[:500]}")
        return "（Coze 未生成回复）"

    except Exception as e:
        logging.error(f"[Coze] 异常: {e}")
        return None


# ==================== 发送消息 ====================
def send_message(hwnd, text):
    ensure_visible(hwnd)
    l, t, r, b = get_rect(hwnd)
    w, h = r - l, b - t
    # 输入框点击点：宽度60%，高度88%
    input_x = l + int(w * 0.60)
    input_y = t + int(h * 0.88)

    bring_to_front(hwnd)
    pyautogui.click(input_x, input_y)
    time.sleep(0.3)

    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.25)
    pyautogui.press('enter')
    time.sleep(0.35)

    # 保持窗口在前
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)


def back_to_chat_list(hwnd):
    """点击左侧空白回退到列表（可选）"""
    l, t, _, _ = get_rect(hwnd)
    pyautogui.click(l + 80, t + 150)
    time.sleep(0.5)


# ==================== 主循环 ====================
def main():
    hwnd = get_wechat_hwnd()
    if not hwnd:
        logging.error("找不到微信窗口")
        return

    logging.info("✅ 微信窗口已找到")
    init_ocr()
    pyautogui.PAUSE = 0.2
    pyautogui.FAILSAFE = False

    last_msg_hash = ""
    last_snap_hash = ""
    no_red_dot_count = 0
    logging.info(f"🤖 机器人启动，唤醒词: {WAKE_WORD}")
    logging.info("📱 窗口保持可见，按 '-' 退出程序")

    while True:
        if KEYBOARD_AVAILABLE and keyboard.is_pressed('-'):
            logging.info("检测到退出键")
            break

        ensure_visible(hwnd)

        # 红点检测：有红点则进入对应聊天
        clicked = click_red_dot(hwnd)
        if not clicked:
            no_red_dot_count += 1
            if no_red_dot_count >= NO_RED_DOT_FALLBACK:
                logging.info(f"[降级] 连续{NO_RED_DOT_FALLBACK}次无红点，强制检查当前聊天")
                no_red_dot_count = 0
            else:
                time.sleep(CHECK_INTERVAL)
                continue
        else:
            no_red_dot_count = 0

        # 等待消息渲染完成
        time.sleep(0.8)

        # 跳转到最新消息
        pyautogui.press('end')
        time.sleep(0.6)

        # ★ 图像变化检测：截图聊天区对比 hash，无变化则跳过
        current_snap = snap_hash(hwnd)
        if current_snap == last_snap_hash:
            logging.info("[Hash] 聊天区无变化，跳过")
            back_to_chat_list(hwnd)
            time.sleep(CHECK_INTERVAL)
            continue
        last_snap_hash = current_snap

        # 获取最新一条消息
        msg = get_latest_bubble_text(hwnd)
        if not msg:
            logging.info("未识别到消息，返回列表")
            last_snap_hash = snap_hash(hwnd)
            back_to_chat_list(hwnd)
            time.sleep(CHECK_INTERVAL)
            continue

        # 文字去重
        mh = hashlib.md5(msg.encode()).hexdigest()[:8]
        if mh == last_msg_hash:
            logging.info("消息重复，跳过")
            back_to_chat_list(hwnd)
            time.sleep(CHECK_INTERVAL)
            continue
        last_msg_hash = mh

        logging.info(f"📩 收到消息: {msg}")

        # 唤醒词检查（正则移除）
        if WAKE_WORD in msg:
            question = re.sub(rf'\s*{re.escape(WAKE_WORD)}\s*', ' ', msg).strip()
            if not question:
                question = "你好"
            logging.info(f"触发唤醒，提问: {question}")
            reply = coze_reply(question)
            if reply:
                send_message(hwnd, reply)
            else:
                send_message(hwnd, "抱歉，暂时无法回答")
        else:
            logging.info("未包含唤醒词，忽略")

        # 回复后更新 hash 基准（等待屏幕稳定）
        time.sleep(1)
        last_snap_hash = snap_hash(hwnd)
        back_to_chat_list(hwnd)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()