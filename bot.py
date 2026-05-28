# -*- coding: utf-8 -*-
"""
企业微信智能机器人 v2.0
========================================
纯企业微信(WeCom)专用版，移除所有普通微信兼容代码
========================================
"""
import sys
import os as _os

_os.environ["FLAGS_use_onednn"] = "0"
_os.environ["FLAGS_onednn_use_cache"] = "0"
_os.environ["KMP_AFFINITY"] = "disabled"
_os.environ["KMP_BLOCKTIME"] = "0"
_os.environ["GLOG_minloglevel"] = "3"
_os.environ["GLOG_logtostderr"] = "0"
import re
import json
import time
import hashlib
import logging
import subprocess
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
    AUTO_LAUNCH,
    WECOM_PATH,
    COZE_API_TOKEN, BOT_ID,
    COZE_CHAT_URL, COZE_RETRIEVE_URL, COZE_MESSAGE_LIST_URL,
    CHECK_INTERVAL, NO_RED_DOT_FALLBACK,
    COZE_TIMEOUT, COZE_POLL_MAX,
    LOG_LEVEL,
    PADDLEOCR_LANG, PADDLEOCR_USE_ANGLE_CLS,
    STATS_DB_PATH,
)

from question_stats import QuestionStats, parse_coze_category

# ==================== 企业微信常量 ====================
_WECOM_CLASS_NAMES = ("WeWorkWindow", "Qt51514QWindowIcon")
_WECOM_TITLE_KW = "企业微信"
_WECOM_PROCESS = "WXWork.exe"
_WECOM_INSTALL_PATHS = [
    r"%ProgramFiles%\WXWork\WXWork.exe",
    r"%ProgramFiles(x86)%\WXWork\WXWork.exe",
    r"%LocalAppData%\Programs\WXWork\WXWork.exe",
]

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(levelname)s - %(message)s'
)

ctypes.windll.shcore.SetProcessDpiAwareness(2)

_ocr = None
_stats = None
_ocr_cache = {"text": "", "nickname": ""}


def init_ocr():
    global _ocr
    logging.info("[OCR] 正在预加载 PaddleOCR 模型...")
    _ocr = PaddleOCR(use_textline_orientation=PADDLEOCR_USE_ANGLE_CLS, lang=PADDLEOCR_LANG, enable_mkldnn=False)
    logging.info("[OCR] PaddleOCR 预加载完成")
    return _ocr


# ==================== 窗口工具 ====================
def _find_wecom_by_class():
    """通过窗口类名找到企业微信窗口（可见优先）"""
    candidates = []
    all_windows = []

    def enum_cb(hwnd, _):
        class_name = win32gui.GetClassName(hwnd)
        if class_name in _WECOM_CLASS_NAMES:
            if win32gui.IsWindow(hwnd):
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                area = max(0, r - l) * max(0, b - t)
                visible = win32gui.IsWindowVisible(hwnd)
                candidates.append((hwnd, area * (100 if visible else 1), visible))
        if win32gui.IsWindowVisible(hwnd):
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if w > 300 and h > 300:
                all_windows.append((hwnd, class_name, win32gui.GetWindowText(hwnd)[:80], w, h))

    win32gui.EnumWindows(enum_cb, None)

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        best, area, visible = candidates[0]
        logging.info(f"[窗口] 类名枚举: hwnd={best} area={area} visible={visible} "
                     f"title='{win32gui.GetWindowText(best)}' candidates={len(candidates)}")
        return best

    logging.warning(f"[窗口] 未找到已知类名 {_WECOM_CLASS_NAMES} 的窗口")
    logging.warning("[诊断] 当前所有可见大窗口:")
    all_windows.sort(key=lambda x: x[3] * x[4], reverse=True)
    for hwnd, cls, title, w, h in all_windows[:15]:
        logging.warning(f"  class='{cls}' title='{title}' size={w}x{h}")
    return None


