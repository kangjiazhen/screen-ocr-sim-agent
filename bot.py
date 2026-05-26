# -*- coding: utf-8 -*-
"""
微信智能机器人 v1.0.32
========================================
变更:
  - ★ 修复多红点场景第二个红点被漏处理 — 主循环移除 back_to_chat_list 硬编码点击
  - ★ 修复点击红点B却读取红点A的聊天内容 — snap_hash 校验聊天是否真实切换
  - ★ 红点处理改为 Y 轴最高优先 → reversed(clusters)
  - ★ 主循环简化 — 每轮一个红点，处理完回到顶部重新扫描，不再硬编码导航
  - back_to_chat_list 改为点击侧边栏安全区域，避免标题栏按钮误触
========================================
"""
import sys
import os as _os

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

from config import (
    AI_BACKEND,
    COZE_ENABLED,
    WAKE_WORD,
    WECHAT_PATH,
    COZE_API_TOKEN, BOT_ID,
    COZE_CHAT_URL, COZE_RETRIEVE_URL, COZE_MESSAGE_LIST_URL,
    CHECK_INTERVAL, NO_RED_DOT_FALLBACK,
    COZE_TIMEOUT, COZE_POLL_MAX,
    LOG_LEVEL,
    PADDLEOCR_LANG, PADDLEOCR_USE_ANGLE_CLS,
    STATS_DB_PATH,
)

from question_stats import QuestionStats, parse_coze_category

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s'
)

ctypes.windll.shcore.SetProcessDpiAwareness(2)

_ocr = None
_stats = None
_ocr_cache = {"text": ""}  # 防止无新消息时重复入库

def init_ocr():
    global _ocr
    logging.info("[OCR] 正在预加载 PaddleOCR 模型...")
    _ocr = PaddleOCR(use_textline_orientation=PADDLEOCR_USE_ANGLE_CLS, lang=PADDLEOCR_LANG, enable_mkldnn=False)
    logging.info("[OCR] PaddleOCR 预加载完成")
    return _ocr


# ==================== 窗口工具 ====================
def get_wechat_hwnd():
    hwnd = _find_wechat_by_class()
    if hwnd:
        logging.info(f"[窗口] 类名匹配: hwnd={hwnd} title={win32gui.GetWindowText(hwnd)}")
        return hwnd
    results = []
    def callback(hwnd, extra):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if '微信' in title:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if w > 500 and h > 400:
                extra.append(hwnd)
    win32gui.EnumWindows(callback, results)
    if results:
        logging.info(f"[窗口] 标题枚举匹配: hwnd={results[0]} title={win32gui.GetWindowText(results[0])}")
        return results[0]
    logging.warning("[窗口] 所有方式均未找到有效微信窗口")
    return None


def _valid_wechat_window(hwnd):
    try:
        if not win32gui.IsWindow(hwnd):
            return False
        if not win32gui.IsWindowVisible(hwnd):
            return False
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        w, h = r - l, b - t
        if w < 500 or h < 400:
            return False
        title = win32gui.GetWindowText(hwnd)
        return '微信' in title
    except Exception:
        return False


def _find_wechat_by_class():
    candidates = []
    def enum_cb(hwnd, _):
        class_name = win32gui.GetClassName(hwnd)
        if class_name in ("WeChatMainWndForPC", "Qt51514QWindowIcon"):
            if win32gui.IsWindow(hwnd):
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                area = max(0, r - l) * max(0, b - t)
                candidates.append((hwnd, area))
    win32gui.EnumWindows(enum_cb, None)
    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        best, area = candidates[0]
        logging.info(f"[窗口] 类名枚举: hwnd={best} area={area} visible={win32gui.IsWindowVisible(best)} title={win32gui.GetWindowText(best)} candidates={len(candidates)}")
        return best
    logging.warning("[窗口] 未找到任何 WeChatMainWndForPC 窗口")
    return None


