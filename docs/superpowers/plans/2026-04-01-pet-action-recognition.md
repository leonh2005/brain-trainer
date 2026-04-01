# 墨墨動作辨識系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 整合 IP 攝影機 RTSP 串流到 rabbit-care，自動辨識墨墨的吃飯、喝水、伸懶腰行為並寫入每日日誌。

**Architecture:** motion_watcher.py 作為獨立背景服務，監看 RTSP 串流偵測移動，擷取影格送 Gemini 2.5 Flash Vision 辨識，透過 HTTP 呼叫 rabbit-care 新增的 `/api/log-action` endpoint 寫入 daily_log。

**Tech Stack:** Python 3、OpenCV (`opencv-python`)、`google-genai`、Flask（現有）、SQLite（現有）、launchd

---

## 檔案結構

| 動作 | 路徑 | 說明 |
|------|------|------|
| Modify | `rabbit-care/app.py` | 新增 DB migration、`/api/log-action` endpoint、index 傳入今日行為 |
| Modify | `rabbit-care/templates/index.html` | 新增今日行為區塊 |
| Modify | `rabbit-care/.env` | 新增 RTSP_URL、GEMINI_API_KEY |
| Create | `rabbit-care/motion_watcher.py` | RTSP 監看 + 動作分析主程式 |
| Create | `rabbit-care/tests/test_action_api.py` | `/api/log-action` 單元測試 |
| Create | `rabbit-care/tests/test_analyzer.py` | Gemini 分析邏輯單元測試 |
| Create | `~/Library/LaunchAgents/com.steven.motion-watcher.plist` | launchd 服務設定 |

---

## Task 1：安裝相依套件

**Files:**
- Modify: `rabbit-care/.env`

- [ ] **Step 1：安裝套件**

```bash
cd ~/CCProject/rabbit-care
pip3 install opencv-python google-genai pillow
```

Expected: Successfully installed opencv-python google-genai pillow

- [ ] **Step 2：驗證安裝**

```bash
python3 -c "import cv2; import google.genai; import PIL; print('OK')"
```

Expected: `OK`

- [ ] **Step 3：新增環境變數到 .env**

在 `rabbit-care/.env` 末尾加入：

```
RTSP_URL=rtsp://admin:password@192.168.1.xxx:554/stream
GEMINI_API_KEY=AIzaSyDMA2glTAU92jNxhORfMlzRDQ0S4FCzWkQ
MOTION_THRESHOLD=500
COOLDOWN_SECONDS=60
GEMINI_MIN_CONFIDENCE=0.7
```

> **注意**：`RTSP_URL` 需等攝影機設定後填入實際 IP 和帳密。暫時留著，Task 4 之前不會實際連線。

- [ ] **Step 4：Commit**

```bash
cd ~/CCProject/rabbit-care
git add .env
git commit -m "chore: add motion watcher env vars"
```

---

## Task 2：DB Migration + API Endpoint

**Files:**
- Modify: `rabbit-care/app.py:64-87`（init_db）
- Modify: `rabbit-care/app.py`（新增 `/api/log-action`）
- Create: `rabbit-care/tests/test_action_api.py`

- [ ] **Step 1：寫失敗測試**

建立 `rabbit-care/tests/test_action_api.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import json
from app import app, init_db, DB_PATH
import tempfile

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = str(tmp_path / 'test.db')
    monkeypatch.setattr('app.DB_PATH', db_file)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    init_db()
    with app.test_client() as c:
        yield c

def test_log_action_creates_record(client):
    payload = {
        'action': 'eating',
        'confidence': 0.92,
        'timestamp': '2026-04-01T09:23:00'
    }
    resp = client.post('/api/log-action',
                       data=json.dumps(payload),
                       content_type='application/json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'ok'

def test_log_action_appends_multiple(client):
    for action, ts in [('eating', '2026-04-01T09:00:00'), ('drinking', '2026-04-01T10:00:00')]:
        client.post('/api/log-action',
                    data=json.dumps({'action': action, 'confidence': 0.9, 'timestamp': ts}),
                    content_type='application/json')
    resp = client.get('/api/today-actions?date=2026-04-01')
    assert resp.status_code == 200
    actions = resp.get_json()['actions']
    assert len(actions) == 2
    assert actions[0]['action'] == 'eating'
    assert actions[1]['action'] == 'drinking'

def test_log_action_ignores_other(client):
    payload = {'action': 'other', 'confidence': 0.9, 'timestamp': '2026-04-01T09:00:00'}
    resp = client.post('/api/log-action',
                       data=json.dumps(payload),
                       content_type='application/json')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ignored'
```