def _find_wecom_by_process():
    """通过 WXWork.exe 进程找到主窗口"""
    results = []

    def enum_cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
            if handle:
                name_buf = ctypes.create_unicode_buffer(260)
                size = ctypes.wintypes.DWORD(260)
                if ctypes.windll.psapi.GetModuleBaseNameW(handle, None, name_buf, size):
                    if name_buf.value.lower() == "wxwork.exe":
                        l, t, r, b = win32gui.GetWindowRect(hwnd)
                        w, h = r - l, b - t
                        if w > 500 and h > 400:
                            results.append((hwnd, w * h))
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass

    win32gui.EnumWindows(enum_cb, None)
    if results:
        results.sort(key=lambda x: x[1], reverse=True)
        return results[0][0]
    return None


def get_wecom_hwnd():
    """获取企业微信窗口句柄（多策略兜底）"""
    # 1. 类名匹配
    hwnd = _find_wecom_by_class()
    if hwnd:
        l, t, r, b = get_rect(hwnd)
        logging.info(f"[窗口] 类名匹配: hwnd={hwnd} title='{win32gui.GetWindowText(hwnd)}' "
                     f"class='{win32gui.GetClassName(hwnd)}' rect=({l},{t},{r},{b}) size={r-l}x{b-t}")
        return hwnd

    # 2. 进程名匹配
    hwnd = _find_wecom_by_process()
    if hwnd:
        l, t, r, b = get_rect(hwnd)
        logging.info(f"[窗口] 进程名匹配: hwnd={hwnd} title='{win32gui.GetWindowText(hwnd)}' "
                     f"class='{win32gui.GetClassName(hwnd)}' rect=({l},{t},{r},{b}) size={r-l}x{b-t}")
        return hwnd

    # 3. 标题匹配兜底
    results = []
    def callback(hwnd, extra):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if _WECOM_TITLE_KW in title:
            l, t, r, b = win32gui.GetWindowRect(hwnd)
            w, h = r - l, b - t
            if w > 500 and h > 400:
                extra.append(hwnd)
    win32gui.EnumWindows(callback, results)
    if results:
        l, t, r, b = get_rect(results[0])
        logging.info(f"[窗口] 标题匹配: hwnd={results[0]} title='{win32gui.GetWindowText(results[0])}' "
                     f"rect=({l},{t},{r},{b})")
        return results[0]

    logging.error("[窗口] 所有方式均未找到企业微信窗口")
    return None


def restore_wecom_window(hwnd):
    if not AUTO_LAUNCH:
        return
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if not win32gui.IsWindowVisible(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    time.sleep(0.5)
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        pass


def _is_wecom_running():
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            ['tasklist', '/FI', f'IMAGENAME eq {_WECOM_PROCESS}', '/FO', 'CSV', '/NH'],
            capture_output=True, text=True, startupinfo=si,
        )
        return _WECOM_PROCESS in result.stdout
    except Exception:
        return False


def try_restore_wecom():
    hwnd = _find_wecom_by_class()
    if hwnd:
        restore_wecom_window(hwnd)
        return hwnd
    return None


def activate_tray_wecom():
    if not _is_wecom_running():
        return False
    try:
        pyautogui.hotkey('ctrl', 'alt', 'w')
        time.sleep(1)
        hwnd = _find_wecom_by_class()
        if hwnd:
            restore_wecom_window(hwnd)
            return True
        time.sleep(2)
        hwnd = _find_wecom_by_class()
        if hwnd:
            restore_wecom_window(hwnd)
            return True
    except Exception:
        pass
    return False


def launch_wecom():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    path = WECOM_PATH
    if path and _os.path.exists(path):
        logging.info(f"[窗口] 正在启动企业微信: {path}")
        subprocess.Popen(path, startupinfo=si)
        return True
    for p in _WECOM_INSTALL_PATHS:
        p = _os.path.expandvars(p)
        if _os.path.exists(p):
            logging.info(f"[窗口] 正在启动企业微信: {p}")
            subprocess.Popen(p, startupinfo=si)
            return True
    return False


