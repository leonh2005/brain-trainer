#!/usr/bin/env python3
"""
盤中主力發動偵測器（雙向）（09:05–13:30 交易時段）

監控清單（2026-10-19 改動態）：每分鐘用 gateway 全市場快照，從「漲幅≥2% 且成交金額≥5 億」
的候選中，依「成交金額名次 + 漲幅名次」名次和取前 25 檔；新入選者訂閱即時 tick 並回補當日。
成交金額是 close × total_volume(張) × 1000。

資料層（2026-07-14 改訂閱制）：對監控股訂閱即時 tick 並本地累積，
1 分鐘 K 棒由本地 tick 重算；中途加入或重啟以一次 api.ticks 回補當日。
（舊版每 60 秒重抓全日 ticks，一天吃掉 Shioaji ~400MB 流量配額）

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
import shioaji as sj
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
warnings.filterwarnings('ignore')

# ── 設定 ─────────────────────────────────────────────────────────────────────
BOT_TOKEN        = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
CHAT_ID          = "7556217543"
SIGNAL_THRESHOLD = 4                    # 觸發推播的最低訊號數
SIGNAL_MARGIN    = 2                    # 多空訊號數差距要達到這個門檻才推播，太接近視為方向不明
CONFIDENCE_MIN   = 36                   # 信心分數低於此門檻不推播
VOL_SIGNAL_KEYS  = ['昨量', '單K', '超越開盤量']  # 必要量能訊號（至少 1 個）
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
_universe_joined: dict = {}   # code -> 加入時間
_universe_missed: dict = {}   # code -> 連續未入選次數

# 族群定義（觀察用，不計入訊號）
SECTOR_PEERS = {
    '2408': ['2344'],
    '2344': ['2408'],
    '2317': ['2303'],
    '2303': ['2317'],
}

# ── Shioaji 連線 ─────────────────────────────────────────────────────────────
_sj_api = None

def _get_sj():
    global _sj_api
    if _sj_api is None:
        _sj_api = sj.Shioaji(simulation=False)
        _sj_api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
    return _sj_api


def connect_with_retry(deadline_sec: int = 900, interval: int = 30):
    """登入＋暖機，失敗每 interval 秒重試直到 deadline_sec。

    09:00 開盤前偶有暫時性失敗（2026-10-20 暖機逾時、10-21 login timeout），
    原本一次失敗就 exit，整天沒有盤中主力訊號，所以改成重試到期限為止。
    """
    global _sj_api
    deadline = time.monotonic() + deadline_sec
    attempt = 0
    while True:
        attempt += 1
        try:
            api = _get_sj()
            probe = [api.Contracts.Stocks[c] for c in ('2330',) if api.Contracts.Stocks.get(c)]
            for wait in range(1, 61):
                time.sleep(1)
                if api.snapshots(probe):
                    print(f'[startup] Shioaji 暖機完成（第 {attempt} 次嘗試、{wait}s）')
                    return api
            raise TimeoutError('暖機 60s 無回應')
        except Exception as e:
            print(f'[startup] 第 {attempt} 次連線失敗：{type(e).__name__}: {e}')
            try:
                if _sj_api is not None:
                    _sj_api.logout()
            except Exception:
                pass
            _sj_api = None
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


def _setup_tick_stream(api):
    """註冊即時 tick callback（訂閱由 _subscribe() 逐檔加入）。"""

    @api.on_tick_stk_v1()
    def _on_tick(exchange, tick):
        if getattr(tick, 'simtrade', False):  # 排除試撮
            return
        row = (pd.Timestamp(tick.datetime), float(tick.close), int(tick.volume), int(tick.tick_type))
        with _tick_lock:
            _tick_store.setdefault(tick.code, []).append(row)


def _backfill_code(api, contract, code: str) -> None:
    """個股加入監控時一次性回補當日 ticks（之後全靠訂閱流）。"""
    try:
        ticks = api.ticks(contract, date=str(date.today()))
        ts = pd.to_datetime(list(ticks.ts), unit='ns')
        # 歷史 ticks 理論上不含試撮；若 SDK 提供 simtrade 欄位則防禦性過濾（與即時流一致）
        sim = list(getattr(ticks, 'simtrade', []) or [])
        rows = [
            (ts[i], float(ticks.close[i]), int(ticks.volume[i]), int(ticks.tick_type[i]))
            for i in range(len(ts))
            if not sim or not sim[i]
        ]
    except Exception as e:
        print(f'[backfill] {code} 失敗: {e}')
        return
    with _tick_lock:
        streamed = _tick_store.get(code, [])
        # 已有串流 tick 時，回補只保留串流開始前的部分，避免重複計量
        cutoff = streamed[0][0] if streamed else None
        kept = [r for r in rows if cutoff is None or r[0] < cutoff]
        _tick_store[code] = kept + streamed
    if kept:
        print(f'[backfill] {code}: 回補 {len(kept)} 筆（至 {kept[-1][0]}）')


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


def _subscribe(api, code: str, name: str, now: datetime) -> bool:
    """訂閱並回補；成功回 True（呼叫端據此計算本輪新增檔數）。"""
    contract = api.Contracts.Stocks.get(code)
    if contract is None:
        print(f'[subscribe] {code} 無合約，跳過')
        return False
    try:
        api.quote.subscribe(
            contract,
            quote_type=sj.constant.QuoteType.Tick,
            version=sj.constant.QuoteVersion.v1,
        )
    except Exception as e:
        print(f'[subscribe] {code} 失敗（該檔今日無即時資料）: {e}')
        return False

    with _tick_lock:
        _tick_store.pop(code, None)      # 重新加入時清空，避免舊 cutoff 漏掉淘汰期間的 tick
    _last_bar_signals.pop(code, None)
    _backfill_code(api, contract, code)

    _universe[code] = name
    _universe_joined[code] = now
    _universe_missed[code] = 0
    print(f'[subscribe] {code} {name} 加入監控（共 {len(_universe)} 檔）')
    return True


def _unsubscribe(api, code: str) -> None:
    contract = api.Contracts.Stocks.get(code)
    try:
        if contract is not None:
            api.quote.unsubscribe(contract)
    except Exception as e:
        print(f'[unsubscribe] {code} 失敗: {e}')
    _universe.pop(code, None)
    _universe_joined.pop(code, None)
    _universe_missed.pop(code, None)
    _last_bar_signals.pop(code, None)
    with _tick_lock:
        _tick_store.pop(code, None)
    print(f'[unsubscribe] {code} 移出監控（剩 {len(_universe)} 檔）')


def sync_universe(api) -> None:
    """重掃名單：新入選者訂閱＋回補；連續 DROP_STREAK 輪未入選且停留滿 MIN_RESIDENCE_MIN 才退訂。"""
    try:
        picked = pick_universe()
    except Exception as e:
        # 掃描失敗時維持現有名單、不淘汰任何檔（避免 gateway 短暫異常就清空監控）
        print(f'[universe] 掃描失敗，維持現有 {len(_universe)} 檔: {e}')
        return
    if not picked:
        print('[universe] 掃描結果為空，維持現有名單')
        return

    now = datetime.now()
    # 開機首輪一次補滿，之後每輪最多新增 MAX_NEW_PER_SCAN 檔，避免瞬時大量回補
    max_new = UNIVERSE_SIZE if not _universe else MAX_NEW_PER_SCAN
    added = 0
    for code, name in picked.items():
        if code in _universe:
            _universe[code] = name
            _universe_missed[code] = 0
        elif added < max_new and _subscribe(api, code, name, now):
            added += 1

    for code in list(_universe):
        if code in picked:
            continue
        _universe_missed[code] = _universe_missed.get(code, 0) + 1
        stayed = (now - _universe_joined.get(code, now)).total_seconds()
        if _universe_missed[code] >= DROP_STREAK and stayed >= MIN_RESIDENCE_MIN * 60:
            _unsubscribe(api, code)

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


def _parse_snaps(snaps, contract_map: dict = None) -> dict:
    result = {}
    for s in snaps:
        name = ''
        if contract_map and s.code in contract_map:
            name = getattr(contract_map[s.code], 'name', '')
        result[s.code] = {
            'name':        name or _universe.get(s.code, s.code),
            'close':       s.close,
            'chg_pct':     round(float(s.change_rate), 2),
            'total_vol':   int(s.total_volume),
            'yday_vol':    int(getattr(s, 'yesterday_volume', 0)),
            'buy_volume':  getattr(s, 'buy_volume', 0),
            'sell_volume': getattr(s, 'sell_volume', 0),
        }
    return result


def get_snapshot(codes: list) -> dict:
    """批次取指定股票即時快照"""
    try:
        api = _get_sj()
        contracts = [api.Contracts.Stocks[c] for c in codes if api.Contracts.Stocks.get(c)]
        if not contracts:
            return {}
        cmap = {c.code: c for c in contracts}
        return _parse_snaps(api.snapshots(contracts), cmap)
    except Exception as e:
        print(f'[snapshot] 失敗: {e}')
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
    api = _get_sj()
    contract = api.Contracts.Stocks.get(code)
    if contract is None:
        return 0
    start = str(date.today() - timedelta(days=20))
    end   = str(date.today() - timedelta(days=1))
    # 當日首次 kbars 呼叫偶發失敗（連線暖機），重試一次即可（2408 每日固定中招）
    for attempt in (1, 2):
        try:
            kb = api.kbars(contract, start=start, end=end)
            df = pd.DataFrame({**kb})
            if df.empty:
                return 0
            df['ts'] = pd.to_datetime(df['ts'])
            df['_d'] = df['ts'].dt.date
            daily = df.groupby('_d')['Volume'].sum().reset_index()
            result = int(round(daily['Volume'].tail(5).mean(), 0)) if len(daily) >= 5 else 0
            cache[code] = result
            return result
        except Exception as e:
            print(f'[avg5] {code} 第{attempt}次失敗: {e}')
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

def send_telegram(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )


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


# ── 主程式 ─────────────────────────────────────────────────────────────────

# {code: {'long': [...], 'short': [...]}}，每分鐘K棒收盤時更新一次
_last_bar_signals: dict = {}

# 5日均量快取，程式啟動時載入一次，之後常駐記憶體共用（避免每5秒重複讀寫檔案）
_avg5_cache: dict = _load_avg5_cache()


def _in_trading_hours(now: datetime) -> bool:
    return (9 <= now.hour <= 12) or (now.hour == 13 and now.minute <= 30)


def recompute_technical_cache():
    """每分鐘K棒收盤時呼叫一次：重算技術指標訊號並存快取，供 volume_check() 合併使用。"""
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] intraday_monitor 開始執行")

    if not _in_trading_hours(now):
        print('非交易時段，略過')
        return

    for code in list(_universe):
        get_avg5_vol(code, _avg5_cache)  # 補齊缺漏的5日均量（首次呼叫才會打API）
        df = get_1min_kbars(code)
        if df.empty:
            continue
        long_sigs, short_sigs = detect_technical_signals(df)
        _last_bar_signals[code] = {'long': long_sigs, 'short': short_sigs}

    _save_avg5_cache(_avg5_cache)


def volume_check():
    """高頻（約5秒）呼叫：用當前累積中的K棒算量能訊號，跟上一根收盤K棒的技術訊號合併判斷是否推播。"""
    now = datetime.now()
    if not _in_trading_hours(now):
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

        # 取信心分數較高的方向
        direction, signals = max(candidates, key=lambda x: calc_confidence(x[1]))
        if calc_confidence(signals) < CONFIDENCE_MIN:
            continue
        sector_txt = get_sector_status(code, all_snaps)
        msg = build_message(code, signals, snap, avg5, sector_txt, direction, tick_triggered)
        send_telegram(msg)
        cooldown[code] = datetime.now().isoformat()
        pushed = True
        name      = _universe.get(code, code)
        dir_label = '多方' if direction == 'long' else '空方'
        print(f"  檢查 {code} {name} ... 多方:{len(long_sigs)} 空方:{len(short_sigs)}")
        print(f'  → {dir_label}推播已送出（信心 {calc_confidence(signals)}%）')

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
    if _now.hour < 8 or _now.hour > 13 or (_now.hour == 13 and _now.minute > 35):
        print('[startup] 非盤中時段，結束')
        sys.exit(0)

    # 以今日日期開頭：command-center sources.py 靠這行切分「當日」訊號，缺了會把昨日訊號當今天
    print(f"{_now.strftime('%Y-%m-%d %H:%M:%S')} [startup] intraday_monitor 啟動（動態清單）")
    print('[startup] 初始化 Shioaji...')
    _api = connect_with_retry()

    _setup_tick_stream(_api)      # 註冊 tick callback（訂閱由 sync_universe 逐檔加入）
    sync_universe(_api)           # 開盤先掃名單：訂閱＋回補當日 ticks

    recompute_technical_cache()   # 開盤暖機：先算一次技術指標，避免 volume_check() 空等到整分邊界

    _next_scan = _time.monotonic() + SCAN_INTERVAL
    while True:
        _now = datetime.now()
        if _now.hour > 13 or (_now.hour == 13 and _now.minute > 35):
            print(f'[{_now.strftime("%H:%M:%S")}] 盤後，監控結束')
            break
        if _time.monotonic() >= _next_scan:
            sync_universe(_api)
            _next_scan = _time.monotonic() + SCAN_INTERVAL
        if _now.second < 5:
            recompute_technical_cache()
        volume_check()
        _time.sleep(5)

    try:
        _api.logout()
        print('[shutdown] Shioaji 已登出，釋放連線額度')
    except Exception:
        pass
