"""各服務資料源讀取器 — 全部唯讀，絕不寫入既有服務的任何資料"""
import json
import os
import re
import sqlite3
import time
import urllib.request
from datetime import datetime, date, timedelta

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

def _stock_sector(code: str) -> str:
    """讀 screener.py / daytrade_alert.py 寫的產業別快取（唯讀，未快取過就回空字串，不主動打 API）"""
    cache_path = f'/tmp/stock_sector_{code}.json'
    if not os.path.exists(cache_path):
        return ''
    try:
        return _read_json(cache_path).get('industry', '')
    except Exception:
        return ''


def _market_change_pct():
    """大盤（加權指數）今日漲跌 %，走 market-analysis(5350) 既有資料"""
    try:
        return _proxy('http://localhost:5350/api/live', ttl=60).get('index', {}).get('change_pct')
    except Exception:
        return None


def _sector_index_pcts() -> dict:
    """TWSE 類股指數今日漲跌 %，一次拿全部類股（收盤後才有當日資料）"""
    today = date.today().strftime('%Y%m%d')
    url = f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={today}&type=IND&response=json'
    try:
        d = _proxy(url, timeout=10, ttl=3600)
        rows = (d.get('tables') or [{}])[0].get('data') or []
    except Exception:
        return {}
    out = {}
    for r in rows:
        name = r[0]
        if not name.endswith('類指數'):
            continue
        base = name[:-3]
        sector = base if base.endswith('業') else base + '業'
        try:
            out[sector] = float(str(r[4]).replace(',', ''))
        except (ValueError, IndexError):
            continue
    return out


def _dir_match(stock_pct, ref_pct):
    """股票漲跌方向是否與參照值（大盤/族群指數）同向；資料不足回 None"""
    if stock_pct is None or ref_pct is None or stock_pct == 0 or ref_pct == 0:
        return None
    return (stock_pct > 0) == (ref_pct > 0)


def _live_chg_pcts(codes: list) -> dict:
    """走 shioaji-gateway(5455) 拿即時漲跌 %，command-center 一律不直連 Shioaji"""
    codes = [c for c in codes if c]
    if not codes:
        return {}
    try:
        d = _proxy(f'http://localhost:5455/snapshot?codes={",".join(codes)}', ttl=60)
        return {c: v.get('change_rate') for c, v in (d.get('data') or {}).items()}
    except Exception:
        return {}


@_wrap
def daytrade():
    p = '/tmp/daytrade_candidates.json'
    d = _read_json(p)
    mkt_pct = _market_change_pct()
    sector_pcts = _sector_index_pcts()
    live_pcts = _live_chg_pcts([c.get('code', '') for c in d])
    for c in d:
        c['sector'] = _stock_sector(c.get('code', ''))
        c['pct_change'] = live_pcts.get(c.get('code', ''))
        c['mkt_dir_match'] = _dir_match(c['pct_change'], mkt_pct)
        c['sector_dir_match'] = _dir_match(c['pct_change'], sector_pcts.get(c['sector']))
    track_path = f'{CC}/telebot/data/daytrade_track.json'
    if os.path.exists(track_path):
        with open(track_path, encoding='utf-8') as f:
            track = json.load(f)
        checked_dates = sorted(dt for dt, v in track.items() if v.get('checked'))
        if checked_dates:
            latest_date = checked_dates[-1]
            entry = track[latest_date]
            d = {'candidates': d, 'track': {
                'date': latest_date, 'checked_at': entry.get('checked_at'),
                'results': entry.get('track_results', []),
            }}
    return d, _mtime(p)


@_wrap
def swing():
    p = '/tmp/swing_candidates.json'
    d = _read_json(p)
    results = d.get('results', [])
    mkt_pct = _market_change_pct()
    sector_pcts = _sector_index_pcts()
    live_pcts = _live_chg_pcts([r.get('code', '') for r in results])
    for r in results:
        r['sector'] = _stock_sector(r.get('code', ''))
        live_pct = live_pcts.get(r.get('code', ''))
        if live_pct is not None:
            r['pct_change'] = live_pct
        r['mkt_dir_match'] = _dir_match(live_pct, mkt_pct)
        r['sector_dir_match'] = _dir_match(live_pct, sector_pcts.get(r['sector']))
    track_path = f'{CC}/telebot/data/swing_track.json'
    if os.path.exists(track_path):
        with open(track_path, encoding='utf-8') as f:
            track = json.load(f)
        checked_dates = sorted(dt for dt, v in track.items() if v.get('checked'))
        if checked_dates:
            latest = track[checked_dates[-1]]
            d['track'] = {'date': checked_dates[-1], 'results': latest.get('track_results', [])}
    return d, d.get('date', _mtime(p))


