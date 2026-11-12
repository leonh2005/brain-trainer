#!/usr/bin/env python3
"""
盤中主力發動偵測器（雙向）（09:05–13:30 交易時段）

監控清單（2026-10-19 改動態）：每分鐘用 gateway 全市場快照，從「漲幅≥2% 且成交金額≥5 億」
的候選中，依「成交金額名次 + 漲幅名次」名次和取前 25 檔；新入選者訂閱即時 tick 並回補當日。
成交金額是 close × total_volume(張) × 1000。

資料層（2026-11-12 改走 shioaji-gateway）：向 gateway 訂閱 tick 並每 5 秒增量輪詢，
1 分鐘 K 棒由本地 tick 重算；中途加入或重啟由 gateway 以當日 ticks 回補。
（不自行登入 Shioaji：同帳號多程序直連會互踢 session，2026-11-12 實測 gateway 一天被踢 20 次）

雙軌更新（2026-08-05 改）：
  - 技術指標（VWAP/OBV/KD/MACD/RSI/MACD背離）每 60 秒隨K棒收盤重算一次
  - 量能/委買賣指標（預估量爆增/外盤內盤比/掛單失衡/昨量單K/均量倍數/超越開盤量/量爆）
    每 5 秒用當前累積中的K棒即時重算，跟上一根收盤K棒的技術指標合併判斷推播
偵測 13 項指標，多方/空方均觸發推播：
  - 需同時滿足：訊號數 ≥ SIGNAL_THRESHOLD 且含至少 1 個量能訊號

指標：
  1.  VWAP 突破↑ / 跌破↓
  2.  OBV 領先創高 / 創低
  3.  KD 鈍化（K≥80 / K≤20 持續3根）
  4.  MACD 0軸上金叉 / 0軸下死叉
  5.  RSI5 陡峭上穿 / 下穿 RSI10
  6.  預估量爆增（>近5日均量×2）【中性，多空共用】
  7.  外盤比≥65% / 內盤比≥65%
  8.  委買委賣差（掛單失衡）
  9.  昨量單K【量能，多空共用】
  10. 單K倍量（≥前5根均量×5）【量能，多空共用】
  11. 超越開盤量【量能，多空共用】
  12. 量爆（≥前5根均量×5）【量能，多空共用】
  13. MACD 底背離 / 頂背離
"""

import copy
import fcntl
import json
import os
import sys
import threading
import time
import warnings
from datetime import date, datetime, timedelta

import pandas as pd
import requests
from dotenv import load_dotenv
from vshape import VShapeParams, VShapeState, detect_v_shape, replay_state

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
from log_redact import redact  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
warnings.filterwarnings('ignore')

# ── 設定 ─────────────────────────────────────────────────────────────────────
BOT_TOKEN        = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
CHAT_ID          = "7556217543"
SIGNAL_THRESHOLD = 4                    # 觸發推播的最低訊號數
SIGNAL_MARGIN    = 2                    # 多空訊號數差距要達到這個門檻才推播，太接近視為方向不明
CONFIDENCE_MIN   = 36                   # 信心分數低於此門檻不推播
VOL_SIGNAL_KEYS  = ['昨量', '單K', '超越開盤量']  # 必要量能訊號（至少 1 個）
MAX_CHG_PCT      = 7.0                  # 順勢幅度門檻：漲逾此值不推多方、跌逾此值不推空方
COOLDOWN_MINUTES = 30
COOLDOWN_FILE    = '/tmp/intraday_cooldown.json'
AVG5_CACHE_FILE  = '/tmp/intraday_avg5_cache.json'

# 動態監控清單（走 shioaji-gateway 全市場快照挑選，不自行直連避免撞 451 連線上限）
GATEWAY           = 'http://localhost:5455'
UNIVERSE_SIZE     = 25
MIN_TURNOVER      = 5e8       # 成交金額門檻（元）
MIN_CHG_PCT       = 2.0       # |漲跌幅| 門檻（%）
SCAN_INTERVAL     = 60        # 名單重掃秒數
MAX_NEW_PER_SCAN  = 5         # 每輪最多新訂閱檔數，避免瞬時大量回補
DROP_STREAK       = 2         # 連續幾次未入選才淘汰
MIN_RESIDENCE_MIN = 15        # 新加入後最短停留分鐘數
LOCK_FILE         = '/Users/steven/CCProject/logs/intraday_monitor.lock'

# 固定監控清單：不受動態篩選（漲幅 ≥2%、金額 ≥5億、排除 0 開頭 ETF）影響。
# 2026-11-06 加入 00631L，用 V 型反轉三階段判定（見 vshape.py）。
# V 型反轉（做多 V／做空倒 V）：監控清單由 UI 維護，兩個方向各自一份代號清單。
# 清單檔與狀態檔都放在 finmind/.local/，UI 服務讀寫同一份，改完下一輪 sync 即生效。
VSHAPE_WATCH_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.local', 'vshape_watch.json')
VSHAPE_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.local', 'vshape_state.json')
VSHAPE_PUSHED_FILE = '/tmp/intraday_vshape_pushed.json'
DEFAULT_VSHAPE_WATCH = {'long': ['00631L'], 'short': ['00631L', '0050']}


def load_vshape_watch() -> dict:
    """讀 UI 維護的監控清單；檔案不存在或格式錯誤時回預設值。"""
    try:
        with open(VSHAPE_WATCH_FILE) as f:
            data = json.load(f)
        return {
            'long': [str(c) for c in (data.get('long') or [])],
            'short': [str(c) for c in (data.get('short') or [])],
        }
    except Exception:
        return {k: list(v) for k, v in DEFAULT_VSHAPE_WATCH.items()}


def fixed_codes() -> list:
    """兩個方向的聯集，維持順序去重（訂閱用）。"""
    return list(dict.fromkeys(_vshape_watch['long'] + _vshape_watch['short']))

# 台股休市日（與 scripts/ma_monitor.py 同源；休市時 snapshot 是前一日舊價，會誤篩）
TW_HOLIDAYS = {
    "2026-01-01",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-27",
    "2026-04-03", "2026-04-06",
    "2026-05-01",
    "2026-06-19",
    "2026-09-25", "2026-09-28",
    "2026-10-09", "2026-10-26",
    "2026-12-25",
}

# 執行期狀態（由 sync_universe() 動態維護）
_universe: dict = {}          # code -> name
_universe_max_chg: dict = {}  # code -> 順勢門檻（%），依合約漲停幅度換算
_universe_joined: dict = {}   # code -> 加入時間
_universe_missed: dict = {}   # code -> 連續未入選次數

# 族群定義（觀察用，不計入訊號）
SECTOR_PEERS = {
    '2408': ['2344'],
    '2344': ['2408'],
    '2317': ['2303'],
    '2303': ['2317'],
}

# ── Shioaji gateway（HTTP；不自行登入，避免與 gateway 互踢 session）─────────
# 2026-11-12：同帳號多程序直連會互踢（gateway 當天被踢 20 次），結算因此取不到價。
_snapshot_health = {'fail_streak': 0} # 快照連續失敗次數（gateway 異常偵測）
SESSION = requests.Session()
CLIENT_ID = 'intraday_monitor'
SNAPSHOT_TIMEOUT = 8
SUBSCRIBE_TIMEOUT = 30
TICKS_TIMEOUT = 8
DAILY_TIMEOUT = 15
_tick_cursor: dict = {}               # code -> 已收到的最大 seq
_gateway_boot_id = None               # gateway 重啟偵測