- [ ] **Step 2：執行測試確認失敗**

```bash
cd ~/CCProject/rabbit-care
python3 -m pytest tests/test_action_api.py -v
```

Expected: FAILED（`/api/log-action` 不存在）

- [ ] **Step 3：在 `app.py` 的 `init_db()` 加入 migration**

在 `init_db()` 的 `executescript` 結尾（`'''` 之前）加入：

```python
            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
```

> 使用獨立的 `action_log` 表（而非修改 daily_log），更易查詢和擴充。

- [ ] **Step 4：在 `app.py` 新增兩個 API endpoint**

在 `app.py` 最後一個 route 之後（`export_med` 之後）加入：

```python
# ── 動作辨識 API ──────────────────────────────────────────────

@app.route('/api/log-action', methods=['POST'])
def api_log_action():
    data = request.get_json()
    action = data.get('action', '')
    confidence = float(data.get('confidence', 0))
    timestamp = data.get('timestamp', '')

    if action == 'other' or not action:
        return jsonify({'status': 'ignored'})

    log_date = timestamp[:10] if timestamp else date.today().isoformat()

    with get_db() as conn:
        conn.execute(
            'INSERT INTO action_log (log_date, action, confidence, timestamp) VALUES (?,?,?,?)',
            (log_date, action, confidence, timestamp)
        )
    return jsonify({'status': 'ok'})


@app.route('/api/today-actions')
def api_today_actions():
    target_date = request.args.get('date', date.today().isoformat())
    with get_db() as conn:
        rows = conn.execute(
            'SELECT action, confidence, timestamp FROM action_log WHERE log_date=? ORDER BY timestamp',
            (target_date,)
        ).fetchall()
    return jsonify({'actions': [dict(r) for r in rows]})
```

- [ ] **Step 5：執行測試確認通過**

```bash
cd ~/CCProject/rabbit-care
python3 -m pytest tests/test_action_api.py -v
```

Expected: 3 passed

- [ ] **Step 6：Commit**

```bash
git add app.py tests/test_action_api.py
git commit -m "feat: add action_log table and /api/log-action endpoint"
```

---

## Task 3：首頁顯示今日行為

**Files:**
- Modify: `rabbit-care/app.py`（index route）
- Modify: `rabbit-care/templates/index.html`

- [ ] **Step 1：更新 index route，查詢今日行為**

在 `index()` 函式的 `with get_db() as conn:` 區塊內，在 `active_meds` 查詢之後加入：

```python
        today_actions_raw = conn.execute(
            'SELECT action, timestamp FROM action_log WHERE log_date=? ORDER BY timestamp',
            (date.today().isoformat(),)
        ).fetchall()
```

在 `return render_template(...)` 加入參數 `today_actions=today_actions_raw`。

- [ ] **Step 2：整理行為統計（在 index route 裡）**

在 `return render_template(...)` 之前加入：

```python
    action_labels = {'eating': '🍽 吃飯', 'drinking': '💧 喝水', 'stretching': '🐇 伸懶腰'}
    today_action_summary = {}
    for row in today_actions_raw:
        a = row['action']
        if a not in today_action_summary:
            today_action_summary[a] = {'label': action_labels.get(a, a), 'count': 0, 'last_time': ''}
        today_action_summary[a]['count'] += 1
        today_action_summary[a]['last_time'] = row['timestamp'][11:16]  # HH:MM
```

並把 `render_template` 的 `today_actions=today_actions_raw` 改為 `today_action_summary=today_action_summary`。

- [ ] **Step 3：在 `index.html` 新增今日行為區塊**

開啟 `rabbit-care/templates/index.html`，在提醒事項區塊（`{% for r in reminders %}`）之後加入：