def restore_wechat_window(hwnd):
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if not win32gui.IsWindowVisible(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    time.sleep(0.5)
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except:
        pass


def _is_wechat_running():
    import subprocess
    try:
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq WeChat.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, shell=True,
        )
        if 'WeChat.exe' in result.stdout:
            return True
        result = subprocess.run(
            ['tasklist', '/FI', 'IMAGENAME eq Weixin.exe', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, shell=True,
        )
        return 'Weixin.exe' in result.stdout
    except Exception:
        return False


def try_restore_wechat():
    hwnd = _find_wechat_by_class()
    if hwnd:
        restore_wechat_window(hwnd)
        return hwnd
    return None


def activate_tray_wechat():
    if not _is_wechat_running():
        return False
    try:
        pyautogui.hotkey('ctrl', 'alt', 'w')
        time.sleep(1)
        hwnd = _find_wechat_by_class()
        if hwnd:
            restore_wechat_window(hwnd)
            return True
        time.sleep(2)
        hwnd = _find_wechat_by_class()
        if hwnd:
            restore_wechat_window(hwnd)
            return True
    except Exception:
        pass
    return False


def launch_wechat():
    import subprocess
    path = WECHAT_PATH
    if path and _os.path.exists(path):
        logging.info(f"[窗口] 正在启动微信: {path}")
        subprocess.Popen(path, shell=True)
        return True
    wechat_paths = [
        _os.path.expandvars(r"%ProgramFiles%\Tencent\WeChat\WeChat.exe"),
        _os.path.expandvars(r"%ProgramFiles(x86)%\Tencent\WeChat\WeChat.exe"),
        _os.path.expandvars(r"%ProgramFiles%\Tencent\Weixin\Weixin.exe"),
        _os.path.expandvars(r"%ProgramFiles(x86)%\Tencent\Weixin\Weixin.exe"),
        _os.path.expandvars(r"%LocalAppData%\Programs\Tencent\WeChat\WeChat.exe"),
    ]
    for p in wechat_paths:
        if _os.path.exists(p):
            logging.info(f"[窗口] 正在启动微信: {p}")
            subprocess.Popen(p, shell=True)
            return True
    return False


def wait_wechat(timeout=30):
    for _ in range(timeout):
        time.sleep(1)
        hwnd = get_wechat_hwnd()
        if hwnd:
            return hwnd
    return None


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


# ==================== 截图 ====================
def capture_window(hwnd):
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


# ==================== 红点检测 v4 ====================
def find_red_dot_clusters(hwnd):
    try:
        l, t, r, b = get_rect(hwnd)
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

        red_pixels.sort(key=lambda p: p[1])

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


def click_red_dot(hwnd):
    clusters = find_red_dot_clusters(hwnd)
    if not clusters:
        return False, None

    for cx, cy, pc in reversed(clusters):
        pyautogui.moveTo(cx, cy, duration=0.1)
        pyautogui.click()
        logging.info(f"[红点] 点击簇 ({cx}, {cy}) 像素={pc}")
        time.sleep(0.8)
        return True, cy

    return False, None


# ==================== 动态定位最新气泡 ====================
def snap_hash(hwnd):
    img = capture_window(hwnd)
    w, h = img.size
    crop = img.crop((int(w * 0.40), int(h * 0.06), int(w * 0.79), int(h * 0.85)))
    return hashlib.md5(crop.tobytes()).hexdigest()


def get_latest_bubble_text(hwnd):
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
        return "", ""

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

    crop_y1 = chat_y1 + max(top_y - 45, 0)
    crop_y2 = chat_y1 + bottom_y + 20
    # 消息气泡区域在窗口右侧，避开左侧聊天列表栏
    crop_x1 = int(w * 0.35)
    crop_x2 = int(w * 0.79)

    crop_h = crop_y2 - crop_y1
    logging.info(f"  [OCR] 文字块: y={crop_y1}~{crop_y2}({crop_h}px), x={crop_x1}~{crop_x2}")
    if crop_h < 10:
        logging.info("[OCR] 文字块高度过小，跳过")
        return "", ""
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
                return "", ""
        ocr = _ocr
        ocr_result = ocr.predict(crop_arr)
    except Exception as e:
        logging.error(f"[OCR] PaddleOCR 识别失败: {e}")
        return "", ""

    if not ocr_result or len(ocr_result) == 0:
        logging.info("[OCR] PaddleOCR 未检测到文字")
        return "", ""

    ocr_texts = []
    for item in ocr_result:
        rec_texts = item.get("rec_texts", [])
        rec_scores = item.get("rec_scores", [])
        dt_polys = item.get("dt_polys", [])
        for i, text in enumerate(rec_texts):
            conf = rec_scores[i] if i < len(rec_scores) else 0
            if text.strip() and conf >= 0.85:
                y_center = 0
                box_height = 0
                if i < len(dt_polys):
                    poly = dt_polys[i]
                    if poly is not None and len(poly) >= 4:
                        try:
                            ys = [float(p[1]) for p in poly if len(p) >= 2]
                            if ys:
                                y_center = sum(ys) / len(ys)
                                box_height = max(ys) - min(ys)
                        except Exception:
                            pass
                ocr_texts.append((y_center, box_height, text.strip()))
                logging.info(f"  [OCR] 片段: 「{text}」 置信度={conf:.2f} Y={y_center:.0f} H={box_height:.0f}")
            elif text.strip():
                logging.info(f"  [OCR] 丢弃低置信度片段: 「{text}」 置信度={conf:.2f}")

    ocr_texts.sort(key=lambda x: x[0])

    if len(ocr_texts) >= 2:
        heights = [t[1] for t in ocr_texts]
        max_h = max(heights)
        min_h = min(heights)
        if max_h > 0 and min_h > 0 and max_h >= min_h * 1.5:
            msg_texts = [t for t in ocr_texts if t[1] >= max_h * 0.7]
            logging.info(f"  [过滤] 群聊: 排除昵称文本(H={min_h:.0f}), 保留消息(H={max_h:.0f})")
            raw = "\n".join(t[2] for t in msg_texts)
        else:
            raw = "\n".join(t[2] for t in ocr_texts)
    else:
        raw = "\n".join(t[2] for t in ocr_texts)
    logging.info(f"  [OCR] 输出: 「{raw[:80]}...」" if len(raw) > 80 else f"  [OCR] 输出: 「{raw}」")

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

    return result, ""


# ==================== AI 后端（v29：可插拔） ====================
_coze_fail_count = 0
COZE_FAIL_BACKOFF_MAX = 5

def _coze_reply(content, chat_user_id):
    """Coze API v3 后端（内置默认）"""
    global _coze_fail_count

    if _coze_fail_count >= COZE_FAIL_BACKOFF_MAX:
        logging.warning(f"[Coze] 已连续失败{_coze_fail_count}次，跳过本次请求")
        return None, False

    headers = {
        "Authorization": f"Bearer {COZE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "bot_id": BOT_ID,
        "user_id": chat_user_id,
        "stream": False,
        "auto_save_history": True,
        "additional_messages": [{"role": "user", "content": content, "content_type": "text"}]
    }

    total_start = time.time()
    COZE_TOTAL_TIMEOUT = 60

    try:
        logging.info(f"[Coze] POST 请求: bot_id={BOT_ID} user_id={chat_user_id} content={content[:60]}...")
        resp = requests.post(COZE_CHAT_URL, headers=headers, json=payload, timeout=COZE_TIMEOUT)
        if time.time() - total_start > COZE_TOTAL_TIMEOUT:
            logging.error("[Coze] 总超时——POST阶段")
            _coze_fail_count += 1
            return None, True

        logging.info(f"[Coze] POST 状态码: {resp.status_code}")
        if resp.status_code != 200:
            logging.error(f"[Coze] POST 失败: {resp.status_code} {resp.text[:300]}")
            _coze_fail_count += 1
            return None, True

        data = resp.json()
        if "data" not in data:
            logging.error(f"[Coze] POST 响应无data: {json.dumps(data, ensure_ascii=False)[:500]}")
            _coze_fail_count += 1
            return None, True

        logging.debug(f"[Coze] POST 完整响应: {json.dumps(data, ensure_ascii=False)}")
        chat_id = data["data"]["id"]
        conv_id = data["data"]["conversation_id"]
        logging.info(f"[Coze] chat_id={chat_id[:20]}... 开始轮询")

        for attempt in range(COZE_POLL_MAX):
            if time.time() - total_start > COZE_TOTAL_TIMEOUT:
                logging.error(f"[Coze] 总超时——轮询阶段(attempt={attempt})")
                _coze_fail_count += 1
                return None, True

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
            logging.debug(f"[Coze] 轮询 attempt={attempt} status={status} 完整响应: {json.dumps(d, ensure_ascii=False)[:500]}")
            if status == "completed":
                break
            elif status == "failed":
                logging.error(f"[Coze] 对话失败: {d['data'].get('last_error', '')}")
                _coze_fail_count += 1
                return None, True
        else:
            logging.warning("[Coze] 轮询超时")
            _coze_fail_count += 1
            return None, True

        if time.time() - total_start > COZE_TOTAL_TIMEOUT:
            logging.error("[Coze] 总超时——获取消息列表阶段")
            _coze_fail_count += 1
            return None, True

        logging.info(f"[Coze] 对话完成，获取消息列表...")
        r = requests.get(COZE_MESSAGE_LIST_URL, headers=headers,
                         params={"chat_id": chat_id, "conversation_id": conv_id}, timeout=COZE_TIMEOUT)
        if r.status_code != 200:
            logging.error(f"[Coze] 消息列表请求失败: {r.status_code}")
            _coze_fail_count += 1
            return None, True

        msg_data = r.json()
        messages = msg_data.get("data", [])

        logging.info(f"[Coze] 消息列表完整响应: {json.dumps(msg_data, ensure_ascii=False)}")

        if not messages:
            messages = msg_data if isinstance(msg_data, list) else []

        logging.info(f"[Coze] 获取到 {len(messages)} 条消息")

        for i, msg in enumerate(messages):
            role = msg.get("role", "?")
            msg_type = msg.get("type", "?")
            logging.info(f"[Coze] msg[{i}] role={role} type={msg_type}")
            content_raw = msg.get("content", "")
            if role == "assistant" or msg_type == "answer":
                logging.info(f"[Coze] msg[{i}] 完整数据: {json.dumps(msg, ensure_ascii=False)}")
                if isinstance(content_raw, str) and content_raw.strip():
                    logging.info(f"[Coze] 提取回复: {content_raw[:100]}")
                    _coze_fail_count = 0
                    return content_raw, True
                elif isinstance(content_raw, list):
                    texts = []
                    for block in content_raw:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                    if texts:
                        result = "".join(texts)
                        logging.info(f"[Coze] 提取回复(列表): {result[:100]}")
                        _coze_fail_count = 0
                        return result, True

        logging.warning(f"[Coze] 未找到assistant回复")
        return "（Coze 未生成回复）", True

    except requests.exceptions.Timeout:
        logging.error("[Coze] 请求超时")
        _coze_fail_count += 1
        return None, True
    except Exception as e:
        logging.error(f"[Coze] 异常: {e}")
        _coze_fail_count += 1
        return None, True


def _openai_reply(content, chat_user_id):
    """
    OpenAI 兼容后端示例（GPT / DeepSeek / 本地 Ollama 等）
    启用方法:
      1. pip install openai
      2. config.py: AI_BACKEND = "openai"
      3. config.py: 填 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
    """
    try:
        from openai import OpenAI
    except ImportError:
        logging.error("[OpenAI] 请先安装: pip install openai")
        return None, True

    try:
        from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
    except ImportError:
        logging.error("[OpenAI] 请在 config.py 配置 OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL")
        return None, True

    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": content}],
        timeout=60,
    )
    reply = resp.choices[0].message.content
    logging.info(f"[OpenAI] 回复: {reply[:100]}")
    return reply, True