def wait_wecom(timeout=30):
    for _ in range(timeout):
        time.sleep(1)
        hwnd = get_wecom_hwnd()
        if hwnd:
            return hwnd
    return None


def get_rect(hwnd):
    return win32gui.GetWindowRect(hwnd)


def ensure_visible(hwnd):
    if not AUTO_LAUNCH:
        return
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.4)


def bring_to_front(hwnd):
    if not AUTO_LAUNCH:
        return
    ensure_visible(hwnd)
    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
    except Exception:
        pass


# ==================== 截图 ====================
def capture_window(hwnd):
    l, t, r, b = get_rect(hwnd)
    w, h = r - l, b - t
    return pyautogui.screenshot(region=(l, t, w, h))


# ==================== 红点检测（企业微信专用 最终完美版） ====================
def find_red_dot_clusters(hwnd):
    """
    企业微信红点扫描（100%匹配实际红点像素数）
    单个红点 ~140-160像素，簇大小 100-200
    """
    try:
        l, t, r, b = get_rect(hwnd)
        h_total = b - t

        scan_l = 90
        scan_r = 120
        scan_t = int(h_total * 0.08)
        scan_b = int(h_total * 0.92)

        full_img = capture_window(hwnd)
        screenshot = full_img.crop((scan_l, scan_t, scan_r, scan_b))
        sw, sh = screenshot.size

        red_background = []
        for y in range(0, sh, 1):
            for x in range(0, sw, 1):
                r_pix, g_pix, b_pix = screenshot.getpixel((x, y))

                is_red_bg = (
                    r_pix > 150
                    and g_pix < 100
                    and b_pix < 100
                    and r_pix - g_pix > 50
                    and r_pix - b_pix > 50
                )
                is_blue = (b_pix > r_pix + 20 and b_pix > g_pix + 20)
                is_green = (g_pix > r_pix + 20 and g_pix > b_pix + 20)

                if is_red_bg and not is_blue and not is_green:
                    red_background.append((
                        l + scan_l + x, t + scan_t + y,
                        x, y, r_pix, g_pix, b_pix
                    ))

        total_red = len(red_background)
        region_w, region_h = scan_r - scan_l, scan_b - scan_t
        logging.info(f"[红点扫描] 区域={region_w}x{region_h}  红底像素={total_red}")

        if total_red < 100:
            logging.info(f"[红点] 像素数不足（仅{total_red}），判定为噪点")
            return []

        red_background.sort(key=lambda p: p[1])

        # 二维聚类
        clusters = []
        current_cluster = [red_background[0]]
        for i in range(1, len(red_background)):
            y_diff = abs(red_background[i][1] - red_background[i - 1][1])
            x_diff = abs(red_background[i][0] - red_background[i - 1][0])
            if y_diff <= 20 and x_diff <= 15:
                current_cluster.append(red_background[i])
            else:
                if 100 <= len(current_cluster) <= 200:
                    clusters.append(current_cluster)
                current_cluster = [red_background[i]]
        if 100 <= len(current_cluster) <= 200:
            clusters.append(current_cluster)

        if not clusters:
            logging.info("[红点] 无符合大小的有效红点")
            return []

        result = []
        for ci, c in enumerate(clusters):
            avg_x = sum(p[0] for p in c) // len(c)
            avg_y = sum(p[1] for p in c) // len(c)
            r0, g0, b0 = c[0][4], c[0][5], c[0][6]
            result.append((avg_x, avg_y, len(c)))
            logging.info(f"  [有效红点{ci+1}] 中心=({avg_x},{avg_y})  像素={len(c)}  颜色=({r0},{g0},{b0})")

        logging.info(f"[红点] 最终检测到 {len(result)} 个未读会话")
        return result

    except Exception as e:
        logging.error(f"[红点] 扫描异常: {e}", exc_info=True)
    return []


