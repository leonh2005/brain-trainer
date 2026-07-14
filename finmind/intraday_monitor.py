#!/usr/bin/env python3
"""
盤中主力發動偵測器（雙向）— 每 60 秒執行（09:05–13:30 交易時段）

資料層（2026-07-14 改訂閱制）：啟動時對監控股訂閱即時 tick 並本地累積，
1 分鐘 K 棒由本地 tick 重算；中途重啟以一次 api.ticks 回補當日。
（舊版每 60 秒重抓全日 ticks，一天吃掉 Shioaji ~400MB 流量配額）
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

import json
import os
import threading
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
VOL_SIGNAL_KEYS  = ['昨量', '單K', '超越開盤量']  # 必要量能訊號（至少 1 個）
COOLDOWN_MINUTES = 30
COOLDOWN_FILE    = '/tmp/intraday_cooldown.json'
AVG5_CACHE_FILE  = '/tmp/intraday_avg5_cache.json'

# 固定監控清單
WATCHLIST = {
    '2408': '南亞科',
    '2344': '華邦電',
    '2317': '鴻海',
    '2303': '聯電',
    '6175': '立敦',
    '1785': '光洋科',
    '3374': '精材',
}

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
    """訂閱監控清單即時 tick，累積到 _tick_store。"""

    @api.on_tick_stk_v1()
    def _on_tick(exchange, tick):
        if getattr(tick, 'simtrade', False):  # 排除試撮
            return
        row = (pd.Timestamp(tick.datetime), float(tick.close), int(tick.volume), int(tick.tick_type))
        with _tick_lock:
            _tick_store.setdefault(tick.code, []).append(row)

    ok = 0
    for code in WATCHLIST:
        try:
            contract = api.Contracts.Stocks.get(code)
            if contract is None:
                print(f'[subscribe] {code} 無合約，跳過')
                continue
            api.quote.subscribe(
                contract,
                quote_type=sj.constant.QuoteType.Tick,
                version=sj.constant.QuoteVersion.v1,
            )
            ok += 1
        except Exception as e:
            print(f'[subscribe] {code} 失敗（該檔今日無即時資料）: {e}')
    print(f'[startup] 已訂閱 {ok}/{len(WATCHLIST)} 檔即時 tick')


def _backfill_today_ticks(api):
    """中途啟動時一次性回補當日 ticks（僅啟動時呼叫，之後全靠訂閱流）。"""
    import time as _t

    today = str(date.today())
    for code in WATCHLIST:
        contract = api.Contracts.Stocks.get(code)
        if contract is None:
            continue
        try:
            ticks = api.ticks(contract, date=today)
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
            continue
        with _tick_lock:
            streamed = _tick_store.get(code, [])
            # 已有串流 tick 時，回補只保留串流開始前的部分，避免重複計量
            cutoff = streamed[0][0] if streamed else None
            kept = [r for r in rows if cutoff is None or r[0] < cutoff]
            _tick_store[code] = kept + streamed
            # 時間基準對齊檢查用（首日驗證：兩值應相近、絕無小時級落差）
            if kept and streamed:
                print(f'[backfill] {code}: 回補{len(kept)}筆(至 {kept[-1][0]}) + 串流{len(streamed)}筆(自 {streamed[0][0]})')
        _t.sleep(0.2)
    total = sum(len(v) for v in _tick_store.values())
    print(f'[startup] 當日 ticks 回補完成（共 {total} 筆）')


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
            'name':        name or WATCHLIST.get(s.code, s.code),
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

def detect_signals(df: pd.DataFrame, snap: dict, avg5: int, yday_vol: int = 0) -> tuple:
    """回傳 (多方訊號清單, 空方訊號清單)"""
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


# ── 族群連動 ─────────────────────────────────────────────────────────────────

def get_sector_status(code: str, all_snaps: dict) -> str:
    peers = SECTOR_PEERS.get(code, [])
    if not peers:
        return ''
    parts = []
    for p in peers:
        if p in all_snaps:
            chg = all_snaps[p]['chg_pct']
            name = WATCHLIST.get(p, p)
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
    last_alert = datetime.fromisoformat(cd[code])
    return (datetime.now() - last_alert).seconds < COOLDOWN_MINUTES * 60


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


def build_message(code: str, signals: list, snap: dict, avg5: int, sector_txt: str, direction: str) -> str:
    now_str    = datetime.now().strftime('%H:%M')
    name       = snap.get('name') or WATCHLIST.get(code, code)
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

    return (
        f"{header}\n"
        f"💰 {close:.1f}（{chg_pct:+.1f}%）  量 {vol_k:,}張  均量 {avg5:,}張\n"
        f"{conf_line}\n"
        f"\n觸發 {len(signals)} 項訊號：\n{sig_lines}"
        f"{sector_line}\n"
        f"\n⚠️ 數據參考，非投資建議"
    )


# ── 主程式 ─────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] intraday_monitor 開始執行")

    if not (now.hour == 13 and now.minute <= 30) and not (9 <= now.hour <= 12):
        print('非交易時段，略過')
        return

    codes     = list(WATCHLIST.keys())
    all_snaps = get_snapshot(codes)
    if not all_snaps:
        print('無法取得快照，略過')
        return

    cooldown   = load_cooldown()
    avg5_cache = _load_avg5_cache()

    for code in codes:
        name = WATCHLIST.get(code, code)
        print(f"  檢查 {code} {name} ...", end=' ')

        if is_in_cooldown(code, cooldown):
            print('冷卻中，跳過')
            continue

        snap = all_snaps.get(code)
        if not snap:
            print('無快照資料')
            continue

        avg5     = get_avg5_vol(code, avg5_cache)
        yday_vol = snap.get('yday_vol', 0)
        df       = get_1min_kbars(code)

        if df.empty:
            print('無 K 棒資料')
            continue

        long_sigs, short_sigs = detect_signals(df, snap, avg5, yday_vol)
        print(f'多方:{len(long_sigs)} 空方:{len(short_sigs)}')

        # 優先推強方，同分推多方
        candidates = []
        if len(long_sigs) >= SIGNAL_THRESHOLD and has_vol_signal(long_sigs):
            candidates.append(('long', long_sigs))
        if len(short_sigs) >= SIGNAL_THRESHOLD and has_vol_signal(short_sigs):
            candidates.append(('short', short_sigs))

        if not candidates:
            continue

        # 取信心分數較高的方向
        direction, signals = max(candidates, key=lambda x: calc_confidence(x[1]))
        sector_txt = get_sector_status(code, all_snaps)
        msg = build_message(code, signals, snap, avg5, sector_txt, direction)
        send_telegram(msg)
        cooldown[code] = datetime.now().isoformat()
        dir_label = '多方' if direction == 'long' else '空方'
        print(f'  → {dir_label}推播已送出（信心 {calc_confidence(signals)}%）')

    save_cooldown(cooldown)
    _save_avg5_cache(avg5_cache)
    print('完成')


if __name__ == '__main__':
    import time as _time

    print('[startup] intraday_monitor 啟動，初始化 Shioaji...')
    _api = _get_sj()
    _probe_contracts = [_api.Contracts.Stocks[c] for c in WATCHLIST if _api.Contracts.Stocks.get(c)]
    for _wait in range(1, 61):
        _time.sleep(1)
        if _api.snapshots(_probe_contracts):
            print(f'[startup] Shioaji 暖機完成（{_wait}s），開始監控迴圈')
            break
    else:
        print('[startup] Shioaji 暖機逾時（60s），結束')
        exit(1)

    _setup_tick_stream(_api)      # 先訂閱（即刻開始累積）
    _backfill_today_ticks(_api)   # 再回補訂閱前的當日 ticks（僅此一次 api.ticks）

    while True:
        _now = datetime.now()
        if _now.hour > 13 or (_now.hour == 13 and _now.minute > 35):
            print(f'[{_now.strftime("%H:%M:%S")}] 盤後，監控結束')
            break
        main()
        _time.sleep(60)
