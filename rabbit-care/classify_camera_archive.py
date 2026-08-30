#!/usr/bin/env python3
"""
掃描攝影機錄影存檔資料夾，用 Gemini Vision 判斷影片是否有拍到墨墨，
分類移動到「有墨墨」/「沒有墨墨」子資料夾。
可重複執行：已分類過的檔案（記錄在 state json）會跳過。
"""

import os
import json
import time
import base64
import subprocess
import tempfile
import logging
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

SRC_DIR = "/Volumes/1TOWC/墨墨/攝影機錄影存檔"
HAS_DIR = os.path.join(SRC_DIR, "有墨墨")
NONE_DIR = os.path.join(SRC_DIR, "沒有墨墨")
STATE_FILE = os.path.join(os.path.dirname(__file__), "camera_archive_state.json")
LOG_FILE = os.path.join(os.path.dirname(__file__), "camera_archive_classify.log")

MIN_CONFIDENCE = 0.6

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

PROMPT = """這幾張圖是家用監視器影片中依時間順序擷取的畫面。
籠子環境：地板是白色地墊，畫面中可能會出現一隻寵物兔（白色或淺色毛、長耳朵）。
請判斷這幾張畫面中，是否有看到兔子本體（不只是籠子或環境，需要看到兔子的身體/耳朵/毛）。
只回傳 JSON，格式：{"has_rabbit": true, "confidence": 0.9}"""


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_duration(path: str) -> float:
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', path],
        capture_output=True, text=True, timeout=30
    ).stdout.strip()
    try:
        return float(out) if out else 0.0
    except ValueError:
        return 0.0


def extract_frames(path: str, tmpdir: str) -> list[str]:
    duration = get_duration(path)
    if duration <= 0:
        duration = 60.0
    fractions = [0.25, 0.5, 0.75]
    frame_paths = []
    for i, frac in enumerate(fractions):
        ts = duration * frac
        out_path = os.path.join(tmpdir, f"frame_{i}.jpg")
        subprocess.run(
            ['ffmpeg', '-y', '-ss', str(ts), '-i', path,
             '-vframes', '1', '-q:v', '3', out_path],
            capture_output=True, timeout=30
        )
        if os.path.exists(out_path):
            frame_paths.append(out_path)
    return frame_paths


def analyze_frames(frame_paths: list[str]):
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    content = [{"type": "text", "text": PROMPT}]
    for p in frame_paths:
        with open(p, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}", "detail": "low"}
        })

    for attempt in range(3):
        try:
            response = client.chat.completions.create(
                model='gpt-4o-mini',
                messages=[{"role": "user", "content": content}],
                max_tokens=100,
            )
            raw = response.choices[0].message.content.strip()
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            data = json.loads(raw)
            return bool(data.get('has_rabbit')), float(data.get('confidence', 0))
        except Exception as e:
            logger.error(f'OpenAI 分析失敗 (第 {attempt+1} 次): {e}')
            if attempt < 2:
                time.sleep(5)
    return None


def is_still_copying(path: str) -> bool:
    """檔案大小在 5 秒內有變化，代表還在被寫入/複製中"""
    try:
        size1 = os.path.getsize(path)
        time.sleep(5)
        size2 = os.path.getsize(path)
        return size1 != size2
    except OSError:
        return True


def classify_one(filename: str) -> str:
    """回傳 'has' / 'none' / 'error' / 'copying'（仍在寫入，跳過待下次執行）"""
    path = os.path.join(SRC_DIR, filename)
    if is_still_copying(path):
        logger.info(f'{filename}: 偵測到仍在寫入中，本次跳過')
        return 'copying'
    with tempfile.TemporaryDirectory() as tmpdir:
        frames = extract_frames(path, tmpdir)
        if not frames:
            logger.error(f'{filename}: 無法擷取影格')
            return 'error'
        result = analyze_frames(frames)
        if result is None:
            return 'error'
        has_rabbit, confidence = result
        if has_rabbit and confidence >= MIN_CONFIDENCE:
            dest = HAS_DIR
            label = 'has'
        else:
            dest = NONE_DIR
            label = 'none'
        os.rename(path, os.path.join(dest, filename))
        logger.info(f'{filename}: {label} (confidence={confidence:.2f})')
        return label


def run():
    os.makedirs(HAS_DIR, exist_ok=True)
    os.makedirs(NONE_DIR, exist_ok=True)
    state = load_state()

    files = sorted(
        f for f in os.listdir(SRC_DIR)
        if f.lower().endswith('.mp4') and os.path.isfile(os.path.join(SRC_DIR, f))
    )
    pending = [f for f in files if f not in state]
    logger.info(f'開始分類，共 {len(pending)} 個待處理檔案')
    print(f'共 {len(pending)} 個待處理檔案')

    counts = {'has': 0, 'none': 0, 'error': 0, 'copying': 0}
    for i, filename in enumerate(pending):
        try:
            result = classify_one(filename)
        except Exception as e:
            logger.error(f'{filename}: 未預期例外 {e}')
            result = 'error'
        if result != 'copying':
            state[filename] = result
            save_state(state)
        counts[result] += 1
        if (i + 1) % 20 == 0:
            print(f'進度 {i+1}/{len(pending)}  有墨墨={counts["has"]} 沒有={counts["none"]} 錯誤={counts["error"]} 寫入中跳過={counts["copying"]}')

    print(f'完成。有墨墨={counts["has"]} 沒有={counts["none"]} 錯誤={counts["error"]} 寫入中跳過={counts["copying"]}')
    logger.info(f'分類完成: {counts}')


if __name__ == '__main__':
    run()