def click_red_dot(hwnd):
    clusters = find_red_dot_clusters(hwnd)
    if not clusters:
        return False, None

    for cx, cy, pc in reversed(clusters):
        pyautogui.moveTo(cx, cy, duration=0.1)
        pyautogui.click()
        logging.info(f"[红点] 精准点击 ({cx}, {cy}) 像素={pc}")
        time.sleep(0.8)
        return True, cy

    return False, None


# ==================== OCR 文字识别（企业微信专用 终极颜色放宽版） ====================
def snap_hash(hwnd):
    img = capture_window(hwnd)
    w, h = img.size
    crop = img.crop((int(w * 0.30), int(h * 0.08), int(w * 0.95), int(h * 0.78)))
    return hashlib.md5(crop.tobytes()).hexdigest()


def get_latest_bubble_text(hwnd):
    """
    企业微信OCR终极版：双模式智能分割
    模式1：坐标正常→物理间距分割 | 模式2：坐标异常→内容特征分割
    """
    try:
        img = capture_window(hwnd)
        w, h = img.size
        arr = np.array(img)
        logging.info(f"  [OCR] 窗口尺寸: {w}x{h}")

        gray_mask = (
            (arr[:, :, 0] > 180) & (arr[:, :, 0] < 255) &
            (arr[:, :, 1] > 180) & (arr[:, :, 1] < 255) &
            (arr[:, :, 2] > 180) & (arr[:, :, 2] < 255) &
            (np.abs(arr[:, :, 0] - arr[:, :, 1]) < 30) &
            (np.abs(arr[:, :, 1] - arr[:, :, 2]) < 30) &
            (np.abs(arr[:, :, 0] - arr[:, :, 2]) < 30)
        )
        row_gray = gray_mask.sum(axis=1)

        total_gray = gray_mask.sum()
        logging.info(f"  [调试] 总灰色像素: {total_gray}")
        if total_gray > 0:
            logging.info(f"  [调试] 单行最大灰色像素: {row_gray.max()}")

        bubble_bottom_y = -1
        for yi in range(int(h * 0.78) - 1, int(h * 0.08), -1):
            if row_gray[yi] >= 3:
                bubble_bottom_y = yi
                break

        if bubble_bottom_y < 0:
            logging.info("[OCR] 未找到对方消息气泡")
            Image.fromarray((gray_mask * 255).astype(np.uint8)).save("gray_mask_debug.png")
            return "", ""

        logging.info(f"  [OCR] 气泡底部Y={bubble_bottom_y}")

        bubble_top_y = bubble_bottom_y
        gap = 0
        for yi in range(bubble_bottom_y, int(h * 0.08), -1):
            if row_gray[yi] >= 3:
                bubble_top_y = yi
                gap = 0
            else:
                gap += 1
                if gap >= 20:
                    break

        bubble_slice = gray_mask[bubble_top_y:bubble_bottom_y + 1, :]
        col_gray = bubble_slice.sum(axis=0)
        bubble_left_x = w
        for xi in range(int(w * 0.30), int(w * 0.95)):
            if col_gray[xi] >= 1:
                bubble_left_x = xi
                break
        bubble_right_x = 0
        for xi in range(int(w * 0.95) - 1, int(w * 0.30), -1):
            if col_gray[xi] >= 1:
                bubble_right_x = xi
                break

        bh = bubble_bottom_y - bubble_top_y + 1
        bw = bubble_right_x - bubble_left_x + 1
        logging.info(f"  [调试] 气泡尺寸: {bw}x{bh}")
        if bh < 15 or bw < 50:
            logging.info("[OCR] 气泡尺寸过小")
            return "", ""
        if bubble_left_x >= bubble_right_x or bw < 50:
            bubble_left_x, bubble_right_x = int(w * 0.30), int(w * 0.80)
            bh = 40
            logging.info("  [OCR] 边界检测失败，使用默认区域")

        crop_y1 = max(bubble_top_y - 50, int(h * 0.08))
        crop_y2 = min(bubble_bottom_y + 10, int(h * 0.78))
        crop_x1 = max(bubble_left_x - 30, int(w * 0.30))
        crop_x2 = min(bubble_right_x + 30, int(w * 0.95))

        crop_h = crop_y2 - crop_y1
        crop_w = crop_x2 - crop_x1
        logging.info(f"  [OCR] 裁剪: {crop_w}x{crop_h} @({crop_x1},{crop_y1})")
        if crop_h < 20 or crop_w < 50:
            logging.info("[OCR] 裁剪区域过小")
            return "", ""

        crop = img.crop((crop_x1, crop_y1, crop_x2, crop_y2))
        crop.save("latest_bubble.png")

        if _ocr is None:
            init_ocr()
            if _ocr is None:
                return "", ""
        ocr_result = _ocr.predict(np.array(crop))
        if not ocr_result or len(ocr_result) == 0:
            logging.info("[OCR] 未检测到文字")
            return "", ""

        toolbar_kw = {"快速会议", "表情", "文件", "截图", "投票", "收藏", "I", "l", "|", "丨"}
        lines = []
        for item in ocr_result:
            rec_texts = item.get("rec_texts", [])
            rec_scores = item.get("rec_scores", [])
            det_boxes = item.get("det_boxes", [])
            dt_polys = item.get("dt_polys", [])
            for i, text in enumerate(rec_texts):
                conf = rec_scores[i] if i < len(rec_scores) else 0
                txt = text.strip()
                if not txt or conf < 0.20 or txt in toolbar_kw:
                    continue

                y_top, y_bottom = 0, 0
                # 兼容 det_boxes 和 dt_polys 两种格式
                if i < len(det_boxes) and len(det_boxes[i]) == 4:
                    try:
                        ys = [p[1] for p in det_boxes[i] if len(p) >= 2]
                        if ys:
                            y_top, y_bottom = min(ys), max(ys)
                    except Exception:
                        pass
                if (y_top == 0 and y_bottom == 0
                        and i < len(dt_polys) and dt_polys[i] is not None
                        and len(dt_polys[i]) >= 4):
                    try:
                        ys = [float(p[1]) for p in dt_polys[i] if len(p) >= 2]
                        if ys:
                            y_top, y_bottom = min(ys), max(ys)
                    except Exception:
                        pass

                if y_top == 0 and y_bottom == 0:
                    y_top = i * 20
                    y_bottom = y_top + 18

                y_center = (y_top + y_bottom) / 2
                line_h = y_bottom - y_top
                lines.append((y_top, y_bottom, y_center, line_h, txt, conf))
                logging.info(f"  [OCR] y={y_center:.0f} h={line_h:.0f} 「{txt}」 置信度={conf:.2f}")

        if not lines:
            logging.info("[OCR] 无有效文字")
            return "", ""

        lines.sort(key=lambda x: x[2])
        logging.info(f"  [OCR] 共 {len(lines)} 行")

        # 基于内容特征分割
        # 昵称后缀：企业微信固定格式 @微信 / @微言 / @言
        # 普通 @xxx 是消息内的@提及，不是昵称
        watermarks = {"6688", "康家镇", "如米公寓", "康家镇@如米公寓"}
        nickname = ""
        message_parts = []
        for l in lines:
            txt = l[4]
            is_watermark = any(w in txt for w in watermarks)
            is_nick = ("@微信" in txt or "@微言" in txt or "@言" in txt)
            if is_watermark:
                logging.info(f"  [分割] 水印:「{txt}」丢弃")
            elif is_nick:
                nickname = re.split(r'@(?:微信|微言|言)', txt)[0].strip()
                logging.info(f"  [分割] 昵称:「{txt}」→「{nickname}」")
            else:
                message_parts.append(txt)
                logging.info(f"  [分割] 消息:「{txt}」")
        message = " ".join(message_parts).strip()
        if not message and not nickname:
            message = " ".join([l[4] for l in lines]).strip()
            logging.info(f"  [分割] 无特征→纯正文:「{message}」")

        # 昵称清理
        if nickname and "@" in nickname:
            nickname = nickname.split("@")[0].strip()
        nickname = re.sub(r'[^\w一-龥]', '', nickname)
        nickname = re.sub(r'康家镇|如米公寓|6688', '', nickname).strip()

        # 正文水印清理
        for pat in [r'康家镇@如米公寓\s*\d*', r'康家镇@如米公寓',
                     r'如米公寓\s*\d*', r'康家镇\s*\d*', r'\b6688\b',
                     r'\b\d{1,2}:\d{2}\b']:
            message = re.sub(pat, '', message).strip()
        message = re.sub(r'\s+', ' ', message).strip()
        message = re.sub(r'[》《|丨•·▪▫▶◀]', '', message).strip()
        message = re.sub(r'(?<=[一-鿿])\s+(?=[一-鿿])', '', message)
        message = re.sub(r'@\s+(?=[一-鿿])', '@', message)

        if not message:
            logging.info("[OCR] 清理后无有效消息")
            return "", ""

        logging.info(f"✅ 发送者:「{nickname}」 消息:「{message}」")
        return message, nickname

    except Exception as e:
        logging.error(f"[OCR] 识别异常: {e}", exc_info=True)
        return "", ""