def _get_json(path: str, params: dict, timeout: int) -> dict:
    r = SESSION.get(f'{GATEWAY}{path}', params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('error') or f'{path} 失敗')
    return data


def wait_for_gateway(deadline_sec: int = 1500, interval: int = 30):
    """等 gateway 登入就緒（取代原本的直連登入＋暖機）。"""
    global _gateway_boot_id
    deadline = time.monotonic() + deadline_sec
    attempt = 0
    while True:
        attempt += 1
        try:
            r = SESSION.get(f'{GATEWAY}/health', timeout=5)
            r.raise_for_status()
            h = r.json()
            if h.get('logged_in') and h.get('state') == 'ok':
                _gateway_boot_id = h.get('boot_id')
                print(f'[startup] gateway 就緒（第 {attempt} 次檢查，gen={h.get("generation")}）')
                return
            raise RuntimeError(f'gateway 尚未就緒：state={h.get("state")} '
                               f'last_error={redact(h.get("last_error"))}')
        except Exception as e:
            print(f'[startup] 第 {attempt} 次連線 gateway 失敗：{type(e).__name__}: {redact(e)}')
            if time.monotonic() >= deadline:
                msg = f'⚠️ 盤中主力監控啟動失敗（重試 {attempt} 次）：{type(e).__name__}，今天不會有訊號'
                print(msg)
                # KeepAlive 會每 5 分鐘重啟，同日只告警一次避免洗版
                marker = f'/tmp/intraday_monitor_alert_{date.today().isoformat()}'
                if not os.path.exists(marker):
                    try:
                        send_telegram(msg)
                        open(marker, 'w').close()
                    except Exception:
                        pass
                raise SystemExit(1)
            time.sleep(interval)


def trading_days_ago(n):
    d = date.today()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime('%Y-%m-%d')


# ── 資料取得（訂閱制 tick 累積）─────────────────────────────────────────────
# {code: [(ts, close, volume, tick_type), ...]}；callback 在 Shioaji 執行緒，需加鎖
_tick_store: dict = {}
_tick_lock = threading.Lock()


def _decode_ticks(rows: list) -> list:
    """gateway tick 列 [ts_iso, close, volume, tick_type] -> 本地 tick store 格式。"""
    return [(pd.Timestamp(r[0]), float(r[1]), int(r[2]), int(r[3])) for r in rows]


def _merge_backfill(code: str, rows: list) -> int:
    """把回補 tick 併入本地 store：只取比現有最後一筆更新的，避免重複計量。"""
    with _tick_lock:
        streamed = _tick_store.get(code, [])
        last_ts = max((r[0] for r in streamed), default=None)
        kept = [r for r in rows if last_ts is None or r[0] > last_ts]
        if kept:
            _tick_store[code] = streamed + kept
    if kept:
        print(f'[backfill] {code}: 回補 {len(kept)} 筆（至 {max(r[0] for r in kept)}）')
    return len(kept)


def poll_ticks() -> list:
    """增量拉取 gateway 的 tick；回傳需要重新訂閱的代號。

    gateway 重啟（boot_id 變）或游標失效（reset）→ 全部重訂閱；
    ring 已淘汰舊 tick（truncated）→ 該檔帶 backfill 重訂閱補洞。
    """
    global _gateway_boot_id
    codes = list(_universe)
    if not codes:
        return []
    after = ','.join(f'{c}:{_tick_cursor.get(c, 0)}' for c in codes)
    try:
        data = _get_json('/ticks', {'codes': ','.join(codes), 'after': after,
                                    'client': CLIENT_ID}, TICKS_TIMEOUT)
    except Exception as e:
        print(f'[ticks] 輪詢失敗: {redact(e)}')
        return []
    if data.get('boot_id') != _gateway_boot_id:
        _gateway_boot_id = data.get('boot_id')
        _tick_cursor.clear()
        return list(_universe)          # gateway 重啟：全部重訂閱
    need, batch = [], {}
    for code, d in (data.get('data') or {}).items():
        if not d.get('subscribed') or d.get('reset'):
            _tick_cursor[code] = 0
            need.append(code)
            continue
        rows = _decode_ticks(d.get('ticks') or [])
        if rows:
            batch[code] = rows
        _tick_cursor[code] = int(d.get('seq') or 0)
        if d.get('truncated'):
            need.append(code)           # ring 已淘汰：重訂閱時帶 backfill 補洞
    if batch:
        with _tick_lock:
            for code, rows in batch.items():
                _tick_store.setdefault(code, []).extend(rows)
    return need


def pick_universe() -> dict:
    """從 gateway 全市場快照挑監控清單。

    候選池：|漲幅| ≥MIN_CHG_PCT 且成交金額 ≥MIN_TURNOVER。
    排序：成交金額名次 + 漲幅名次，取名次和最小的 UNIVERSE_SIZE 檔。
    只看成交金額會讓大型股佔滿名額（科嶠 4542 漲跌幅第 3、金額第 49 被擠掉）。
    股票與市場別都收（8358 金居是上櫃 .TWO），只排除 0 開頭 ETF。
    """
    r = requests.get(f'{GATEWAY}/market_snapshot', timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('error') or 'market_snapshot 失敗')

    candidates = []
    for s in data.get('items', []):
        code = s.get('code') or ''
        if not (code.isdigit() and len(code) == 4) or code.startswith('0'):
            continue
        close, vol, chg = s.get('close'), s.get('total_volume'), s.get('change_rate')
        if not close or not vol or chg is None:
            continue
        turnover = close * vol * 1000        # total_volume 單位是「張」
        if turnover >= MIN_TURNOVER and abs(chg) >= MIN_CHG_PCT:
            candidates.append((turnover, code, (s.get('name') or code).strip(), chg))

    rank_sum: dict = {}
    names: dict = {}
    for i, (_, code, name, _) in enumerate(sorted(candidates, key=lambda x: -x[0])):
        rank_sum[code] = i + 1
        names[code] = name
    for i, (_, code, _, _) in enumerate(sorted(candidates, key=lambda x: -abs(x[3]))):
        rank_sum[code] += i + 1

    return {code: names[code] for code in sorted(rank_sum, key=lambda c: rank_sum[c])[:UNIVERSE_SIZE]}


