#!/usr/bin/env python3
"""
墨墨動作辨識監看服務
連接 IP 攝影機 RTSP 串流，偵測移動後送 Gemini Vision 分析，結果寫入 rabbit-care。
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from dotenv import load_dotenv
from google import genai

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), 'motion-watcher.log'),
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

# ── 設定 ────────────────────────────────────────
RTSP_URL         = os.getenv('RTSP_URL', '')
RABBIT_CARE_URL  = os.getenv('RABBIT_CARE_URL', 'http://localhost:5200')
MOTION_THRESHOLD = int(os.getenv('MOTION_THRESHOLD', '500'))
COOLDOWN_SECONDS = int(os.getenv('COOLDOWN_SECONDS', '60'))
MIN_CONFIDENCE   = float(os.getenv('GEMINI_MIN_CONFIDENCE', '0.85'))
BOT_TOKEN        = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID          = os.getenv('TELEGRAM_CHAT_ID', '')

PROMPT = """這是寵物兔子（墨墨）的監控截圖（依時間順序）。
籠子擺設說明：
- 地板鋪有白色地墊（不是草地）
- 畫面右側或角落有黃色飲水容器（水碗/水瓶）
- 牧草放在固定的草架或草盤，不是直接鋪在地上

請根據兔子的具體動作，從以下選項中選一個：
- eating（嘴巴在啃咬動作，可以看到頭部反覆點動或下顎咀嚼，且頭部靠近草架/食物區）
- drinking（嘴巴靠近水容器，有飲水動作）
- resting（靜止坐著或趴著在地墊上，無明顯動作，頭可能稍低但沒有咀嚼）
- stretching（身體完全伸展開來在伸懶腰）
- sleeping（身體側躺或蜷縮，眼睛閉著或幾乎不動，長時間靜止）
- other（其他動作）

重要提示：
- 如果兔子只是靜止坐著或趴著，請選 resting，不是 eating
- 只有看到明確咀嚼或啃咬動作才選 eating
- 如果不確定，請降低 confidence 值（低於 0.7）