# ==================== AI 后端（可插拔） ====================
_coze_fail_count = 0
COZE_FAIL_BACKOFF_MAX = 5


def _coze_reply(content, chat_user_id):
    """Coze API v3 后端"""
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
            logging.debug(f"[Coze] 轮询 attempt={attempt} status={status}")
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

        logging.info("[Coze] 对话完成，获取消息列表...")
        r = requests.get(COZE_MESSAGE_LIST_URL, headers=headers,
                         params={"chat_id": chat_id, "conversation_id": conv_id}, timeout=COZE_TIMEOUT)
        if r.status_code != 200:
            logging.error(f"[Coze] 消息列表请求失败: {r.status_code}")
            _coze_fail_count += 1
            return None, True

        msg_data = r.json()
        messages = msg_data.get("data", [])
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

        logging.warning("[Coze] 未找到assistant回复")
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
    """OpenAI 兼容后端（GPT / DeepSeek / Ollama 等）"""
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


ai_backends = {
    "coze": _coze_reply,
    "openai": _openai_reply,
}


def ai_reply(content, chat_user_id="wecom-default"):
    """统一 AI 回复入口"""
    backend_fn = ai_backends.get(AI_BACKEND)
    if backend_fn is None:
        logging.error(f"[AI] 未知后端: '{AI_BACKEND}'，可用: {list(ai_backends.keys())}")
        return None, True
    logging.debug(f"[AI] 使用后端: {AI_BACKEND}")
    return backend_fn(content, chat_user_id)