def _track_history(track_path: str):
    """通用歷史紀錄整理：依日期新到舊排序 + 累積命中率 + 累積損益（供 swing/daytrade history 共用）。"""
    if not os.path.exists(track_path):
        return {'days': [], 'total_hits': 0, 'total_picks': 0, 'hit_rate': None, 'total_net_pnl': 0}
    with open(track_path, encoding='utf-8') as f:
        track = json.load(f)

    days = []
    total_hits = total_picks = 0
    total_net_pnl = 0
    for dt in sorted(track, reverse=True):
        entry = track[dt]
        results = entry.get('track_results', [])
        hits = sum(1 for r in results if r.get('up'))
        day_net_pnl = sum(r['net_pnl'] for r in results if r.get('net_pnl') is not None)
        days.append({
            'date': dt, 'checked': entry.get('checked', False),
            'candidates': entry.get('candidates', []), 'results': results,
            'hits': hits, 'picks': len(results), 'net_pnl': day_net_pnl,
        })
        if entry.get('checked'):
            total_hits += hits
            total_picks += len(results)
            total_net_pnl += day_net_pnl
    hit_rate = round(total_hits / total_picks * 100, 1) if total_picks else None
    return {'days': days, 'total_hits': total_hits, 'total_picks': total_picks,
            'hit_rate': hit_rate, 'total_net_pnl': total_net_pnl}


def swing_history():
    """隔日沖候選歷史命中紀錄，供 /swing-history 頁面複查用。回傳依日期新到舊排序的清單 + 整體命中率 + 累積損益。"""
    return _track_history(f'{CC}/telebot/data/swing_track.json')


def daytrade_history():
    """當沖候選歷史命中紀錄，供 /daytrade-history 頁面複查用。回傳依日期新到舊排序的清單 + 整體命中率 + 累積損益。"""
    return _track_history(f'{CC}/telebot/data/daytrade_track.json')


@_wrap
def intraday():
    """解析 intraday_monitor.log 今日訊號（格式：檢查 CODE NAME ... → 多方/空方推播已送出（信心 N%））"""
    p = f'{CC}/logs/intraday_monitor.log'
    with open(p, errors='replace') as f:
        lines = f.readlines()[-20000:]
    # 每次啟動時 FinMind/shioaji 都會印出帶完整日期的 log（如 "2026-08-07 09:00:03..."），
    # 用今天最後一次這種行當分界，避免昨天殘留在同一份 log 裡的舊訊號被當成還沒更新。
    today_str = str(date.today())
    start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith(today_str):
            start_idx = i
    lines = lines[start_idx:]

    signals = []
    last_check = ''
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
    return {'signals': signals[-20:], 'last_check': last_check}, _mtime(p)


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


def _roc_to_iso(d: str) -> str:
    """民國年字串（如 1150811）轉西元 ISO 日期（2026-08-11）"""
    d = str(d)
    return f"{int(d[:-4]) + 1911}-{d[-4:-2]}-{d[-2:]}"


_STOCK_CODE_RE = re.compile(r'^\d{4}[A-Z]?$')  # 一般股票代碼（含特別股），排除權證/可轉債等衍生代碼


def _disposition_period(period: str, sep: str) -> tuple:
    parts = [p.strip().replace('/', '') for p in period.split(sep)]
    return _roc_to_iso(parts[0]), _roc_to_iso(parts[-1])


def _twse_punish_rows(url: str) -> list:
    d = _proxy(url, ttl=1800)
    return (d or {}).get('data', [])


def _next_trading_day(iso_date: str) -> str:
    """處置期間最後一天的隔一個交易日（僅排除週末，未含國定假日）——
    處置「迄日」當天仍在處置中，實際解除限制（出關）是這一天之後的下一個交易日。"""
    d = date.fromisoformat(iso_date) + timedelta(days=1)
    while d.weekday() >= 5:  # 5=六 6=日
        d += timedelta(days=1)
    return d.isoformat()


