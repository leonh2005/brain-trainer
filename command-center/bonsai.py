"""Bonsai 27B 本地模型控制面板 —— 開/關 Terminal 並載入/卸載模型。

只有本機回環（瀏覽器從本機連到 command-center）能呼叫，避免內網／Tunnel
任意觸發 osascript。指令一律寫死白名單，不接受外部傳入的字串。
"""
import os
import subprocess
import threading
import time

LMS = os.path.expanduser('~/.lmstudio/bin/lms')
MODEL = 'prism-ml/bonsai-27b'
CONTEXT = '16384'
LOAD_CMD = f'{LMS} load {MODEL} -c {CONTEXT} -y'
UNLOAD_CMD = f'{LMS} unload {MODEL}'

_lock = threading.Lock()
_terminal_window_id: str | None = None   # 我們開的那個 Terminal window id


def is_local(client_host: str) -> bool:
    """只允許 127.0.0.1 / ::1。IPv4-mapped IPv6 也算。"""
    return client_host in ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def status() -> dict:
    """以 lms ps 判斷模型是否已載入（不依賴本行程狀態，重啟 command-center 也正確）。"""
    loaded = False
    try:
        out = subprocess.run([LMS, 'ps'], capture_output=True, text=True, timeout=10).stdout
        # ps 表頭也含 model 名稱（IDENTIFIER/MODEL 欄），所以要有資料列才算：
        # 資料列以 prism-ml/ 開頭且包含我們的模型
        loaded = any(line.strip().startswith(MODEL) for line in out.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        pass
    with _lock:
        terminal_open = _terminal_window_id is not None
    return {'loaded': loaded, 'terminal_open': terminal_open}


def start() -> dict:
    """在 Terminal 開一個視窗執行 lms load，並記下 window id 供停止時關閉。"""
    global _terminal_window_id
    with _lock:
        if _terminal_window_id is not None:
            return {'started': False, 'reason': 'Terminal 已開啟'}
        # 新版 macOS 的 Terminal AppleScript 無法對 do script 回傳值取 id/window，
        # 改用「開視窗前後的 id 集合差集」找出新建的那個視窗。
        def window_ids():
            r = subprocess.run(
                ['osascript', '-e', 'tell application "Terminal" to return id of every window'],
                capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                return set()
            return {x.strip() for x in r.stdout.split(',') if x.strip()}
        try:
            before = window_ids()
            r = subprocess.run(
                ['osascript', '-e', f'tell application "Terminal" to do script "{LOAD_CMD}"'],
                capture_output=True, text=True, timeout=20)
            if r.returncode != 0:
                return {'started': False, 'reason': (r.stderr or '').strip()[:200] or '開 Terminal 失敗'}
            time.sleep(1.0)
            after = window_ids()
            new_ids = after - before
            wid = sorted(new_ids)[0] if new_ids else 'unknown'
        except (OSError, subprocess.TimeoutExpired) as e:
            return {'started': False, 'reason': f'osascript 失敗：{type(e).__name__}'}
        _terminal_window_id = wid
    # 模型載入在 Terminal 背景下進行，API 不阻塞；前端等 ~35 秒再查狀態
    return {'started': True, 'window_id': _terminal_window_id, 'cmd': LOAD_CMD}


def stop() -> dict:
    """卸載模型並關閉我們開的 Terminal 視窗。"""
    global _terminal_window_id
    unload_err = ''
    try:
        subprocess.run([LMS, 'unload', MODEL], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        unload_err = f'{type(e).__name__}'

    with _lock:
        wid = _terminal_window_id
        _terminal_window_id = None
    closed = False
    if wid and wid != 'unknown':
        try:
            script = f'tell application "Terminal" to close (window id {wid})'
            r = subprocess.run(['osascript', '-e', script], capture_output=True,
                               text=True, timeout=15)
            closed = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            closed = False
    return {'stopped': True, 'terminal_closed': closed, 'unload_error': unload_err}