```html
<!-- 今日行為 -->
{% if today_action_summary %}
<div class="card mb-3">
  <div class="card-header"><strong>🎯 今日行為</strong></div>
  <ul class="list-group list-group-flush">
    {% for key, info in today_action_summary.items() %}
    <li class="list-group-item d-flex justify-content-between align-items-center">
      {{ info.label }}
      <span>
        <span class="badge bg-primary rounded-pill">{{ info.count }} 次</span>
        <small class="text-muted ms-2">最後 {{ info.last_time }}</small>
      </span>
    </li>
    {% endfor %}
  </ul>
</div>
{% endif %}
```

- [ ] **Step 4：手動驗證**

```bash
cd ~/CCProject/rabbit-care
python3 app.py &
# 插入測試資料
python3 -c "
import sqlite3
conn = sqlite3.connect('rabbit.db')
conn.execute(\"INSERT INTO action_log (log_date, action, confidence, timestamp) VALUES ('$(date +%Y-%m-%d)', 'eating', 0.92, '$(date +%Y-%m-%d)T09:23:00')\")
conn.commit()
"
open http://localhost:5200
```

確認首頁出現「🍽 吃飯 1 次」。

```bash
kill %1  # 停止測試 server
```

- [ ] **Step 5：Commit**

```bash
git add app.py templates/index.html
git commit -m "feat: show today's action summary on index page"
```

---

## Task 4：motion_watcher.py — 動作分析核心

**Files:**
- Create: `rabbit-care/motion_watcher.py`
- Create: `rabbit-care/tests/test_analyzer.py`

- [ ] **Step 1：寫 Gemini 分析函式的失敗測試**

建立 `rabbit-care/tests/test_analyzer.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from unittest.mock import patch, MagicMock
import numpy as np

def test_analyze_frames_returns_eating():
    """Gemini 回傳 eating 時，函式應回傳 ('eating', 0.92)"""
    from motion_watcher import analyze_frames

    mock_response = MagicMock()
    mock_response.text = '{"action": "eating", "confidence": 0.92}'

    fake_frames = [np.zeros((100, 100, 3), dtype=np.uint8)] * 4

    with patch('motion_watcher._get_gemini_client') as mock_client:
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_client.return_value.models = MagicMock()
        mock_client.return_value.models.generate_content = mock_model.generate_content
        result = analyze_frames(fake_frames)

    assert result == ('eating', 0.92)

def test_analyze_frames_filters_low_confidence():
    """信心值低於 0.7 應回傳 None"""
    from motion_watcher import analyze_frames

    mock_response = MagicMock()
    mock_response.text = '{"action": "eating", "confidence": 0.5}'

    fake_frames = [np.zeros((100, 100, 3), dtype=np.uint8)] * 4

    with patch('motion_watcher._get_gemini_client') as mock_client:
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_client.return_value.models.generate_content = mock_model.generate_content
        result = analyze_frames(fake_frames)

    assert result is None

def test_analyze_frames_filters_other():
    """action == 'other' 應回傳 None"""
    from motion_watcher import analyze_frames

    mock_response = MagicMock()
    mock_response.text = '{"action": "other", "confidence": 0.95}'

    fake_frames = [np.zeros((100, 100, 3), dtype=np.uint8)] * 4

    with patch('motion_watcher._get_gemini_client') as mock_client:
        mock_model = MagicMock()
        mock_model.generate_content.return_value = mock_response
        mock_client.return_value.models.generate_content = mock_model.generate_content
        result = analyze_frames(fake_frames)

    assert result is None
```

- [ ] **Step 2：執行測試確認失敗**

```bash
cd ~/CCProject/rabbit-care
python3 -m pytest tests/test_analyzer.py -v
```

Expected: ERROR（`motion_watcher` 不存在）

- [ ] **Step 3：建立 `motion_watcher.py`**

建立 `rabbit-care/motion_watcher.py`：