# ==================== 发送消息（企业微信专用） ====================
def send_message(hwnd, text):
    """发送纯文本消息"""
    ensure_visible(hwnd)
    l, t, r, b = get_rect(hwnd)
    w, h = r - l, b - t
    input_x = l + int(w * 0.55)
    input_y = t + int(h * 0.92)

    bring_to_front(hwnd)
    pyautogui.click(input_x, input_y)
    time.sleep(0.3)

    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.25)
    pyautogui.press('enter')
    time.sleep(0.35)

    if AUTO_LAUNCH:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)


def send_mention_reply(hwnd, nickname, text):
    """企业微信 @某人 + 回复"""
    ensure_visible(hwnd)
    l, t, r, b = get_rect(hwnd)
    w, h = r - l, b - t
    input_x = l + int(w * 0.55)
    input_y = t + int(h * 0.92)

    bring_to_front(hwnd)
    pyautogui.click(input_x, input_y)
    time.sleep(0.3)

    pyautogui.press('@')
    time.sleep(0.6)

    pyperclip.copy(nickname)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.4)

    pyautogui.press('enter')
    time.sleep(0.4)

    pyperclip.copy(text)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.25)

    pyautogui.press('enter')
    time.sleep(0.35)

    if AUTO_LAUNCH:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)