@_wrap
def disposition():
    """未來7天內仍處置中股票 + 明日真出關股（TWSE + TPEx 官方資料，30分鐘快取）

    同一檔股票違規次數多的話會有多筆處置公告（第一次/第二次/第三次處置…），
    取「結束日期最晚」的那筆代表目前實際狀態——避免把「出關又緊接著入關」的股票誤判為已出關。
    只看一般股票代碼（4碼數字，可含特別股字母），排除權證/可轉債等衍生證券。

    TWSE 資料源用 www.twse.com.tw 的 RWD 端點而非 openapi.twse.com.tw：後者只列「目前已生效」的處置，
    當天剛公布、尚未開始生效的新一輪處置（例如處置起始日是明天）不會出現，會漏抓「出關又入關」的情況。
    RWD 端點不帶日期參數時才會包含當天最新公布（帶日期區間查歷史反而還沒建檔進去），
    所以這裡「不帶參數」+「查過去30天」兩次都抓，合併起來才完整。
    """
    # 45天：處置期間通常在1個月內結束，抓寬一點避免漏掉公布較早、但期間仍在延續的處置
    history_start = (date.today() - timedelta(days=45)).strftime('%Y%m%d')
    today_str = date.today().strftime('%Y%m%d')
    twse_latest = _twse_punish_rows('https://www.twse.com.tw/rwd/zh/announcement/punish?response=json')
    twse_history = _twse_punish_rows(
        f'https://www.twse.com.tw/rwd/zh/announcement/punish?startDate={history_start}&endDate={today_str}&response=json'
    )

    tpex = _proxy('https://www.tpex.org.tw/openapi/v1/tpex_disposal_information', ttl=1800)

    items = []
    for row in twse_latest + twse_history:
        try:
            code = str(row[2])
            if not _STOCK_CODE_RE.match(code):
                continue
            start, end = _disposition_period(row[6], '～')
            items.append({'code': code, 'name': row[3], 'market': 'TWSE',
                           'start': start, 'end': end, 'reason': row[5]})
        except (ValueError, IndexError, TypeError):
            continue
    for r in (tpex or []):
        code = r.get('SecuritiesCompanyCode', '')
        if not _STOCK_CODE_RE.match(code):
            continue
        try:
            start, end = _disposition_period(r.get('DispositionPeriod', ''), '~')
            items.append({'code': code, 'name': r.get('CompanyName'), 'market': 'TPEx',
                           'start': start, 'end': end, 'reason': r.get('DispositionReasons', '')})
        except (ValueError, IndexError):
            continue

    # 同一代碼取結束日期最晚的那筆，代表目前真實處置狀態
    current = {}
    for it in items:
        prev = current.get(it['code'])
        if prev is None or it['end'] > prev['end']:
            current[it['code']] = it
    for it in current.values():
        it['exit_date'] = _next_trading_day(it['end'])  # 出關日＝迄日的下一個交易日，非迄日本身

    today_iso = date.today().isoformat()
    window_end = (date.today() + timedelta(days=7)).isoformat()
    tomorrow_iso = (date.today() + timedelta(days=1)).isoformat()

    tomorrow_exit = [v for v in current.values() if v['exit_date'] == tomorrow_iso]
    # 明日已出關的不重複列在「未來7天仍處置中」，兩份清單互斥
    still_in = sorted(
        [v for v in current.values()
         if v['end'] >= today_iso and v['start'] <= window_end and v['exit_date'] != tomorrow_iso],
        key=lambda v: v['exit_date']
    )

    return {'recent': still_in, 'tomorrow_exit': tomorrow_exit}, datetime.now().strftime('%Y-%m-%d %H:%M')


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
def daily_stock_analysis():
    d = _proxy('http://localhost:5650/api/v1/history?limit=5', ttl=300)
    return d.get('items', []), datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def market_cycle():
    d = _proxy('http://localhost:5905/api/chart-data', ttl=300)
    stages = (d.get('position') or {}).get('stages') or {}
    return {'econ': stages.get('econ', []), 'market': stages.get('market', [])}, \
        datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def market_analysis():
    analysis = _proxy('http://localhost:5350/api/analysis', ttl=300)
    live = _proxy('http://localhost:5350/api/live', ttl=60)

    hit_rates = analysis.get('hit_rates') or {}
    same_dir = hit_rates.get('sox_tsm_same_dir_twii_same') or {}
    next_day_up = same_dir.get('up', 0)  # 費半+台積電ADR 同漲→隔日台股同漲 %

    intraday_types = analysis.get('intraday_types') or []
    top_type = intraday_types[0] if intraday_types else None

    index_change_pct = (live.get('index') or {}).get('change_pct', 0)

    return {
        'top_type': top_type,
        'hit_up': next_day_up,
        'index_change_pct': index_change_pct,
    }, datetime.now().strftime('%Y-%m-%d %H:%M')


@_wrap
def guru_tracker():
    data = _proxy('http://localhost:5910/api/holders', ttl=300)
    holders = data.get('holders') or []
    steven = (data.get('steven_zhou') or [{}])[0]
    top3 = steven.get('top3') or []
    return {
        'holder_count': len(holders),
        'steven_zhou_count': steven.get('holding_count'),
        'steven_zhou_top': top3[0]['ticker'] if top3 else None,
    }, datetime.now().strftime('%Y-%m-%d %H:%M')


SIGNALS = {
    'daytrade': daytrade, 'swing': swing, 'intraday': intraday, 'ma': ma,
    'chips': chips, 'news': news, 'market-fear': market_fear,
    'disposition': disposition,
}
