"""各服務資料源讀取器 — 全部唯讀，絕不寫入既有服務的任何資料"""
import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, date

CC = '/Users/steven/CCProject'

# ── 共用工具 ─────────────────────────────────────

_cache: dict = {}

def _proxy(url: str, timeout: int = 10, ttl: int = 30):
    """GET 既有服務 API，帶記憶體快取（TTL 秒）"""
    now = time.time()
    hit = _cache.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read())
    _cache[url] = (now, data)
    return data


def _read_json(path: str):
    with open(path) as f:
        return json.load(f)


def _ro_db(path: str):
    return sqlite3.connect(f'file:{path}?mode=ro', uri=True)


def _mtime(path: str) -> str:
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')
    except OSError:
        return ''


def _wrap(fn):
    """統一回傳 {ok, data|error, updated}"""
    def inner(*a, **kw):
        try:
            data, updated = fn(*a, **kw)
            return {'ok': True, 'data': data, 'updated': updated}
        except Exception as e:
            return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'updated': ''}
    return inner


# ── 訊號牆 ───────────────────────────────────────

@_wrap
def daytrade():
    p = '/tmp/daytrade_candidates.json'
    return _read_json(p), _mtime(p)


@_wrap
def swing():
    p = '/tmp/swing_candidates.json'
    d = _read_json(p)
    return d, d.get('date', _mtime(p))


@_wrap
def intraday():
    """解析 intraday_monitor.log 今日訊號（格式：檢查 CODE NAME ... → 多方/空方推播已送出（信心 N%））"""
    p = f'{CC}/logs/intraday_monitor.log'
    signals = []
    last_check = ''
    with open(p, errors='replace') as f:
        lines = f.readlines()[-3000:]
    pending = None
    for line in lines:
        m = re.match(r'\[(\d{2}:\d{2}:\d{2})\] intraday_monitor 開始執行', line)
        if m:
            last_check = m.group(1)
            continue
        m = re.match(r'\s*檢查 (\w+) (\S+)', line)
        if m:
            pending = {'code': m.group(1), 'name': m.group(2)}
            continue
        m = re.search(r'→ (多方|空方)推播已送出（信心 (\d+)%）', line)
        if m and pending:
            signals.append({**pending, 'side': m.group(1), 'confidence': int(m.group(2)),
                            'at': last_check})
            pending = None
    # 只留今日最後 20 筆（log 無日期戳，以檔案 mtime 是否今日判斷）
    is_today = _mtime(p).startswith(str(date.today()))
    return {'signals': signals[-20:] if is_today else [], 'last_check': last_check}, _mtime(p)


_ma_names_cache = None

def _ma_names():
    """從 ma_monitor.py 的 STOCKS 定義解析 代碼→中文名（不 import，避免 shioaji 依賴）"""
    global _ma_names_cache
    if _ma_names_cache is None:
        _ma_names_cache = {}
        try:
            txt = open(f'{CC}/scripts/ma_monitor.py').read()
            for code, name in re.findall(r'"(\d{4})":\s*\("[^"]*",\s*"([^"]+)"\)', txt):
                _ma_names_cache[code] = name
        except OSError:
            pass
    return _ma_names_cache


@_wrap
def ma():
    p = f'{CC}/scripts/ma_monitor_state.json'
    state = _read_json(p)
    names = _ma_names()
    items = [{'key': k, 'name': names.get(k.split('_')[0], ''), **v} for k, v in state.items()]
    return items, _mtime(p)


@_wrap
def chips():
    d = _proxy('http://localhost:5850/api/data', ttl=300)
    return d, datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def news():
    stats = _proxy('http://localhost:5300/api/stats', ttl=300)
    return stats, stats.get('last_updated', '')