# ★ 后端注册表 —— 添加新后端只需在这里加一行
ai_backends = {
    "coze": _coze_reply,
    "openai": _openai_reply,
    # "custom": your_custom_function,  # 自定义后端示例
}


def ai_reply(content, chat_user_id="wx-default"):
    """
    统一 AI 回复入口。
    根据 config.py 的 AI_BACKEND 自动选择后端，
    所有后端签名统一: (content, chat_user_id) -> (reply_text, success)
    """
    backend_fn = ai_backends.get(AI_BACKEND)
    if backend_fn is None:
        logging.error(f"[AI] 未知后端: '{AI_BACKEND}'，可用: {list(ai_backends.keys())}")
        return None, True
    logging.debug(f"[AI] 使用后端: {AI_BACKEND}")
    return backend_fn(content, chat_user_id)


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
    """点击左侧聊天列表安全区域返回焦点，避开标题栏按钮"""
    l, t, r, b = get_rect(hwnd)
    w = r - l
    h = b - t
    # 点击左侧8%宽度、50%高度位置，位于聊天列表深处，不会碰到标题栏按钮
    pyautogui.click(l + int(w * 0.08), t + int(h * 0.5))
    time.sleep(0.3)


# ==================== 单条消息处理 ====================
def process_one_message(hwnd, last_msg_hash, chat_user_id):
    time.sleep(0.8)
    pyautogui.press('end')
    time.sleep(0.6)

    msg, _ = get_latest_bubble_text(hwnd)
    if not msg:
        logging.info("[处理] 未识别到文字 → 可能是图片/语音/视频")
        return 'no_text', last_msg_hash

    logging.info(f" 收到消息: {msg}")

    global _ocr_cache
    if msg == _ocr_cache["text"]:
        logging.info("[缓存] OCR结果与上次相同，跳过")
        return 'duplicate', last_msg_hash
    _ocr_cache["text"] = msg

    mh = hashlib.md5(msg.encode()).hexdigest()[:8]
    if mh == last_msg_hash:
        logging.info("[去重] 当前会话消息重复")
        return 'duplicate', last_msg_hash

    if not COZE_ENABLED:
        question = msg
        logging.info(f"[采集] 入库: {question[:60]}")
        try:
            _stats.record(question, nickname="", reply_full="", chat_user_id=chat_user_id)
        except Exception:
            pass
        return 'ok', mh

    question = re.sub(rf'\s*{re.escape(WAKE_WORD)}\s*', ' ', msg).strip() if WAKE_WORD in msg else msg
    if WAKE_WORD not in msg:
        logging.info("[跳过] 未包含唤醒词")
        return 'no_wake', last_msg_hash

    if not question:
        question = "你好"
    logging.info(f"[触发] 提问: {question}")

    reply, ok = ai_reply(question, chat_user_id)
    reply_body = parse_coze_category(reply)[1] if reply else ""
    if reply and ok:
        try:
            _stats.record(question, nickname="", reply_full=reply, chat_user_id=chat_user_id)
        except Exception:
            pass
        send_text = reply_body or reply
        send_message(hwnd, send_text)
    elif not ok:
        logging.warning(f"[AI] {AI_BACKEND} 请求失败，跳过回复")
    else:
        send_message(hwnd, "抱歉，暂时无法回答")
    return 'ok', mh


