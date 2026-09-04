"""動作按鈕 job registry — 白名單制，只能執行此處列出的任務"""
import os
import socket
import subprocess
import time

CC = '/Users/steven/CCProject'
FINMIND_PY = f'{CC}/finmind/venv/bin/python3'
COMFY = '/Volumes/1TOWC/ComfyUI'


def _port_up(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0

# id → 定義。cmd 類直接 subprocess；label 類走 launchctl kickstart
JOBS = {
    'swing-rerun': {
        'name': '重跑隔日沖選股',
        'cmd': [FINMIND_PY, f'{CC}/telebot/screener.py'],
        'cwd': f'{CC}/telebot',
        'log': f'{CC}/logs/screener.log',
    },
    'daytrade-rerun': {
        'name': '重跑當沖選股',
        'cmd': [FINMIND_PY, f'{CC}/finmind/daytrade_alert.py'],
        'cwd': f'{CC}/finmind',
        'log': f'{CC}/logs/daytrade.log',
    },
    'swing-track-check': {
        'name': '手動比對隔日沖',
        'cmd': [FINMIND_PY, f'{CC}/telebot/check_swing_track.py'],
        'cwd': f'{CC}/telebot',
        'log': f'{CC}/logs/swing_track.log',
    },
    'swing-exit-stats': {
        'name': '隔日沖出場統計',
        'cmd': [FINMIND_PY, f'{CC}/telebot/analyze_swing_exit_time.py'],
        'cwd': f'{CC}/telebot',
        'log': f'{CC}/logs/swing_exit_stats.log',
    },
    'swing-strategy-compare': {
        'name': '嚴格全過 vs 放寬篩選(差一項) 勝率比較',
        'cmd': [FINMIND_PY, f'{CC}/telebot/compare_swing_strategies.py'],
        'cwd': f'{CC}/telebot',
        'log': f'{CC}/logs/swing_strategy_compare.log',
    },
    'daytrade-track-check': {
        'name': '手動比對當沖',
        'cmd': [FINMIND_PY, f'{CC}/telebot/check_daytrade_track.py'],
        'cwd': f'{CC}/telebot',
        'log': f'{CC}/logs/daytrade_track.log',
    },
    'chip-update': {
        'name': '更新籌碼資料',
        'label': 'com.steven.chip-tracker-updater',
        'log': f'{CC}/logs/chip-tracker-updater.log',
    },
    'market-rebuild': {
        'name': '重建市場恐慌儀表板',
        'label': 'com.steven.market-dashboard',
        'log': f'{CC}/logs/market-dashboard.log',
    },
    'comfyui': {
        'name': '啟動 ComfyUI（AI 影片）',
        'server': [f'{COMFY}/venv/bin/python', 'main.py'],
        'cwd': COMFY,
        'port': 8188,
        'requires': COMFY,          # 外接硬碟目錄，未掛載則不啟動
        'log': f'{CC}/logs/comfyui.log',
    },
}

_running: dict = {}  # id → {proc?, started}


def list_jobs():
    return [{'id': jid, 'name': j['name'], 'running': is_running(jid)} for jid, j in JOBS.items()]


def is_running(jid: str) -> bool:
    job = JOBS.get(jid, {})
    # server 類：以 port 是否在聽判斷（跨進程可靠）
    if 'server' in job:
        return _port_up(job['port'])
    r = _running.get(jid)
    if not r:
        return False
    proc = r.get('proc')
    if proc is not None and proc.poll() is None:
        return True
    # launchctl 類無法追蹤 proc，以 60 秒內視為執行中
    if proc is None and time.time() - r['started'] < 60:
        return True
    return False


def run(jid: str):
    job = JOBS.get(jid)
    if job is None:
        raise KeyError(jid)
    if is_running(jid):
        return {'started': False, 'reason': 'already running'}
    if 'server' in job:
        # 外接硬碟服務：先確認掛載
        if not os.path.isdir(job['requires']):
            return {'started': False, 'reason': '外接硬碟未掛載，請插上 1TOWC'}
        logf = open(job['log'], 'a')
        # start_new_session：脫離 command-center，服務可獨立常駐
        subprocess.Popen(job['server'], cwd=job['cwd'], stdout=logf, stderr=logf,
                         start_new_session=True)
        _running[jid] = {'proc': None, 'started': time.time()}
    elif 'cmd' in job:
        logf = open(job['log'], 'a')
        proc = subprocess.Popen(job['cmd'], cwd=job.get('cwd'), stdout=logf, stderr=logf)
        _running[jid] = {'proc': proc, 'started': time.time()}
    else:
        # 不加 -k：避免 kill 掉正在執行的更新作業（如籌碼更新需數分鐘）
        subprocess.run(['launchctl', 'kickstart', f'gui/501/{job["label"]}'],
                       capture_output=True, timeout=15)
        _running[jid] = {'proc': None, 'started': time.time()}
    return {'started': True}


def status(jid: str):
    job = JOBS.get(jid)
    if job is None:
        raise KeyError(jid)
    tail = ''
    try:
        with open(job['log'], errors='replace') as f:
            tail = ''.join(f.readlines()[-60:])
    except OSError:
        pass
    return {'id': jid, 'name': job['name'], 'running': is_running(jid), 'log_tail': tail}