def _subscribe(code: str, name: str, now: datetime, keep_ticks: bool = False) -> bool:
    """向 gateway 訂閱並回補；成功回 True（呼叫端據此計算本輪新增檔數）。

    keep_ticks=True 供重連／補洞使用：不清掉當日已累積的 tick。
    09:00 前不要求回補（盤前沒有成交，省掉每檔一次歷史查詢）。
    """
    global _gateway_boot_id
    try:
        data = _get_json('/ticks/subscribe',
                         {'codes': code, 'client': CLIENT_ID,
                          'backfill': '1' if now.hour >= 9 else '0'},
                         SUBSCRIBE_TIMEOUT)
    except Exception as e:
        print(f'[subscribe] {code} 失敗: {redact(e)}')
        return False
    info = (data.get('data') or {}).get(code) or {}
    if not info.get('subscribed'):
        print(f'[subscribe] {code} 失敗（該檔今日無即時資料）: {redact(info.get("error"))}')
        return False
    _gateway_boot_id = data.get('boot_id') or _gateway_boot_id

    if not keep_ticks:
        with _tick_lock:
            _tick_store.pop(code, None)  # 重新加入時清空，避免舊資料漏掉淘汰期間的 tick
    _last_bar_signals.pop(code, None)
    _merge_backfill(code, _decode_ticks(info.get('backfill') or []))
    _tick_cursor[code] = int(info.get('resume_seq') or 0)

    # vshape 固定清單的標的不在當日候選池，呼叫端只能傳代號當 name；
    # gateway 已回傳合約中文名，這裡優先補上。
    if not name or name == code:
        name = info.get('name') or code
    # 順勢門檻按這檔自己的漲停幅度換算：10% 限制 → 7%，20%（槓桿 ETF）→ 14%。
    # 無漲跌幅限制的標的 shioaji 用 9999.95 當哨兵，這種要退回預設值，
    # 否則會算出數萬 % 的門檻讓追高過濾器完全失效。
    ref = info.get('reference') or 0
    limit_up = info.get('limit_up') or 0
    limit_pct = (limit_up - ref) / ref * 100 if ref and limit_up else 0
    _universe_max_chg[code] = (
        MAX_CHG_PCT * (limit_pct / 10.0) if 0 < limit_pct <= 25 else MAX_CHG_PCT
    )
    _universe[code] = name
    _universe_joined[code] = now
    _universe_missed[code] = 0
    print(f'[subscribe] {code} {name} 加入監控（共 {len(_universe)} 檔）')
    return True


def _unsubscribe(code: str) -> None:
    try:
        _get_json('/ticks/unsubscribe', {'codes': code, 'client': CLIENT_ID}, 5)
    except Exception as e:
        print(f'[unsubscribe] {code} 失敗: {redact(e)}')
    _universe.pop(code, None)
    _universe_max_chg.pop(code, None)
    _universe_joined.pop(code, None)
    _universe_missed.pop(code, None)
    _last_bar_signals.pop(code, None)
    _tick_cursor.pop(code, None)
    with _tick_lock:
        _tick_store.pop(code, None)
    print(f'[unsubscribe] {code} 移出監控（剩 {len(_universe)} 檔）')


def sync_universe() -> None:
    """重掃名單：新入選者訂閱＋回補；連續 DROP_STREAK 輪未入選且停留滿 MIN_RESIDENCE_MIN 才退訂。"""
    global _vshape_watch
    now = datetime.now()

    # 每輪重讀 UI 維護的 V 型監控清單，讓增刪標的在下一輪生效
    new_watch = load_vshape_watch()
    if new_watch != _vshape_watch:
        print(f'[vshape] 監控清單變更: 做多 {new_watch["long"]}｜做空 {new_watch["short"]}')
        _vshape_watch = new_watch
        for code in list(_vshape_states):
            if code not in fixed_codes():
                _vshape_states.pop(code, None)

    fixed = {code: (_universe.get(code) or code) for code in fixed_codes()}
    _ever_fixed.update(fixed)

    # 「首輪」以動態清單為準：固定清單先訂閱後 _universe 必然非空，
    # 若用 not _universe 判斷，首輪掃描失敗一次就再也補不滿 25 檔。
    first_round = not any(code not in fixed for code in _universe)

    # 固定監控標的先處理，不依賴 gateway 掃描：全市場快照在盤中可能逾時，
    # 若放在掃描之後，掃描失敗會連固定標的都訂不到。
    for code, name in fixed.items():
        if code in _universe:
            _universe[code] = _universe[code] or name
            _universe_missed[code] = 0
        elif not _subscribe(code, name, now) and code not in _fixed_watch_alerted:
            # 訂閱失敗時整個 V 型功能等於沒開；只提醒一次，避免每分鐘洗版
            _fixed_watch_alerted.add(code)
            print(f'[universe] 固定標的 {code} 訂閱失敗，V 型反轉本日不會運作')
            send_telegram(f"⚠️ <b>{code} {name} 訂閱失敗</b>\nV 型反轉今日不會運作，請檢查 Shioaji 合約或連線")

    # UI 已移除的固定標的：退訂並清掉狀態
    for code in list(_universe):
        if code in fixed:
            continue
        if code in _ever_fixed:
            _unsubscribe(code)
            _ever_fixed.discard(code)
            _fixed_watch_alerted.discard(code)
            print(f'[vshape] {code} 已從監控清單移除，退訂')

    try:
        picked = pick_universe()
    except Exception as e:
        # 掃描失敗時維持現有名單、不淘汰任何檔（避免 gateway 短暫異常就清空監控）
        print(f'[universe] 掃描失敗，維持現有 {len(_universe)} 檔: {e}')
        return
    if not picked:
        print('[universe] 掃描結果為空，維持現有名單')
        return

    # 固定監控標的也算在 picked 內，下方淘汰迴圈才不會把它退訂。
    # fixed 只有裸代號，這裡先用 _universe 已解析好的名字，避免把中文名覆寫回代號。
    picked = {**{c: _universe.get(c) or c for c in fixed}, **picked}

    # 開機首輪一次補滿（固定清單已先訂閱，不佔 added 名額），之後每輪最多新增 MAX_NEW_PER_SCAN 檔
    max_new = UNIVERSE_SIZE if first_round else MAX_NEW_PER_SCAN
    added = 0
    for code, name in picked.items():
        if code in _universe:
            # fixed 清單帶的是裸代號（vshape 標的不在候選池），別把 _subscribe
            # 剛從合約補好的中文名又蓋回去
            _universe[code] = _universe.get(code) or name
            _universe_missed[code] = 0
        elif added < max_new and _subscribe(code, name, now):
            added += 1

    for code in list(_universe):
        if code in picked:
            continue
        _universe_missed[code] = _universe_missed.get(code, 0) + 1
        stayed = (now - _universe_joined.get(code, now)).total_seconds()
        if _universe_missed[code] >= DROP_STREAK and stayed >= MIN_RESIDENCE_MIN * 60:
            _unsubscribe(code)

    print(f'[universe] 候選 {len(picked)} 檔 → 監控 {len(_universe)} 檔（本輪新增 {added}）')


def get_1min_kbars(code: str) -> pd.DataFrame:
    """由本地累積的 tick resample 成今日 1 分鐘 K 棒，附 bid_vol/ask_vol 欄位。

    輸出格式與舊版（api.ticks 全量重抓）完全一致，訊號計算不受影響。
    """
    try:
        with _tick_lock:
            rows = list(_tick_store.get(code, []))
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=['ts', 'close', 'volume', 'tick_type'])
        df = df.set_index('ts').sort_index()
        ohlcv = df['close'].resample('1min').ohlc()
        ohlcv['volume'] = df['volume'].resample('1min').sum()
        ohlcv['ask_vol'] = df[df['tick_type']==1]['volume'].resample('1min').sum()
        ohlcv['bid_vol'] = df[df['tick_type']==2]['volume'].resample('1min').sum()
        ohlcv = ohlcv.dropna(subset=['open']).reset_index()
        return ohlcv
    except Exception as e:
        print(f'[ticks] {code} 失敗: {e}')
        return pd.DataFrame()