只回傳 JSON，格式：{"action": "resting", "confidence": 0.9}"""


_gemini_client = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    return _gemini_client


def frames_to_pil(frames: list) -> list:
    """numpy BGR frames → PIL RGB Images"""
    import cv2
    from PIL import Image
    result = []
    for f in frames:
        rgb = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
        result.append(Image.fromarray(rgb))
    return result


def analyze_frames(frames: list):
    """
    送 4 張影格給 Gemini Vision 分析。
    回傳 (action, confidence) 或 None（若 other 或信心不足）。
    """
    for attempt in range(3):
        try:
            client = _get_gemini_client()
            pil_images = frames_to_pil(frames)
            contents = pil_images + [PROMPT]
            response = client.models.generate_content(
                model='gemini-1.5-flash-latest',
                contents=contents
            )
            raw = response.text.strip()
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            data = json.loads(raw)
            action = data.get('action', 'other')
            confidence = float(data.get('confidence', 0))

            if action == 'other' or confidence < MIN_CONFIDENCE:
                return None
            return (action, confidence)
        except Exception as e:
            err_str = str(e)
            if '429' in err_str and attempt < 2:
                wait = 60 * (attempt + 1)
                logger.warning(f'Gemini 429，等待 {wait}s 後重試（第 {attempt+1} 次）')
                time.sleep(wait)
            else:
                logger.error(f'Gemini 分析失敗: {e}')
                return None
    return None


ACTION_EMOJI = {'eating': '🍽', 'drinking': '💧', 'stretching': '🐇', 'sleeping': '😴', 'resting': '🐰'}
ACTION_LABEL = {'eating': '吃飯', 'drinking': '喝水', 'stretching': '伸懶腰', 'sleeping': '睡覺', 'resting': '休息'}

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), 'static', 'action_screenshots')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def save_screenshot(frames: list, action: str) -> str:
    """把 4 張影格存成 2x2 拼接圖，回傳相對路徑（供 web 使用）"""
    import cv2
    import numpy as np
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'{ts}_{action}.jpg'
    filepath = os.path.join(SCREENSHOT_DIR, filename)
    while len(frames) < 4:
        frames.append(frames[-1])
    # 縮小至 640x360 再拼接，每張約 150KB
    resized = [cv2.resize(f, (640, 360)) for f in frames[:4]]
    top = np.concatenate([resized[0], resized[1]], axis=1)
    bot = np.concatenate([resized[2], resized[3]], axis=1)
    grid = np.concatenate([top, bot], axis=0)
    cv2.imwrite(filepath, grid, [cv2.IMWRITE_JPEG_QUALITY, 75])
    # 清除 7 天前的截圖
    cutoff = time.time() - 7 * 86400
    for f in os.listdir(SCREENSHOT_DIR):
        fp = os.path.join(SCREENSHOT_DIR, f)
        if os.path.getmtime(fp) < cutoff:
            os.remove(fp)
    return f'action_screenshots/{filename}'

def send_telegram(action: str, confidence: float):
    """推播 Telegram 通知"""
    if not BOT_TOKEN or not CHAT_ID:
        return
    emoji = ACTION_EMOJI.get(action, '🐾')
    label = ACTION_LABEL.get(action, action)
    now = datetime.now().strftime('%H:%M')
    text = f"{emoji} 墨墨正在{label}！（{now}，信心 {confidence:.0%}）"
    try:
        requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            json={'chat_id': CHAT_ID, 'text': text},
            timeout=10
        )
    except Exception as e:
        logger.error(f'Telegram 推播失敗: {e}')


def post_action(action: str, confidence: float, frames: list):
    """存截圖、寫入 rabbit-care API 並推播 Telegram"""
    screenshot = None
    try:
        screenshot = save_screenshot(frames, action)
    except Exception as e:
        logger.error(f'截圖儲存失敗: {e}')
    try:
        resp = requests.post(
            f'{RABBIT_CARE_URL}/api/log-action',
            json={
                'action': action,
                'confidence': confidence,
                'timestamp': datetime.now().isoformat(),
                'screenshot': screenshot
            },
            timeout=10
        )
        resp.raise_for_status()
        logger.info(f'已記錄動作: {action} (信心: {confidence:.2f}) 截圖: {screenshot}')
    except Exception as e:
        logger.error(f'寫入 rabbit-care 失敗: {e}')
    send_telegram(action, confidence)


def detect_motion(prev_frame, curr_frame) -> bool:
    """比較兩影格差異，超過門檻視為移動"""
    import cv2
    diff = cv2.absdiff(prev_frame, curr_frame)
    gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 25, 255, cv2.THRESH_BINARY)
    return thresh.sum() > MOTION_THRESHOLD * 255


def run():
    """主迴圈：監看 RTSP 串流"""
    if not RTSP_URL:
        logger.error('RTSP_URL 未設定，請在 .env 設定攝影機網址')
        return

    logger.info(f'連線 RTSP: {RTSP_URL}')
    last_action_time = 0
    motion_frames = []      # 偵測用（密集收集）
    screenshot_frames = []  # 截圖用（每 2 秒一張）
    in_motion = False
    still_count = 0
    motion_start_time = 0
    last_screenshot_time = 0

    import cv2
    import numpy as np
    while True:
        cap = cv2.VideoCapture(RTSP_URL)
        if not cap.isOpened():
            logger.warning('RTSP 連線失敗，30 秒後重試')
            time.sleep(30)
            continue

        logger.info('RTSP 串流連線成功')
        prev_frame = None

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    logger.warning('讀取影格失敗，重新連線')
                    break

                time.sleep(0.2)  # 每秒約 5 幀，降低 CPU 消耗

                if prev_frame is None:
                    prev_frame = frame
                    continue

                moving = detect_motion(prev_frame, frame)
                prev_frame = frame

                def _try_analyze():
                    nonlocal in_motion, still_count, last_action_time, motion_frames, screenshot_frames, motion_start_time, last_screenshot_time
                    in_motion = False
                    still_count = 0
                    motion_start_time = 0
                    last_screenshot_time = 0
                    now = time.time()
                    logger.info(f'分析條件: motion_frames={len(motion_frames)} cooldown剩餘={max(0, COOLDOWN_SECONDS-(now-last_action_time)):.0f}s')
                    if now - last_action_time >= COOLDOWN_SECONDS and len(motion_frames) >= 10:
                        indices = np.linspace(0, len(motion_frames)-1, 4, dtype=int)
                        selected = [motion_frames[i] for i in indices]
                        result = analyze_frames(selected)
                        if result:
                            action, confidence = result
                            if action != 'resting':
                                post_action(action, confidence, screenshot_frames[:4])
                            else:
                                logger.info(f'識別為休息，略過記錄 (信心: {confidence:.2f})')
                            last_action_time = now
                    motion_frames = []
                    screenshot_frames = []

                if moving:
                    still_count = 0
                    if not in_motion:
                        in_motion = True
                        motion_frames = []
                        screenshot_frames = []
                        motion_start_time = time.time()
                        last_screenshot_time = 0
                        logger.info('偵測到移動，開始收集影格')
                    motion_frames.append(frame.copy())
                    # 截圖每 2 秒抓一張，最多 4 張
                    now_t = time.time()
                    if len(screenshot_frames) < 4 and now_t - last_screenshot_time >= 2.0:
                        screenshot_frames.append(frame.copy())
                        last_screenshot_time = now_t
                    # 移動超過 15 秒仍未靜止，強制觸發分析
                    if time.time() - motion_start_time >= 15 and len(motion_frames) >= 4:
                        logger.info('移動持續 15 秒，強制觸發分析')
                        _try_analyze()
                else:
                    if in_motion:
                        still_count += 1
                        if still_count >= 30:
                            logger.info(f'靜止 {still_count} 幀，觸發分析')
                            _try_analyze()
        finally:
            cap.release()


if __name__ == '__main__':
    run()
