#!/usr/bin/env python3
"""
墨墨動作辨識監看服務
連接 IP 攝影機 RTSP 串流，偵測移動後送 Gemini Vision 分析，結果寫入 rabbit-care。
"""

import os
import cv2
import json
import time
import logging
import requests
import numpy as np
from datetime import datetime
from PIL import Image
import google.genai as genai
from dotenv import load_dotenv

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
MIN_CONFIDENCE   = float(os.getenv('GEMINI_MIN_CONFIDENCE', '0.7'))

PROMPT = """這是寵物兔子的監控截圖（依時間順序）。
請判斷牠正在做什麼，從以下選項中選一個：
- eating（在吃飯）
- drinking（在喝水）
- stretching（在伸懶腰）
- other（其他）

只回傳 JSON，格式：{"action": "eating", "confidence": 0.9}"""


# ── Gemini client（singleton）───────────────────
_gemini_client = None

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    return _gemini_client


def frames_to_pil(frames: list) -> list:
    """numpy BGR frames → PIL RGB Images"""
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
    try:
        client = _get_gemini_client()
        pil_images = frames_to_pil(frames)
        contents = pil_images + [PROMPT]
        response = client.models.generate_content(
            model='gemini-2.5-flash',
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
        logger.error(f'Gemini 分析失敗: {e}')
        return None


def post_action(action: str, confidence: float):
    """寫入 rabbit-care API"""
    try:
        resp = requests.post(
            f'{RABBIT_CARE_URL}/api/log-action',
            json={
                'action': action,
                'confidence': confidence,
                'timestamp': datetime.now().isoformat()
            },
            timeout=10
        )
        resp.raise_for_status()
        logger.info(f'已記錄動作: {action} (信心: {confidence:.2f})')
    except Exception as e:
        logger.error(f'寫入 rabbit-care 失敗: {e}')


def detect_motion(prev_frame, curr_frame) -> bool:
    """比較兩影格差異，超過門檻視為移動"""
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
    motion_frames = []
    in_motion = False
    still_count = 0

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

                if prev_frame is None:
                    prev_frame = frame
                    continue

                moving = detect_motion(prev_frame, frame)
                prev_frame = frame

                if moving:
                    still_count = 0
                    if not in_motion:
                        in_motion = True
                        motion_frames = []
                        logger.info('偵測到移動，開始收集影格')
                    if len(motion_frames) < 8:
                        motion_frames.append(frame.copy())
                else:
                    if in_motion:
                        still_count += 1
                        if still_count >= 90:
                            in_motion = False
                            still_count = 0
                            now = time.time()
                            if now - last_action_time >= COOLDOWN_SECONDS and len(motion_frames) >= 4:
                                indices = np.linspace(0, len(motion_frames)-1, 4, dtype=int)
                                selected = [motion_frames[i] for i in indices]
                                result = analyze_frames(selected)
                                if result:
                                    action, confidence = result
                                    post_action(action, confidence)
                                    last_action_time = now
                            motion_frames = []
        finally:
            cap.release()


if __name__ == '__main__':
    run()