def back_to_chat_list(hwnd):
    """点击左侧聊天列表安全区域"""
    l, t, r, b = get_rect(hwnd)
    pyautogui.click(l + int((r - l) * 0.15), t + int((b - t) * 0.5))
    time.sleep(0.3)


# ==================== 单条消息处理 ====================
def process_one_message(hwnd, last_msg_hash, chat_user_id):
    time.sleep(0.8)
    pyautogui.press('end')
    time.sleep(0.6)

    msg, nickname = get_latest_bubble_text(hwnd)
    if not msg:
        logging.info("[处理] 未识别到文字 → 可能是图片/语音/视频")
        return 'no_text', last_msg_hash, nickname

    if nickname:
        nickname = re.sub(r'@\S+$', '', nickname).strip()
        logging.info(f"👤 发送者: {nickname}")
    logging.info(f"📩 收到消息: {msg}")

    global _ocr_cache
    if msg == _ocr_cache["text"] and nickname == _ocr_cache["nickname"]:
        logging.info("[缓存] OCR结果与上次相同，跳过")
        return 'duplicate', last_msg_hash, nickname
    _ocr_cache["text"] = msg
    _ocr_cache["nickname"] = nickname or ""

    mh = hashlib.md5(msg.encode()).hexdigest()[:8]
    if mh == last_msg_hash:
        logging.info("[去重] 当前会话消息重复")
        return 'duplicate', last_msg_hash, nickname

    # 采集模式：所有消息直接入库
    if not COZE_ENABLED:
        question = msg
        logging.info(f"[采集] {'私聊' if not nickname else '群聊'} 入库: {question[:60]}")
        try:
            _stats.record(question, nickname=nickname or "", reply_full="", chat_user_id=chat_user_id)
        except Exception:
            pass
        return 'ok', mh, nickname

    # 回复模式：群聊需要唤醒词，私聊直接触发
    if nickname and WAKE_WORD not in msg:
        logging.info("[跳过] 群聊未包含唤醒词")
        return 'no_wake', last_msg_hash, nickname

    question = re.sub(rf'\s*{re.escape(WAKE_WORD)}\s*', ' ', msg).strip() if WAKE_WORD in msg else msg
    if not question:
        question = "你好"
    logging.info(f"[触发] {'群聊' if nickname else '私聊'}，提问: {question}")

    reply, ok = ai_reply(question, chat_user_id)
    reply_body = parse_coze_category(reply)[1] if reply else ""
    if reply and ok:
        try:
            _stats.record(question, nickname=nickname or "", reply_full=reply, chat_user_id=chat_user_id)
        except Exception:
            pass
        send_text = reply_body or reply
        if nickname:
            send_mention_reply(hwnd, nickname, send_text)
            logging.info(f"[@] 真实@「{nickname}」回复")
        else:
            send_message(hwnd, send_text)
    elif not ok:
        logging.warning(f"[AI] {AI_BACKEND} 请求失败，跳过回复")
    else:
        send_message(hwnd, "抱歉，暂时无法回答")
    return 'ok', mh, nickname


