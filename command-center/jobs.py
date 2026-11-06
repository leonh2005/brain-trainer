"""動作按鈕 job registry — 白名單制，只能執行此處列出的任務"""
import os
import socket
import subprocess
import time

CC = '/Users/steven/CCProject'
FINMIND_PY = f'{CC}/finmind/venv/bin/python3'
COMFY = '/Volumes/1TOWC/ComfyUI'
LA_DIR = os.path.expanduser('~/Library/LaunchAgents')


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
    'foreign-return-update': {
        'name': '更新外資回頭三訊號',
        'cmd': ['/opt/homebrew/bin/python3', f'{CC}/scripts/foreign_return_monitor.py'],
        'cwd': CC,
        'log': f'{CC}/logs/foreign_return.log',
    },
    'comfyui': {
        'name': '啟動 ComfyUI（AI 影片）',
        'server': [f'{COMFY}/venv/bin/python', 'main.py'],
        'cwd': COMFY,
        'port': 8188,
        'requires': COMFY,          # 外接硬碟目錄，未掛載則不啟動
        'log': f'{CC}/logs/comfyui.log',
    },
    # 盤中主力監控：LaunchAgent 每天 08:55 起跑，stoppable 讓卡片能 bootout 停掉
    # （單靠 kill 會被 KeepAlive 在 5 分鐘後拉回來）。停止後隔天要按「啟用」重新 bootstrap。
    'intraday-monitor': {
        'name': '盤中主力監控',
        'label': 'com.steven.intraday-monitor',
        'plist': f'{LA_DIR}/com.steven.intraday-monitor.plist',
        'stoppable': True,
        'log': f'{CC}/logs/intraday_monitor.log',
    },
}

_running: dict = {}  # id → {proc?, started}


def list_jobs():
    return [{'id': jid, 'name': j['name'], 'running': is_running(jid)} for jid, j in JOBS.items()]


def _label_pid(label: str) -> int | None:
    """回傳 launchctl 中該 label 的 PID；未載入或已結束回 None。"""
    try:
        out = subprocess.run(['launchctl', 'list'], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 3 and parts[2] == label:
            pid = parts[0].strip()
            return int(pid) if pid.isdigit() else None
    return None


def is_running(jid: str) -> bool:
    job = JOBS.get(jid, {})
    # server 類：以 port 是否在聽判斷（跨進程可靠）
    if 'server' in job:
        return _port_up(job['port'])
    # stoppable 的 label 類（如盤中監控）：以 launchctl 的 PID 判斷，重啟後仍正確
    if job.get('stoppable') and 'label' in job:
        return _label_pid(job['label']) is not None
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
        target = f'gui/{os.getuid()}/{job["label"]}'
        try:
            r = subprocess.run(['launchctl', 'kickstart', target], capture_output=True, timeout=15)
            # bootout 過的 job 不在 launchctl 裡，kickstart 會失敗 → 先 bootstrap 再踢
            if r.returncode != 0 and 'plist' in job:
                subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', job['plist']],
                               capture_output=True, timeout=15)
                r = subprocess.run(['launchctl', 'kickstart', target],
                                   capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {'started': False, 'reason': f'launchctl 執行失敗：{type(e).__name__}'}
        if r.returncode != 0:
            err = (r.stderr or b'').decode(errors='replace').strip()
            return {'started': False, 'reason': err[:200] or f'kickstart rc={r.returncode}'}
        _running[jid] = {'proc': None, 'started': time.time()}
    return {'started': True}


def stop(jid: str):
    job = JOBS.get(jid)
    if job is None:
        raise KeyError(jid)
    # stoppable 的 label 類：bootout 會把 job 整個下架，KeepAlive 才不會在 5 分鐘後拉回來
    if job.get('stoppable') and 'label' in job:
        try:
            r = subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{job["label"]}'],
                               capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as e:
            return {'stopped': False, 'reason': f'launchctl 執行失敗：{type(e).__name__}'}
        _running.pop(jid, None)
        if r.returncode == 0:
            return {'stopped': True}
        # 已經不在 launchctl 裡（例如程序剛自行結束）→ 視為已達成停止，不是失敗
        if _label_pid(job['label']) is None:
            return {'stopped': True}
        err = (r.stderr or b'').decode(errors='replace').strip()
        return {'stopped': False, 'reason': err[:200] or 'bootout 失敗'}
    if 'server' not in job:
        return {'stopped': False, 'reason': '此任務不支援停止'}
    # 以 port 找監聽行程：手動啟動的服務也殺得到，不依賴 _running 記錄
    try:
        # 絕對路徑：LaunchAgent 的 PATH 不一定含 /usr/sbin
        pids = subprocess.run(['/usr/sbin/lsof', '-ti', f'tcp:{job["port"]}', '-sTCP:LISTEN'],
                              capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.TimeoutExpired):
        pids = []
    if not pids:
        return {'stopped': False, 'reason': '服務未在執行'}
    for pid in pids:
        subprocess.run(['kill', '-TERM', pid], capture_output=True)
    deadline = time.time() + 10
    while time.time() < deadline:
        if not _port_up(job['port']):
            _running.pop(jid, None)
            return {'stopped': True}
        time.sleep(0.5)
    for pid in pids:  # 10 秒內沒收乾淨就強制結束
        subprocess.run(['kill', '-KILL', pid], capture_output=True)
    time.sleep(1)
    _running.pop(jid, None)
    stopped = not _port_up(job['port'])
    return {'stopped': stopped, 'reason': '' if stopped else '結束失敗（port 仍在使用）'}


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