def _parse_snaps(payload: dict) -> dict:
    result = {}
    for code, s in payload.items():
        result[code] = {
            'name':        s.get('name') or _universe.get(code, code),
            'close':       s.get('close'),
            'chg_pct':     round(float(s.get('change_rate') or 0), 2),
            'total_vol':   int(s.get('total_volume') or 0),
            'yday_vol':    int(s.get('yesterday_volume') or 0),
            'buy_volume':  int(s.get('buy_volume') or 0),
            'sell_volume': int(s.get('sell_volume') or 0),
        }
    return result


def get_snapshot(codes: list) -> dict:
    """批次取指定股票即時快照（走 gateway）"""
    try:
        data = _get_json('/snapshot', {'codes': ','.join(codes)}, SNAPSHOT_TIMEOUT)
        _snapshot_health['fail_streak'] = 0
        return _parse_snaps(data.get('data') or {})
    except Exception as e:
        _snapshot_health['fail_streak'] += 1
        print(f'[snapshot] 失敗（連續 {_snapshot_health["fail_streak"]} 次）: {redact(e)}')
        return {}


def _load_avg5_cache() -> dict:
    try:
        if os.path.exists(AVG5_CACHE_FILE):
            data = json.load(open(AVG5_CACHE_FILE))
            if data.get('date') == str(date.today()):
                return data.get('stocks', {})
    except Exception:
        pass
    return {}

def _save_avg5_cache(stocks: dict):
    with open(AVG5_CACHE_FILE, 'w') as f:
        json.dump({'date': str(date.today()), 'stocks': stocks}, f)

def get_avg5_vol(code: str, cache: dict) -> int:
    if code in cache:
        return cache[code]
    # 當日首次查詢偶發失敗（gateway 暖機），重試一次即可（2408 每日固定中招）
    for attempt in (1, 2):
        try:
            data = _get_json('/daily_ohlcv', {'code': code, 'days': 20}, DAILY_TIMEOUT)
            bars = [b for b in (data.get('bars') or []) if b.get('date') < str(date.today())]
            if len(bars) < 5:
                return 0
            result = int(round(sum(b['volume'] for b in bars[-5:]) / 5, 0))
            cache[code] = result
            return result
        except Exception as e:
            print(f'[avg5] {code} 第{attempt}次失敗: {redact(e)}')
            if attempt == 1:
                import time as _t
                _t.sleep(2)
    return 0


# ── 技術指標計算 ─────────────────────────────────────────────────────────────

def calc_vwap(df: pd.DataFrame) -> pd.Series:
    cum_vol = df['volume'].cumsum()
    cum_tp_vol = (df['close'] * df['volume']).cumsum()
    return cum_tp_vol / cum_vol.replace(0, float('nan'))


def calc_obv(df: pd.DataFrame) -> pd.Series:
    obv = [0]
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i-1]:
            obv.append(obv[-1] + df['volume'].iloc[i])
        elif df['close'].iloc[i] < df['close'].iloc[i-1]:
            obv.append(obv[-1] - df['volume'].iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=df.index)


def calc_kd(df: pd.DataFrame, n=9) -> tuple:
    low_n  = df['low'].rolling(n).min()
    high_n = df['high'].rolling(n).max()
    rsv = (df['close'] - low_n) / (high_n - low_n + 1e-9) * 100
    K = rsv.ewm(com=2, adjust=False).mean()
    D = K.ewm(com=2, adjust=False).mean()
    return K, D