# ==================== 主循环 ====================
def main():
    hwnd = try_restore_wechat()
    if hwnd:
        logging.info("✅ 微信窗口已恢复")
    elif _is_wechat_running():
        logging.info("🔍 微信在后台运行（托盘），尝试激活...")
        if activate_tray_wechat():
            hwnd = _find_wechat_by_class()
            logging.info("✅ 微信窗口已从托盘激活")
        else:
            logging.warning("[窗口] 无法激活托盘微信，尝试重新启动...")
            launch_wechat()
            hwnd = wait_wechat(60)
    else:
        logging.info("🚀 微信未运行，正在启动...")
        if not launch_wechat():
            logging.error("❌ 未找到微信安装路径，请在 config.py 中配置 WECHAT_PATH")
            logging.error("   例如: WECHAT_PATH = r\"C:\\Program Files\\Tencent\\WeChat\\WeChat.exe\"")
            return
        logging.info("⏳ 等待微信窗口就绪（最多60秒）...")
        hwnd = wait_wechat(60)
    if not hwnd:
        logging.error("❌ 无法获取微信窗口，请检查微信是否正常")
        return

    init_ocr()
    global _stats
    _stats = QuestionStats(STATS_DB_PATH)
    pyautogui.PAUSE = 0.2
    pyautogui.FAILSAFE = False

    per_chat_hash = {}
    last_snap_hash = ""
    no_red_dot_count = 0
    total_processed = 0
    last_processed_chat_hash = ""

    SAME_CHAT_MAX_PASS = 4
    IDLE_INTERVAL = 3.0
    WINDOW_RECHECK_INTERVAL = 30
    last_activity_time = time.time()
    last_window_check = 0

    logging.info(f"🤖 机器人启动，AI后端: {AI_BACKEND} | 唤醒词: {WAKE_WORD}")
    logging.info("📱 自适应间隔: 活跃1s / 空闲3s | 熔断退避: 连续5次失败暂停")
    logging.info("🔴 多红点模式：Y轴聚类 → 同聊多轮 → 逐个击破")

    while True:
        if KEYBOARD_AVAILABLE and keyboard.is_pressed('-'):
            logging.info("检测到退出键")
            break

        now_ts = time.time()
        if now_ts - last_window_check > WINDOW_RECHECK_INTERVAL:
            last_window_check = now_ts
            if not _find_wechat_by_class():
                logging.warning("[窗口] 微信窗口丢失，重新启动...")
                launch_wechat()
                new_hwnd = wait_wechat(30)
                if new_hwnd:
                    hwnd = new_hwnd
                    restore_wechat_window(hwnd)
                    per_chat_hash.clear()
                    no_red_dot_count = 0
                    logging.info("[窗口] 微信窗口已恢复，状态已重置")
                else:
                    logging.warning("[窗口] 仍未找到微信窗口，等待10秒...")
                    time.sleep(10)
                    continue

        restore_wechat_window(hwnd)

        idle_sec = time.time() - last_activity_time
        if idle_sec > 60:
            sleep_time = max(IDLE_INTERVAL, CHECK_INTERVAL)
        else:
            sleep_time = CHECK_INTERVAL

        def handle_current_chat():
            nonlocal total_processed, last_processed_chat_hash
            # 用 clicked_y 生成唯一会话ID，避免昵称冲突导致跨会话去重污染
            chat_key = f"chat-y{clicked_y}" if clicked_y else "chat-default"
            mh = per_chat_hash.get(chat_key, "")
            processed = 0
            first_sh = snap_hash(hwnd)
            if first_sh == last_processed_chat_hash and last_processed_chat_hash:
                logging.info("[切换] 聊天未切换，停留在上次会话，跳过")
                return
            last_sh = first_sh

            for _pass in range(SAME_CHAT_MAX_PASS):
                if _pass > 0:
                    cur = snap_hash(hwnd)
                    if cur == last_sh:
                        break
                last_sh = snap_hash(hwnd)

                rt, mh, nick = process_one_message(hwnd, mh, chat_key)
                per_chat_hash[chat_key] = mh
                if rt == 'ok':
                    processed += 1
                    total_processed += 1
                    logging.info(f"[统计] 本轮第{_pass+1}条 | 累计={total_processed}")
                elif rt == 'no_text':
                    break
                else:
                    break

            last_processed_chat_hash = snap_hash(hwnd)

        clicked, clicked_y = click_red_dot(hwnd)
        if not clicked:
            no_red_dot_count += 1
            if no_red_dot_count >= NO_RED_DOT_FALLBACK:
                logging.info(f"[降级] 连续{NO_RED_DOT_FALLBACK}次无红点，强制检查当前聊天")
                no_red_dot_count = 0
                last_activity_time = time.time()
                per_chat_hash.clear()
                process_one_message(hwnd, "", "wx-default")
            else:
                time.sleep(sleep_time)
            continue

        no_red_dot_count = 0
        last_activity_time = time.time()

        handle_current_chat()

        if len(per_chat_hash) > 50:
            per_chat_hash.pop(next(iter(per_chat_hash)), None)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