# ==================== 主循环 ====================
def main():
    logging.info("=" * 60)
    logging.info("  企业微信智能机器人 v2.0")
    logging.info("  纯企业微信(WeCom)专用版")
    logging.info("=" * 60)

    if AUTO_LAUNCH:
        hwnd = try_restore_wecom()
        if hwnd:
            logging.info("✅ 企业微信窗口已恢复")
        elif _is_wecom_running():
            logging.info("🔍 企业微信在后台运行（托盘），尝试激活...")
            if activate_tray_wecom():
                hwnd = _find_wecom_by_class()
                logging.info("✅ 企业微信窗口已从托盘激活")
            else:
                logging.warning("[窗口] 无法激活托盘企业微信，尝试重新启动...")
                launch_wecom()
                hwnd = wait_wecom(60)
        else:
            logging.info("🚀 企业微信未运行，正在启动...")
            if not launch_wecom():
                logging.error("❌ 未找到企业微信安装路径，请在 config.py 中配置 WECOM_PATH")
                logging.error("   例如: WECOM_PATH = r\"C:\\Program Files\\WXWork\\WXWork.exe\"")
                return
            logging.info("⏳ 等待企业微信窗口就绪（最多60秒）...")
            hwnd = wait_wecom(60)
        if not hwnd:
            logging.error("❌ 无法获取企业微信窗口，请检查企业微信是否正常")
            return
    else:
        logging.info("👆 手动模式：请确保企业微信窗口已打开并在前台...")
        logging.info("   💡 提示：如果窗口被遮挡，请把企业微信放到最前")
        hwnd = get_wecom_hwnd()
        while not hwnd or not win32gui.IsWindowVisible(hwnd):
            if hwnd and not win32gui.IsWindowVisible(hwnd):
                logging.warning("⚠️ 找到企业微信窗口但不可见（可能最小化），请切换到前台...")
                hwnd = None
            time.sleep(3)
            hwnd = get_wecom_hwnd()
        l, t, r, b = get_rect(hwnd)
        logging.info(f"✅ 已找到企业微信窗口: hwnd={hwnd} rect=({l},{t},{r},{b}) size={r-l}x{b-t}")

    init_ocr()
    global _stats
    _stats = QuestionStats(STATS_DB_PATH)
    pyautogui.PAUSE = 0.2
    pyautogui.FAILSAFE = False

    per_chat_hash = {}
    no_red_dot_count = 0
    total_processed = 0
    last_processed_chat_hash = ""

    SAME_CHAT_MAX_PASS = 4
    IDLE_INTERVAL = 3.0
    WINDOW_RECHECK_INTERVAL = 30
    last_activity_time = time.time()
    last_window_check = 0

    logging.info(f"🤖 机器人启动 | AI后端: {AI_BACKEND} | 唤醒词: {WAKE_WORD} | 平台: 企业微信")
    logging.info("📱 自适应间隔: 活跃1s / 空闲3s | 熔断退避: 连续5次失败暂停")
    logging.info("🔴 红点模式：红底白字检测 → Y轴聚类 → 底部优先")

    while True:
        if KEYBOARD_AVAILABLE and keyboard.is_pressed('-'):
            logging.info("检测到退出键 (-)，正在退出...")
            break

        now_ts = time.time()
        if now_ts - last_window_check > WINDOW_RECHECK_INTERVAL:
            last_window_check = now_ts
            if not _find_wecom_by_class():
                if AUTO_LAUNCH:
                    logging.warning("[窗口] 企业微信窗口丢失，重新启动...")
                    launch_wecom()
                    new_hwnd = wait_wecom(30)
                    if new_hwnd:
                        hwnd = new_hwnd
                        restore_wecom_window(hwnd)
                        per_chat_hash.clear()
                        no_red_dot_count = 0
                        logging.info("[窗口] 企业微信窗口已恢复，状态已重置")
                    else:
                        logging.warning("[窗口] 仍未找到企业微信窗口，等待10秒...")
                        time.sleep(10)
                        continue
                else:
                    logging.warning("[窗口] 企业微信窗口丢失，等待手动恢复...")
                    while not _find_wecom_by_class():
                        time.sleep(3)
                    hwnd = get_wecom_hwnd()
                    per_chat_hash.clear()
                    no_red_dot_count = 0
                    logging.info("[窗口] 企业微信窗口已恢复，状态已重置")

        restore_wecom_window(hwnd)

        idle_sec = time.time() - last_activity_time
        if idle_sec > 60:
            sleep_time = max(IDLE_INTERVAL, CHECK_INTERVAL)
        else:
            sleep_time = CHECK_INTERVAL

        def handle_current_chat():
            nonlocal total_processed, last_processed_chat_hash
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
                process_one_message(hwnd, "", "wecom-default")
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