def calc_macd(df: pd.DataFrame, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif  = ema_fast - ema_slow
    macd = dif.ewm(span=signal, adjust=False).mean()
    return dif, macd


def calc_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs    = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


# ── 訊號偵測（雙向）─────────────────────────────────────────────────────────

def detect_technical_signals(df: pd.DataFrame) -> tuple:
    """技術指標訊號（VWAP/OBV/KD/MACD/RSI/MACD背離），只在K棒收盤時算一次。

    回傳 (多方訊號清單, 空方訊號清單)
    """
    long_sigs  = []
    short_sigs = []
    if len(df) < 30:
        return long_sigs, short_sigs

    vwap = calc_vwap(df)
    obv  = calc_obv(df)
    K, D = calc_kd(df)
    dif, macd_sig = calc_macd(df)
    rsi5  = calc_rsi(df['close'], 5)
    rsi10 = calc_rsi(df['close'], 10)

    close = df['close']
    last  = -1
    prev  = -2

    # 1. VWAP 突破↑ / 跌破↓
    if close.iloc[prev] < vwap.iloc[prev] and close.iloc[last] > vwap.iloc[last]:
        long_sigs.append('VWAP突破↑')
    elif close.iloc[prev] > vwap.iloc[prev] and close.iloc[last] < vwap.iloc[last]:
        short_sigs.append('VWAP跌破↓')

    # 2. OBV 領先
    price_high30 = close.iloc[-30:].max()
    price_low30  = close.iloc[-30:].min()
    obv_slice    = obv.iloc[-30:]
    if obv.iloc[last] >= obv_slice.max() * 0.99 and close.iloc[last] < price_high30 * 0.995:
        long_sigs.append('OBV領先創高')
    if obv.iloc[last] <= obv_slice.min() * 1.01 and close.iloc[last] > price_low30 * 1.005:
        short_sigs.append('OBV領先創低')

    # 3. KD 鈍化
    if all(K.iloc[i] >= 80 for i in [-3, -2, -1]):
        long_sigs.append('KD鈍化≥80')
    if all(K.iloc[i] <= 20 for i in [-3, -2, -1]):
        short_sigs.append('KD鈍化≤20')

    # 4. MACD 0軸金叉 / 死叉
    cross_up   = dif.iloc[prev] < macd_sig.iloc[prev] and dif.iloc[last] > macd_sig.iloc[last]
    cross_down = dif.iloc[prev] > macd_sig.iloc[prev] and dif.iloc[last] < macd_sig.iloc[last]
    if cross_up and dif.iloc[last] > 0 and macd_sig.iloc[last] > 0:
        long_sigs.append('MACD0軸上金叉')
    if cross_down and dif.iloc[last] < 0 and macd_sig.iloc[last] < 0:
        short_sigs.append('MACD0軸下死叉')

    # 5. RSI5 穿越 RSI10
    rsi_diff_prev = rsi5.iloc[prev] - rsi10.iloc[prev]
    rsi_diff_now  = rsi5.iloc[last] - rsi10.iloc[last]
    if rsi_diff_prev < -2 and rsi_diff_now > 2:
        long_sigs.append('RSI5穿RSI10↑')
    if rsi_diff_prev > 2 and rsi_diff_now < -2:
        short_sigs.append('RSI5穿RSI10↓')

    # 13. MACD 底背離 / 頂背離
    lk = min(20, len(close) - 1)
    if lk >= 5:
        p_min = close.iloc[-lk-1:-1].min()
        p_max = close.iloc[-lk-1:-1].max()
        d_min = dif.iloc[-lk-1:-1].min()
        d_max = dif.iloc[-lk-1:-1].max()
        if close.iloc[last] <= p_min * 1.002 and dif.iloc[last] > d_min:
            long_sigs.append('MACD底背離')
        if close.iloc[last] >= p_max * 0.998 and dif.iloc[last] < d_max:
            short_sigs.append('MACD頂背離')

    return long_sigs, short_sigs


def detect_volume_signals(df: pd.DataFrame, snap: dict, avg5: int, yday_vol: int = 0) -> tuple:
    """量能/委買賣訊號，讀當前累積中的K棒，可高頻呼叫（不用等K棒收盤）。

    回傳 (多方訊號清單, 空方訊號清單)
    """
    long_sigs  = []
    short_sigs = []
    if len(df) < 30:
        return long_sigs, short_sigs

    last = -1

    # 6. 預估量爆增（中性，多空共用）
    if avg5 > 0 and len(df) >= 1:
        today_vol = df['volume'].sum()
        est_daily_vol = today_vol / max(len(df), 1) * 270
        if est_daily_vol > avg5 * 2:
            sig = f'預估量{est_daily_vol/avg5:.1f}x均量'
            long_sigs.append(sig)
            short_sigs.append(sig)

    # 7. 外盤比 / 內盤比
    if 'ask_vol' in df.columns and 'bid_vol' in df.columns:
        total_tick_vol = df['ask_vol'].fillna(0).sum() + df['bid_vol'].fillna(0).sum()
        if total_tick_vol > 0:
            ask_ratio = df['ask_vol'].fillna(0).sum() / total_tick_vol
            if ask_ratio >= 0.65:
                long_sigs.append(f'外盤比{ask_ratio*100:.0f}%')
            elif ask_ratio <= 0.35:
                short_sigs.append(f'內盤比{(1-ask_ratio)*100:.0f}%')

    # 8. 委買 / 委賣失衡
    bv = snap.get('buy_volume', 0)
    sv = snap.get('sell_volume', 0)
    if sv > 0 and bv > sv * 1.5:
        long_sigs.append(f'掛單委買>{bv//sv}x委賣')
    elif bv > 0 and sv == 0:
        long_sigs.append('掛單委買大幅領先')
    if bv > 0 and sv > bv * 1.5:
        short_sigs.append(f'掛單委賣>{sv//bv}x委買')
    elif sv > 0 and bv == 0:
        short_sigs.append('掛單委賣大幅領先')

    cur_bar_vol = df['volume'].iloc[last]

    # 9. 昨量單K（量能，多空共用）
    if yday_vol > 0 and cur_bar_vol >= yday_vol * 0.01:
        sig = f'昨量{cur_bar_vol/yday_vol*100:.1f}%單K'
        long_sigs.append(sig)
        short_sigs.append(sig)

    # 10. 均量倍數（量能，多空共用）
    prev5_avg = 0
    if len(df) >= 6:
        prev5_avg = df['volume'].iloc[-6:-1].mean()
        if prev5_avg > 0 and cur_bar_vol >= prev5_avg * 5:
            sig = f'單K{cur_bar_vol/prev5_avg:.1f}x前5均'
            long_sigs.append(sig)
            short_sigs.append(sig)

    # 11. 超越開盤量（量能，多空共用）
    open_bar = df[df['ts'].dt.hour == 9]
    if not open_bar.empty:
        open_vol = open_bar['volume'].iloc[0]
        if open_vol > 0 and cur_bar_vol >= open_vol * 0.9:
            sig = f'超越開盤量({cur_bar_vol}/{open_vol}張)'
            long_sigs.append(sig)
            short_sigs.append(sig)

    # 12. 量爆（量能，多空共用）
    if prev5_avg > 0 and cur_bar_vol >= prev5_avg * 5:
        sig = f'量爆{cur_bar_vol/prev5_avg:.1f}x前5均'
        long_sigs.append(sig)
        short_sigs.append(sig)

    return long_sigs, short_sigs


# ── 族群連動 ─────────────────────────────────────────────────────────────────

def get_sector_status(code: str, all_snaps: dict) -> str:
    peers = SECTOR_PEERS.get(code, [])
    if not peers:
        return ''
    parts = []
    for p in peers:
        if p in all_snaps:
            chg = all_snaps[p]['chg_pct']
            name = _universe.get(p, p)
            emoji = '🟢' if chg > 1 else ('🔴' if chg < -1 else '⬜')
            parts.append(f"{emoji}{p} {name} {chg:+.1f}%")
    return '  '.join(parts)


# ── 冷卻控制 ─────────────────────────────────────────────────────────────────

def load_cooldown() -> dict:
    try:
        if os.path.exists(COOLDOWN_FILE):
            with open(COOLDOWN_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_cooldown(cd: dict):
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(cd, f)


def is_in_cooldown(code: str, cd: dict) -> bool:
    if code not in cd:
        return False
    try:
        last_alert = datetime.fromisoformat(cd[code])
    except (TypeError, ValueError):
        return False
    elapsed = (datetime.now() - last_alert).total_seconds()
    return 0 <= elapsed < COOLDOWN_MINUTES * 60


# ── 信心分數 ─────────────────────────────────────────────────────────────────

_SIGNAL_WEIGHTS = {
    'VWAP':       1.0,
    'OBV':        1.0,
    'KD':         1.0,
    'MACD':       1.0,
    'RSI5':       1.0,
    '預估量':     1.5,
    '外盤比':     1.5,
    '內盤比':     1.5,
    '昨量':       1.5,
    '單K':        1.5,
    '超越開盤量': 1.5,
    '掛單委':     0.5,
}
_MAX_SCORE = sum(_SIGNAL_WEIGHTS.values())

def calc_confidence(signals: list) -> int:
    score = 0.0
    for sig in signals:
        for key, w in _SIGNAL_WEIGHTS.items():
            if key in sig:
                score += w
                break
    return min(100, round(score / _MAX_SCORE * 100))


def has_vol_signal(signals: list) -> bool:
    return any(any(k in s for k in VOL_SIGNAL_KEYS) for s in signals)


# ── 推播 ─────────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    """送出並回報是否真的成功；V 型推播靠這個判斷要不要記成已推。"""
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10
        )
        if r.status_code != 200:
            print(f'[telegram] HTTP {r.status_code}: {r.text[:200]}')
            return False
        return bool(r.json().get('ok'))
    except Exception as e:
        print(f'[telegram] 推播失敗: {e}')
        return False


def build_message(code: str, signals: list, snap: dict, avg5: int, sector_txt: str, direction: str,
                   tick_triggered: bool = False) -> str:
    now_str    = datetime.now().strftime('%H:%M')
    name       = snap.get('name') or _universe.get(code, code)
    close      = snap.get('close', 0)
    chg_pct    = snap.get('chg_pct', 0)
    vol_k      = snap.get('total_vol', 0)
    confidence = calc_confidence(signals)

    filled = round(confidence / 10)
    bar    = '█' * filled + '░' * (10 - filled)

    if direction == 'long':
        header    = f"🚀 <b>主力多方發動</b>｜{code} {name}｜{now_str}"
        conf_line = f"📈 多方信心：{bar} {confidence}%"
    else:
        header    = f"🔻 <b>主力空方發動</b>｜{code} {name}｜{now_str}"
        conf_line = f"📉 空方信心：{bar} {confidence}%"

    sig_lines   = '\n'.join(f"  ✅ {s}" for s in signals)
    sector_line = f"\n🔗 族群：{sector_txt}" if sector_txt else ''
    tick_line   = "\n⚡ tick即時觸發" if tick_triggered else ''

    return (
        f"{header}\n"
        f"💰 {close:.1f}（{chg_pct:+.1f}%）  量 {vol_k:,}張  均量 {avg5:,}張\n"
        f"{conf_line}\n"
        f"\n觸發 {len(signals)} 項訊號：\n{sig_lines}"
        f"{sector_line}"
        f"{tick_line}\n"
        f"\n⚠️ 數據參考，非投資建議"
    )


# ── V 型反轉推播（做多 V／做空倒 V）─────────────────────────────────────────
VSHAPE_TITLE = {'long': 'V型反轉', 'short': '倒V反轉'}
VSHAPE_STYLE = {
    'long': {
        'A': ('🟡', '疑似止跌', '開始觀察'),
        'B': ('🟢', '初步反轉', '初步確認反轉'),
        'C': ('🔵', '回測確認', '反轉結構進一步確認'),
    },
    'short': {
        'A': ('🟠', '疑似止漲', '開始觀察'),
        'B': ('🔴', '初步反轉', '初步確認反轉'),
        'C': ('🟣', '回測確認', '反轉結構進一步確認'),
    },
}


def _state_for(code: str, direction: str) -> VShapeState:
    """取得（必要時建立）某標的某方向的狀態。"""
    per = _vshape_states.setdefault(code, {})
    if direction not in per:
        per[direction] = VShapeState()
    return per[direction]


def _load_vshape_pushed() -> dict:
    """讀回今日已推播的階段 {code: {direction: {stage: 成立時間}}}（跨日自動失效）。"""
    try:
        with open(VSHAPE_PUSHED_FILE) as f:
            data = json.load(f)
        if data.get('date') != str(date.today()):
            return {}
        out: dict = {}
        for key, ts in (data.get('pushed') or {}).items():
            code, _, rest = key.partition(':')
            direction, _, stage = rest.partition(':')
            if code and stage in ('A', 'B', 'C') and direction in ('long', 'short'):
                out.setdefault(code, {}).setdefault(direction, {})[stage] = ts
            elif code and direction:
                # 舊格式（code:stage）：視為做多
                out.setdefault(code, {}).setdefault('long', {})[direction] = ts
        return out
    except Exception:
        return {}


def _save_vshape_pushed() -> None:
    pushed = {f'{code}:{direction}:{stage}': ts
              for code, per in _vshape_states.items()
              for direction, st in per.items()
              for stage, ts in st.pushed.items()}
    try:
        with open(VSHAPE_PUSHED_FILE, 'w') as f:
            json.dump({'date': str(date.today()), 'pushed': pushed}, f)
    except Exception as e:
        print(f'[vshape] 推播紀錄寫入失敗: {e}')


def _restore_vshape_pushed() -> None:
    """重啟後補回已推播紀錄，並標記需要回放當日 K 棒重建階段（否則 B/C 永遠接不上）。"""
    for code, per in _load_vshape_pushed().items():
        for direction, stages in per.items():
            state = _state_for(code, direction)
            state.pushed.update(stages)
            state.needs_replay = True
            print(f'[vshape] {code} {direction} 恢復已推階段 {stages}，待回放重建狀態')


def build_vshape_message(code: str, name: str, event, snap: dict, direction: str) -> str:
    icon, label, action = VSHAPE_STYLE[direction][event.stage]
    title = VSHAPE_TITLE[direction]
    close = snap.get('close', event.price)
    chg = snap.get('chg_pct')
    price_line = f"💰 {close:.2f}（{chg:+.1f}%）" if chg is not None else f"💰 {close:.2f}"
    d = event.detail

    if event.stage == 'A':
        if direction == 'long':
            lines = (
                f"跌破近 {d['lookback']} 根低點 {d['recent_low']:.2f} → 低點 {d['bar_low']:.2f}\n"
                f"下影線 {d['lower_shadow_pct']:.0f}%｜收盤位置 {d['close_pos_pct']:.0f}%｜量 {d['volume_ratio']:.1f}x"
            )
        else:
            lines = (
                f"突破近 {d['lookback']} 根高點 {d['recent_high']:.2f} → 高點 {d['bar_high']:.2f}\n"
                f"上影線 {d['upper_shadow_pct']:.0f}%｜收盤位置 {d['close_pos_pct']:.0f}%｜量 {d['volume_ratio']:.1f}x"
            )
    elif event.stage == 'B':
        if direction == 'long':
            lines = (
                f"站回 VWAP {event.vwap:.2f}｜突破 A 段高點 {d['a_high']:.2f}\n"
                f"量 {d['volume_ratio']:.1f}x"
            )
        else:
            lines = (
                f"跌破 VWAP {event.vwap:.2f}｜跌破 A 段低點 {d['a_low']:.2f}\n"
                f"量 {d['volume_ratio']:.1f}x"
            )
    else:
        if direction == 'long':
            lines = (
                f"回踩 VWAP {d['pullback_vwap']:.2f} 守住（低 {d['pullback_low']:.2f}）\n"
                f"突破 B 段高點 {d['b_high']:.2f}｜現 VWAP {event.vwap:.2f}"
            )
        else:
            lines = (
                f"反彈 VWAP {d['pullback_vwap']:.2f} 被壓回（高 {d['pullback_high']:.2f}）\n"
                f"跌破 B 段低點 {d['b_low']:.2f}｜現 VWAP {event.vwap:.2f}"
            )

    return (
        f"{icon} <b>{title}｜{event.stage} {label}</b>｜{code} {name}｜{event.ts}\n"
        f"\n{price_line}\n"
        f"{lines}\n"
        f"\n→ {action}\n"
        f"\n⚠️ 數據參考，非投資建議"
    )


def _vshape_display_name(code: str) -> str:
    """名稱由 _subscribe() 寫進 _universe（合約中文名）；這裡只是它還沒訂閱到之前的保險。"""
    return _universe.get(code) or code


def _save_vshape_state() -> None:
    """寫出目前各標的階段給 UI 讀；每分鐘 recompute 時呼叫一次。"""
    items = {}
    for code in fixed_codes():
        per = _vshape_states.get(code) or {}
        entry = {'name': _vshape_display_name(code)}
        for direction in ('long', 'short'):
            st = per.get(direction)
            if st is None:
                entry[direction] = {'stage': 'idle'}
                continue
            entry[direction] = {
                'stage': st.stage,
                'a_low': st.a_low, 'a_high': st.a_high,
                'b_high': st.b_high, 'b_low': st.b_low,
                'a_ts': st.a_ts, 'b_ts': st.b_ts,
                'pushed': dict(st.pushed),
            }
        items[code] = entry
    now = datetime.now()
    # tick 串流斷線時 updated 仍會是現在時間；記下最後一筆 tick 的時間，
    # 讀者才能分辨「真的在跑」與「階段停在幾小時前」。
    with _tick_lock:
        last_tick = None
        for rows in _tick_store.values():
            if rows:
                # 重登補洞會把較舊的 tick append 在後面，不能只看 rows[-1]
                m = max(r[0] for r in rows)
                if last_tick is None or m > last_tick:
                    last_tick = m
    payload = {
        'updated': now.strftime('%H:%M:%S'),   # 前端直接顯示，維持原格式
        'date': now.strftime('%Y-%m-%d'),      # 讀者靠這個判斷是否跨日殘檔
        'last_tick': last_tick.strftime('%H:%M:%S') if last_tick else None,
        'watch': _vshape_watch,
        'items': items,
    }
    try:
        os.makedirs(os.path.dirname(VSHAPE_STATE_FILE), exist_ok=True)
        # 先寫暫存再 os.replace：兩個常駐讀者（command-center 每 60 秒、
        # vshape-dashboard 每 30 秒）才不會撞到截斷後、寫完前的空檔
        tmp = VSHAPE_STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, VSHAPE_STATE_FILE)
    except Exception as e:
        print(f'[vshape] 狀態檔寫入失敗: {e}')