@_wrap
def market_fear():
    md = f'{CC}/market-dashboard'
    out = {}
    try:
        fg = _read_json(f'{md}/fg_history.json')
        if fg:
            last_day = sorted(fg)[-1]
            out['fear_greed'] = {'date': last_day, **fg[last_day]}
    except Exception:
        out['fear_greed'] = None
    try:
        out['sp_vs_ma'] = _read_json(f'{md}/sp_state.json')
    except Exception:
        out['sp_vs_ma'] = None
    for key, path, kind in [
        ('bofa_bb', f'{md}/bb_cache.json', 'history'),
        ('buffett_cash', f'{md}/buffett_cache.json', 'history'),
        ('hindenburg', f'{md}/hindenburg_cache.json', 'history'),
        ('cape', f'{md}/cape_cache.json', 'xy'),
        ('m1b', f'{md}/m1b_cache.json', 'xy'),
        ('margin_ratio', f'{md}/margin_cache.json', 'xy'),
    ]:
        try:
            d = _read_json(path)
            hist = d['history'] if kind == 'history' else d
            out[key] = hist[-1] if hist else None
        except Exception:
            out[key] = None
    return out, _mtime(f'{md}/index.html')


# ── 投組 / 健康 / 生活 ───────────────────────────

@_wrap
def portfolio():
    d = _proxy('http://localhost:5800/api/data', timeout=20, ttl=300)
    return d, datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def health_all():
    out = {'services': None, 'log_scan': None}
    try:
        out['services'] = _proxy('http://localhost:5600/api/status', timeout=45, ttl=120)
    except Exception as e:
        out['services'] = {'error': str(e)}
    p = f'{CC}/logs/log_scan_report.json'
    try:
        out['log_scan'] = _read_json(p)
    except Exception:
        pass
    return out, _mtime(p)


@_wrap
def rabbit():
    d = _proxy('http://localhost:5200/api/today-actions', ttl=120)
    con = _ro_db(f'{CC}/rabbit-care/rabbit.db')
    row = con.execute(
        'SELECT log_date, SUM(amount_cc) FROM water_log GROUP BY log_date ORDER BY log_date DESC LIMIT 1'
    ).fetchone()
    con.close()
    out = dict(d)  # 複製，避免 mutate _proxy 快取物件
    out['water_today'] = {'date': row[0], 'cc': row[1]} if row else None
    return out, datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def skilltree():
    p = f'{CC}/skill-tree/skill_tree.db'
    con = _ro_db(p)
    cats = con.execute(
        'SELECT c.name, c.emoji, COUNT(s.id) FROM categories c '
        'LEFT JOIN skills s ON s.category_id = c.id GROUP BY c.id'
    ).fetchall()
    total_xp = con.execute('SELECT COALESCE(SUM(xp), 0) FROM xp_logs').fetchone()[0]
    con.close()
    return {'categories': [{'name': n, 'emoji': e, 'skills': c} for n, e, c in cats],
            'total_xp': total_xp}, _mtime(p)


@_wrap
def hay():
    p = f'{CC}/scripts/hay_current.json'
    d = _read_json(p)
    return d.get('items', []), d.get('updated', _mtime(p))


@_wrap
def stock_query(symbol: str):
    d = _proxy(f'http://localhost:5100/api/analyze?symbol={symbol}', timeout=25, ttl=60)
    return d, datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def sim_invest():
    accounts = _proxy('http://localhost:5250/api/accounts', ttl=300)
    out = []
    for a in accounts:
        detail = _proxy(f"http://localhost:5250/api/account/{a['id']}", ttl=300)
        latest = detail.get('latest') or {}
        out.append({
            'id': a['id'], 'name': a['name'],
            'total_value_twd': latest.get('total_value_twd'),
            'unrealized_pnl_twd': latest.get('unrealized_pnl_twd'),
        })
    return out, datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def market_analysis():
    analysis = _proxy('http://localhost:5350/api/analysis', ttl=300)
    live = _proxy('http://localhost:5350/api/live', ttl=60)

    hit_rates = analysis.get('hit_rates') or {}
    next_day_up = hit_rates.get('next_day_up', 0)

    intraday_types = analysis.get('intraday_types') or []
    top_type = intraday_types[0] if intraday_types else None

    index_change_pct = live.get('index_change_pct', 0)

    return {
        'top_type': top_type,
        'hit_up': next_day_up,
        'index_change_pct': index_change_pct,
    }, datetime.now().strftime('%Y-%m-%d %H:%M')


SIGNALS = {
    'daytrade': daytrade, 'swing': swing, 'intraday': intraday, 'ma': ma,
    'chips': chips, 'news': news, 'market-fear': market_fear,
}