```python
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
from io import BytesIO
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
RTSP_URL            = os.getenv('RTSP_URL', '')
RABBIT_CARE_URL     = os.getenv('RABBIT_CARE_URL', 'http://localhost:5200')
MOTION_THRESHOLD    = int(os.getenv('MOTION_THRESHOLD', '500'))
COOLDOWN_SECONDS    = int(os.getenv('COOLDOWN_SECONDS', '60'))
MIN_CONFIDENCE      = float(os.getenv('GEMINI_MIN_CONFIDENCE', '0.7'))

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
        # 去掉可能的 markdown code block
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
                    # 每秒抽樣（約 30fps → 每 30 影格取一張）
                    if len(motion_frames) < 8:
                        motion_frames.append(frame.copy())
                else:
                    if in_motion:
                        still_count += 1
                        if still_count >= 90:  # 約 3 秒靜止
                            in_motion = False
                            still_count = 0
                            now = time.time()
                            if now - last_action_time >= COOLDOWN_SECONDS and len(motion_frames) >= 4:
                                # 等距取 4 影格
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
```

- [ ] **Step 4：執行測試確認通過**

```bash
cd ~/CCProject/rabbit-care
python3 -m pytest tests/test_analyzer.py -v
```

Expected: 3 passed

- [ ] **Step 5：Commit**

```bash
git add motion_watcher.py tests/test_analyzer.py
git commit -m "feat: add motion_watcher with Gemini Vision analysis"
```

---

## Task 5：launchd 服務

**Files:**
- Create: `~/Library/LaunchAgents/com.steven.motion-watcher.plist`

- [ ] **Step 1：建立 plist 檔**

建立 `~/Library/LaunchAgents/com.steven.motion-watcher.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.steven.motion-watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/steven/CCProject/rabbit-care/motion_watcher.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/steven/CCProject/rabbit-care/motion-watcher.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/steven/CCProject/rabbit-care/motion-watcher.log</string>
    <key>WorkingDirectory</key>
    <string>/Users/steven/CCProject/rabbit-care</string>
</dict>
</plist>
```

- [ ] **Step 2：載入服務（等 RTSP_URL 設定好再做）**

> ⚠️ **注意**：先在 `.env` 填入真實的 `RTSP_URL` 再執行以下指令。

```bash
launchctl load ~/Library/LaunchAgents/com.steven.motion-watcher.plist
```

- [ ] **Step 3：確認服務狀態**

```bash
launchctl list | grep motion-watcher
tail -f ~/CCProject/rabbit-care/motion-watcher.log
```

Expected: log 出現 `連線 RTSP: rtsp://...` 或 `RTSP_URL 未設定`（若尚未填入）

- [ ] **Step 4：Commit**

```bash
git add ../Library/LaunchAgents/com.steven.motion-watcher.plist 2>/dev/null || true
cd ~/CCProject/rabbit-care
git add .
git commit -m "feat: add launchd service for motion watcher"
```

---

## Task 6：整合測試

- [ ] **Step 1：跑全部測試**

```bash
cd ~/CCProject/rabbit-care
python3 -m pytest tests/ -v
```

Expected: 全部 pass

- [ ] **Step 2：手動端對端測試（RTSP 設定好後）**

```bash
# 終端機 1：確認 rabbit-care 在跑
lsof -i :5200 | grep LISTEN

# 終端機 2：手動呼叫 API 模擬一筆動作
curl -X POST http://localhost:5200/api/log-action \
  -H "Content-Type: application/json" \
  -d '{"action":"eating","confidence":0.92,"timestamp":"'$(date -u +%Y-%m-%dT%H:%M:%S)'"}'
```

Expected: `{"status":"ok"}`

- [ ] **Step 3：確認首頁顯示**

```bash
open http://localhost:5200
```

Expected: 首頁出現「🍽 吃飯 1 次」區塊

- [ ] **Step 4：最終 Commit**

```bash
cd ~/CCProject/rabbit-care
git add -A
git commit -m "feat: complete pet action recognition system integration"
```

---

## 完成後設定清單

攝影機到位後需完成：

1. 取得攝影機的 RTSP 網址（通常格式：`rtsp://admin:密碼@IP:554/stream`）
2. 填入 `rabbit-care/.env` 的 `RTSP_URL`
3. 確認攝影機與 Mac 在同一個區域網路
4. `launchctl load ~/Library/LaunchAgents/com.steven.motion-watcher.plist`
5. 觀察 `motion-watcher.log` 確認串流連線成功