def _check_one(code: str, df: pd.DataFrame, snap: dict, direction: str) -> None:
    """跑單一方向的判定；有新階段且推播成功才記錄。"""
    key = (code, direction)
    # 上次推播失敗的事件先補送。stage 已隨 detect_v_shape 推進，重跑判定救不回來
    # （detect 只看最新一根，更晚的 K 不會再重現同一階段）。
    pending = _pending_vshape.get(key)
    if pending is not None:
        name = _universe.get(code) or code
        if send_telegram(build_vshape_message(code, name, pending, snap, direction)):
            state = _state_for(code, direction)
            state.pushed[pending.stage] = pending.ts
            _save_vshape_pushed()
            _pending_vshape.pop(key, None)
            print(f'[vshape] {code} {direction} {name} → {pending.stage}（補送成功）')
        return   # 補送期間先不推進新判定，避免事件堆積

    state = _state_for(code, direction)
    before = copy.deepcopy(state)
    try:
        if state.needs_replay and len(df) >= _vshape_params.min_bars:
            replay_state(df, state, _vshape_params, direction=direction)
            state.needs_replay = False
            print(f'[vshape] {code} {direction} 已回放 {len(df)} 根重建 → {state.stage}')
        event = detect_v_shape(df, state, _vshape_params, direction=direction)
    except Exception as e:
        state.__dict__.update(before.__dict__)
        print(f'[vshape] {code} {direction} 判定失敗（狀態已回滾）: {e}')
        return
    if event is None:
        return
    name = _universe.get(code) or code
    if not send_telegram(build_vshape_message(code, name, event, snap, direction)):
        # 不回滾 stage：回滾會讓後續 B/C 找不到 stage=='A' 的前置而整條鏈消失，
        # 改成把事件暫存，下一輪先補送。
        _pending_vshape[key] = event
        print(f'[vshape] {code} {direction} {event.stage} 推播失敗（已暫存，下一輪補送）')
        return
    state.pushed[event.stage] = event.ts
    _save_vshape_pushed()
    print(f"[vshape] {code} {direction} {name} → {event.stage}"
          f"（收 {event.price:.2f} VWAP {event.vwap:.2f}）")


