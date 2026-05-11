# -*- coding: utf-8 -*-
"""
微信智能机器人 v27
========================================
基于 v26 升级：多红点并发 + 同聊多轮处理
升级：
  - Y轴聚类检测 → 多个未读会话逐个击破，不再平均取点
  - stall 跟踪去重 → 点击无消息则计数，3次后跳过，不受置顶Y偏移影响
  - 同聊多轮扫描 → 同一聊天内多人同时提问，最多4轮逐一处理
  - 轮次计数器 → 每次处理完一轮红点后统一报告
保留：
  - PaddleOCR（PP-OCRv5，enable_mkldnn=False）
  - PrintWindow(PW_RENDERFULLCONTENT) 支持微信4.x GPU渲染
  - 动态消息定位（像素行扫描 + 百分比坐标）
  - 图像 hash 变化检测去重
  - 唤醒词正则过滤
  - Coze API v3 三步流程（chat → retrieve → message/list）
  - 配置抽离到 config.py

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


# ==================== 红点检测 v4：Y轴聚类 → 逐个击破 ====================
def find_red_dot_clusters(hwnd):
    """扫描聊天列表，按Y轴聚类红像素，返回 [(center_x, center_y, pixel_count), ...] 自上而下排序"""
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w = r - l
        h = b - t
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
                is_red_dot = (r_pix > 160 and g_pix < r_pix * 0.55 and b_pix < r_pix * 0.55)
                is_badge = (r_pix > 130 and g_pix < 100 and b_pix < 100 and r_pix > g_pix + 40)
                if is_red_dot or is_badge:
                    red_pixels.append((scan_l + x, scan_t + y))

        total = len(red_pixels)
        logging.info(f"[红点扫描] 区域={region_w}x{region_h}  总红像素={total}")

        if total <= 2:
            logging.info(f"[红点] 未检测到（仅{total}像素）")
            return []

        # 按 Y 排序
        red_pixels.sort(key=lambda p: p[1])

        # Y轴聚类：相邻像素 Y 间距 ≤ 25px 归为同一簇
        clusters = []
        current_cluster = [red_pixels[0]]
        for i in range(1, len(red_pixels)):
            if red_pixels[i][1] - red_pixels[i - 1][1] <= 25:
                current_cluster.append(red_pixels[i])
            else:
                if len(current_cluster) > 2:
                    clusters.append(current_cluster)
                current_cluster = [red_pixels[i]]
        if len(current_cluster) > 2:
            clusters.append(current_cluster)

        if not clusters:
            logging.info("[红点] 聚类后无有效簇")
            return []

        # 计算每个簇的中心
        result = []
        for ci, c in enumerate(clusters):
            avg_x = sum(p[0] for p in c) // len(c)
            avg_y = sum(p[1] for p in c) // len(c)
            result.append((avg_x, avg_y, len(c)))
            logging.info(f"  [红点簇{ci+1}] 中心=({avg_x},{avg_y})  像素={len(c)}  Y范围={c[0][1]}~{c[-1][1]}")

        logging.info(f"[红点] 检测到 {len(result)} 个未读会话")
        return result

    except Exception as e:
        logging.error(f"[红点] 扫描异常: {e}")
    return []


def click_red_dot(hwnd, stall_tracker):
    """点击最顶部的红点簇。stall_tracker 记录每个Y位置连续无新消息次数"""
    clusters = find_red_dot_clusters(hwnd)
    if not clusters:
        return False, None

    for cx, cy, pc in clusters:
        # 检查该位置是否已连续多次无新消息
        stall_count = stall_tracker.get(cy, 0)
        if stall_count >= 3:
            logging.info(f"[红点] 跳过簇 ({cx},{cy}) —— 已连续{stall_count}次无新消息")
            continue

        pyautogui.moveTo(cx, cy, duration=0.1)
        pyautogui.click()
        logging.info(f"[红点] 点击簇 ({cx}, {cy}) 像素={pc}  stall={stall_count}")
        time.sleep(0.8)
        return True, cy

    logging.info("[红点] 所有簇均处于stall状态，跳过")
    return False, None


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


# ==================== 动态定位最新气泡 ====================
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

    chat_y1 = int(h * 0.06)
    chat_y2 = int(h * 0.85)
    text_x1 = int(w * 0.40)
    text_x2 = int(w * 0.77)

    region = arr[chat_y1:chat_y2, text_x1:text_x2]
    rh = region.shape[0]

    dark = (region[:, :, 0] < 80) & (region[:, :, 1] < 80) & (region[:, :, 2] < 80)
    row_dark = dark.sum(axis=1)

    bottom_y = -1
    for yi in range(rh - 1, -1, -1):
        if row_dark[yi] > 2:
            bottom_y = yi
            break

    if bottom_y < 0:
        logging.info("[OCR] 未找到对方文字行")
        return ""

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

    crop_y1 = chat_y1 + max(top_y - 15, 0)
    crop_y2 = chat_y1 + bottom_y + 20
    crop_x1 = int(w * 0.40)
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

    ctypes.windll.user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)


def back_to_chat_list(hwnd):
    """点击左侧空白回退到列表"""
    l, t, _, _ = get_rect(hwnd)
    pyautogui.click(l + 80, t + 150)
    time.sleep(0.5)


# ==================== 单条消息处理 ====================
def process_one_message(hwnd, last_msg_hash):
    """进入聊天后，OCR识别 + 回复。返回 (是否成功, 新msg_hash)"""
    time.sleep(0.8)
    pyautogui.press('end')
    time.sleep(0.6)

    msg = get_latest_bubble_text(hwnd)
    if not msg:
        logging.info("[处理] 未识别到消息")
        return False, last_msg_hash

    logging.info(f"📩 收到消息: {msg}")

    # 文字去重（同一会话内，相同消息不重复回复）
    mh = hashlib.md5(msg.encode()).hexdigest()[:8]
    if mh == last_msg_hash:
        logging.info("[去重] 当前会话消息重复，跳过")
        return False, last_msg_hash

    if WAKE_WORD in msg:
        question = re.sub(rf'\s*{re.escape(WAKE_WORD)}\s*', ' ', msg).strip()
        if not question:
            question = "你好"
        logging.info(f"[触发] 唤醒词匹配，提问: {question}")
        reply = coze_reply(question)
        if reply:
            send_message(hwnd, reply)
        else:
            send_message(hwnd, "抱歉，暂时无法回答")
        return True, mh
    else:
        logging.info("[跳过] 未包含唤醒词")
        return False, last_msg_hash


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

    stall_tracker = {}
    last_msg_hash = ""
    last_snap_hash = ""
    no_red_dot_count = 0
    total_processed = 0

    SAME_CHAT_MAX_PASS = 4

    logging.info(f"🤖 机器人启动，唤醒词: {WAKE_WORD}")
    logging.info("📱 窗口保持可见，按 '-' 退出程序")
    logging.info("🔴 多红点模式：Y轴聚类 → 同聊多轮 → 逐个击破")

    while True:
        if KEYBOARD_AVAILABLE and keyboard.is_pressed('-'):
            logging.info("检测到退出键")
            break

        ensure_visible(hwnd)

        # ======== 红点检测：聚类 → 点最顶部的 ========
        clicked, clicked_y = click_red_dot(hwnd, stall_tracker)
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
            last_msg_hash = ""

        # ======== 同一聊天多轮检测（处理多人同时提问） ========
        chat_processed = 0
        last_snap_hash = snap_hash(hwnd)

        for _pass in range(SAME_CHAT_MAX_PASS):
            if _pass > 0:
                current_snap = snap_hash(hwnd)
                if current_snap == last_snap_hash:
                    break
            last_snap_hash = snap_hash(hwnd)

            success, last_msg_hash = process_one_message(hwnd, last_msg_hash)
            if success:
                chat_processed += 1
                total_processed += 1
                logging.info(f"[统计] 本轮第{_pass+1}条 | 累计={total_processed}")
            else:
                break

            # 短暂等待，检查是否有新消息弹出来
            time.sleep(1.5)
            pyautogui.press('end')
            time.sleep(0.5)

        # ======== stall 跟踪：无新消息则计数，有处理则清零 ========
        if clicked_y is not None:
            if chat_processed > 0:
                stall_tracker.pop(clicked_y, None)
                logging.info(f"[Stall] Y={clicked_y} 处理成功，清零")
            else:
                stall_tracker[clicked_y] = stall_tracker.get(clicked_y, 0) + 1
                logging.info(f"[Stall] Y={clicked_y} 无新消息，stall={stall_tracker[clicked_y]}/3")

        # ======== 清理过期 stall 记录（120秒无访问则清除） ========
        for y in list(stall_tracker.keys()):
            # 用简单的惰性清理：每次最多保留50条
            if len(stall_tracker) > 50:
                stall_tracker.pop(y, None)
                break

        # ======== 返回列表，准备下一轮 ========
        time.sleep(1)
        back_to_chat_list(hwnd)
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