def check_v_shape(code: str, df: pd.DataFrame, snap: dict) -> None:
    """對單一監控標的跑它被指派的方向（可同時做多與做空）。"""
    for direction in ('long', 'short'):
        if code in _vshape_watch[direction]:
            _check_one(code, df, snap, direction)


# ── 主程式 ─────────────────────────────────────────────────────────────────

# {code: {'long': [...], 'short': [...]}}，每分鐘K棒收盤時更新一次
_last_bar_signals: dict = {}

# 5日均量快取，程式啟動時載入一次，之後常駐記憶體共用（避免每5秒重複讀寫檔案）
_avg5_cache: dict = _load_avg5_cache()

# V 型反轉：監控清單由 UI 維護（finmind/.local/vshape_watch.json），每輪 sync 重讀。
# _vshape_states 結構 {code: {'long': VShapeState, 'short': VShapeState}}，兩個方向各自去重。
_vshape_watch: dict = load_vshape_watch()
_vshape_states: dict = {}
_pending_vshape: dict = {}   # (code, direction) -> event，推播失敗待補送
_vshape_params = VShapeParams()
_fixed_watch_alerted: set = set()   # 固定標的訂閱失敗只提醒一次
_ever_fixed: set = set()            # 曾進過固定清單的代號，UI 移除時用來判斷要不要立即退訂


def _in_main_hours(now: datetime) -> bool:
    """盤中主力發動（13 項指標）只在 09:00–10:30 判定與推播。

    2026-11-10 使用者要求：10:30 之後這套不做了。
    """
    return now.hour == 9 or (now.hour == 10 and now.minute <= 30)


def _in_vshape_hours(now: datetime) -> bool:
    """V 型反轉判定涵蓋整個交易時段 09:00–13:30，與主力監控時段無關。

    多留一分鐘到 13:31：13:30:0x 那次 recompute 只看得到 13:29 收盤的 K，
    13:30 那根要 13:31 才收盤；若 13:31 就 early-return，尾盤成形的階段永遠不推。
    """
    return (9 <= now.hour <= 12) or (now.hour == 13 and now.minute <= 31)


def recompute_technical_cache():
    """每分鐘K棒收盤時呼叫一次：重算技術指標訊號並存快取，供 volume_check() 合併使用。"""
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] intraday_monitor 開始執行")

    in_main = _in_main_hours(now)      # 主力發動：09:00–10:30
    in_vshape = _in_vshape_hours(now)  # V 型反轉：整個交易時段
    if not (in_main or in_vshape):
        print('非交易時段，略過')
        return

    for code in list(_universe):
        get_avg5_vol(code, _avg5_cache)  # 補齊缺漏的5日均量（首次呼叫才會打API）
        df = get_1min_kbars(code)
        if df.empty:
            continue
        # recompute 在整分 0-4 秒觸發，此時 resample 的最後一根是「剛開始的當前分鐘」，
        # 量能與價位都還在變動；技術指標與 V 型判定都只該用已收盤的 K 棒。
        # 每檔重算 current_min：第一輪補 avg5 可能跨過整分邊界，固定值會少算剛收盤那根。
        current_min = pd.Timestamp(datetime.now()).floor('min')
        closed = df[df['ts'] < current_min]
        if in_main:
            long_sigs, short_sigs = detect_technical_signals(closed)
            _last_bar_signals[code] = {'long': long_sigs, 'short': short_sigs}
        if in_vshape and (code in _vshape_watch['long'] or code in _vshape_watch['short']):
            try:
                snap = get_snapshot([code]).get(code) or {}
            except Exception:
                snap = {}
            check_v_shape(code, closed, snap)

    _save_avg5_cache(_avg5_cache)
    _save_vshape_state()


def volume_check():
    """高頻（約5秒）呼叫：用當前累積中的K棒算量能訊號，跟上一根收盤K棒的技術訊號合併判斷是否推播。"""
    now = datetime.now()
    if not _in_main_hours(now):
        return

    codes     = list(_universe.keys())
    all_snaps = get_snapshot(codes)
    if not all_snaps:
        return

    cooldown    = load_cooldown()
    tick_triggered = now.second >= 5  # 緊接在整分收盤重算之後才算「收盤觸發」
    pushed = False

    for code in codes:
        if is_in_cooldown(code, cooldown):
            continue

        snap = all_snaps.get(code)
        if not snap:
            continue

        # 只讀快取，不呼叫 get_avg5_vol()：避免抓取失敗時的重試 sleep 拖慢 5 秒熱迴圈
        # （快取由 recompute_technical_cache() 每分鐘負責補齊，那裡才會真的打 API）
        avg5     = _avg5_cache.get(code, 0)
        yday_vol = snap.get('yday_vol', 0)
        df       = get_1min_kbars(code)
        if df.empty:
            continue

        vol_long, vol_short = detect_volume_signals(df, snap, avg5, yday_vol)
        tech = _last_bar_signals.get(code, {'long': [], 'short': []})
        long_sigs  = tech['long'] + vol_long
        short_sigs = tech['short'] + vol_short

        candidates = []
        if len(long_sigs) >= SIGNAL_THRESHOLD and has_vol_signal(long_sigs):
            candidates.append(('long', long_sigs))
        if len(short_sigs) >= SIGNAL_THRESHOLD and has_vol_signal(short_sigs):
            candidates.append(('short', short_sigs))

        if not candidates:
            continue

        # 多空訊號數差距不夠代表方向不明（實測log：36.7%的推播差距只有0~1個訊號），
        # 硬選信心較高的一邊推播容易誤判成反方向；差距太小就跳過，不推播。
        if abs(len(long_sigs) - len(short_sigs)) < SIGNAL_MARGIN:
            continue

        # 順勢方向已走完門檻：漲逾門檻不推多方、跌逾門檻不推空方（追高／追空點位已無空間）。
        # 門檻按合約漲跌停幅度換算（槓桿 ETF 是 20%，不能跟個股共用 7%）。
        # 必須「先過濾再取 max」：先取 max 的話，被擋掉的強方向會連帶丟掉另一邊仍合格的候選。
        chg_pct = snap.get('chg_pct', 0)
        max_chg = _universe_max_chg.get(code, MAX_CHG_PCT)
        # 每個候選只算一次信心分數（原本同一方向重複算 3 次）
        scored_all = [(d, s, calc_confidence(s)) for d, s in candidates]
        both_sides = len(scored_all) == 2
        scored = [
            (d, s, c) for d, s, c in scored_all
            if not (d == 'long' and chg_pct >= max_chg)
            and not (d == 'short' and chg_pct <= -max_chg)
        ]
        if not scored:
            continue
        direction, signals, conf = max(scored, key=lambda x: x[2])
        # 兩邊原本都達標、過濾後只剩一邊：只有「被砍掉的方向信心 >= 存活方向」才算方向不明。
        # 被砍的是較弱那邊時，較強方向仍完全合格，照推。
        if both_sides and len(scored) < 2:
            dropped = [c for d, _s, c in scored_all if d != direction]
            if dropped and max(dropped) >= conf:
                continue
        if conf < CONFIDENCE_MIN:
            continue

        sector_txt = get_sector_status(code, all_snaps)
        msg = build_message(code, signals, snap, avg5, sector_txt, direction, tick_triggered)
        name = _universe.get(code, code)
        if not send_telegram(msg):
            # 失敗仍要寫冷卻：volume_check 每 5 秒跑一次，不寫冷卻會對同一訊號
            # 一路重送到洗版（Telegram 429 或 reset 時單檔一天可重試上千次）
            cooldown[code] = datetime.now().isoformat()
            pushed = True
            print(f"  檢查 {code} {name} ... 推播失敗（已冷卻 {COOLDOWN_MINUTES} 分鐘）")
            continue
        cooldown[code] = datetime.now().isoformat()
        pushed = True
        dir_label = '多方' if direction == 'long' else '空方'
        print(f"  檢查 {code} {name} ... 多方:{len(long_sigs)} 空方:{len(short_sigs)}")
        print(f'  → {dir_label}推播已送出（信心 {conf}%）')

    if pushed:
        save_cooldown(cooldown)


if __name__ == '__main__':
    import time as _time

    # 單實例鎖：重複啟動會雙登入 Shioaji 額度、重複推播
    _lockf = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(_lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print('[startup] 已有另一個 intraday_monitor 在執行，結束')
        sys.exit(1)

    _now = datetime.now()
    if _now.weekday() >= 5 or _now.date().isoformat() in TW_HOLIDAYS:
        print(f"{_now.strftime('%Y-%m-%d %H:%M:%S')} [startup] 非交易日（{_now.date()}），結束")
        sys.exit(0)

    # KeepAlive 誤觸時（例如盤後被重啟）直接結束，避免反覆登入 Shioaji
    # V 型反轉要跑到 13:30，所以主程式不能只開到 10:30；主力推播由 _in_main_hours 控制
    if _now.hour < 8 or _now.hour > 13 or (_now.hour == 13 and _now.minute > 35):
        print('[startup] 非盤中時段，結束')
        sys.exit(0)

    # 以今日日期開頭：command-center sources.py 靠這行切分「當日」訊號，缺了會把昨日訊號當今天
    print(f"{_now.strftime('%Y-%m-%d %H:%M:%S')} [startup] intraday_monitor 啟動（動態清單）")
    print('[startup] 初始化 Shioaji gateway...')
    wait_for_gateway()

    _restore_vshape_pushed()      # 異常重啟時補回今日已推播的 V 型階段

    sync_universe()               # 開盤先掃名單：訂閱＋回補當日 ticks

    recompute_technical_cache()   # 開盤暖機：先算一次技術指標，避免 volume_check() 空等到整分邊界

    _next_scan = _time.monotonic() + SCAN_INTERVAL
    while True:
        _now = datetime.now()
        if _now.hour > 13 or (_now.hour == 13 and _now.minute > 35):
            print(f'[{_now.strftime("%H:%M:%S")}] 盤後，監控結束')
            break
        # 快照連續失敗只記錄；連線復原由 gateway 負責，本程式不再自行重登
        if _snapshot_health['fail_streak'] == 6:
            print(f'[{_now.strftime("%H:%M:%S")}] 快照連續失敗 6 次，等 gateway 恢復')
        if _time.monotonic() >= _next_scan:
            sync_universe()
            _next_scan = _time.monotonic() + SCAN_INTERVAL
        # 必須在 recompute_technical_cache 之前：剛收盤那一分鐘的最後幾筆 tick 要先進來
        need = poll_ticks()
        for code in need[:MAX_NEW_PER_SCAN]:
            _subscribe(code, _universe.get(code) or code, _now, keep_ticks=True)
        if _now.second < 5:
            recompute_technical_cache()
        volume_check()
        _time.sleep(5)

    try:
        _get_json('/ticks/unsubscribe',
                  {'codes': ','.join(_universe), 'client': CLIENT_ID}, 5)
        print('[shutdown] 已退訂 tick，gateway 連線保留給其他服務')
    except Exception:
        pass
