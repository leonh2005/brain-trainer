"""
訊號計算 — 從 intraday_monitor.py 複製核心函數，加上回測包裝。
訊號 7（外盤比）、8（委買委賣差）需要即時 tick 資料，歷史回放無法計算，回傳 null。
"""

import pandas as pd
import warnings
warnings.filterwarnings('ignore')

SIGNAL_THRESHOLD = 4
VOL_SIGNAL_KEYS  = ['昨量', '單K', '超越開盤量']

# ── 指標計算（原版，零修改）─────────────────────────────────────────────────

def calc_vwap(df):
    cum_vol = df['volume'].cumsum()
    cum_tp_vol = (df['close'] * df['volume']).cumsum()
    return cum_tp_vol / cum_vol.replace(0, float('nan'))


def calc_obv(df):
    obv = [0]
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i-1]:
            obv.append(obv[-1] + df['volume'].iloc[i])
        elif df['close'].iloc[i] < df['close'].iloc[i-1]:
            obv.append(obv[-1] - df['volume'].iloc[i])
        else:
            obv.append(obv[-1])
    return pd.Series(obv, index=df.index)


def calc_kd(df, n=9):
    low_n  = df['low'].rolling(n).min()
    high_n = df['high'].rolling(n).max()
    rsv = (df['close'] - low_n) / (high_n - low_n + 1e-9) * 100
    K = rsv.ewm(com=2, adjust=False).mean()
    D = K.ewm(com=2, adjust=False).mean()
    return K, D


def calc_macd(df, fast=12, slow=26, signal=9):
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    dif  = ema_fast - ema_slow
    macd = dif.ewm(span=signal, adjust=False).mean()
    return dif, macd


def calc_rsi(series, period):
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs    = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def detect_signals(df, snap, avg5, yday_vol=0):
    signals = []
    if len(df) < 30:
        return signals

    vwap = calc_vwap(df)
    obv  = calc_obv(df)
    K, D = calc_kd(df)
    dif, macd_sig = calc_macd(df)
    rsi5  = calc_rsi(df['close'], 5)
    rsi10 = calc_rsi(df['close'], 10)

    close = df['close']
    last  = -1
    prev  = -2

    if close.iloc[prev] < vwap.iloc[prev] and close.iloc[last] > vwap.iloc[last]:
        signals.append('VWAP突破↑')

    price_high30 = close.iloc[-30:].max()
    obv_is_high  = obv.iloc[last] >= obv.iloc[-30:].max() * 0.99
    price_lag    = close.iloc[last] < price_high30 * 0.995
    if obv_is_high and price_lag:
        signals.append('OBV領先創高')

    if all(K.iloc[i] >= 80 for i in [-3, -2, -1]):
        signals.append('KD鈍化≥80')

    macd_cross = dif.iloc[prev] < macd_sig.iloc[prev] and dif.iloc[last] > macd_sig.iloc[last]
    if macd_cross and dif.iloc[last] > 0 and macd_sig.iloc[last] > 0:
        signals.append('MACD0軸上金叉')

    rsi_diff_prev = rsi5.iloc[prev] - rsi10.iloc[prev]
    rsi_diff_now  = rsi5.iloc[last] - rsi10.iloc[last]
    if rsi_diff_prev < -2 and rsi_diff_now > 2:
        signals.append('RSI5穿RSI10↑')

    if avg5 > 0 and len(df) >= 1:
        today_vol = df['volume'].sum()
        elapsed_bars = max(len(df), 1)
        est_daily_vol = today_vol / elapsed_bars * 270
        if est_daily_vol > avg5 * 2:
            signals.append(f'預估量{est_daily_vol/avg5:.1f}x均量')

    # 訊號 7（外盤比）：需要 ask_vol 欄位，歷史資料無此欄位，自動跳過
    if 'ask_vol' in df.columns and 'bid_vol' in df.columns:
        total_tick_vol = df['ask_vol'].fillna(0).sum() + df['bid_vol'].fillna(0).sum()
        ask_ratio = df['ask_vol'].fillna(0).sum() / total_tick_vol if total_tick_vol > 0 else 0.5
        if ask_ratio >= 0.65:
            signals.append(f'外盤比{ask_ratio*100:.0f}%')

    # 訊號 8（委買委賣差）：snap.buy_volume=0 時自動跳過
    bv = snap.get('buy_volume', 0)
    sv = snap.get('sell_volume', 0)
    if sv > 0 and bv > sv * 1.5:
        signals.append(f'掛單委買>{bv//sv}x委賣')
    elif bv > 0 and sv == 0:
        signals.append('掛單委買大幅領先')

    cur_bar_vol = df['volume'].iloc[last]

    if yday_vol > 0 and cur_bar_vol >= yday_vol * 0.01:
        signals.append(f'昨量{cur_bar_vol/yday_vol*100:.1f}%單K')

    prev5_avg = 0
    if len(df) >= 6:
        prev5_avg = df['volume'].iloc[-6:-1].mean()
        if prev5_avg > 0 and cur_bar_vol >= prev5_avg * 5:
            signals.append(f'單K{cur_bar_vol/prev5_avg:.1f}x前5均')

    if 'ts' in df.columns:
        open_bar = df[pd.to_datetime(df['ts']).dt.hour == 9]
        if not open_bar.empty:
            open_vol = open_bar['volume'].iloc[0]
            if open_vol > 0 and cur_bar_vol >= open_vol * 0.9:
                signals.append(f'超越開盤量({cur_bar_vol}/{open_vol}張)')

    if prev5_avg > 0 and cur_bar_vol >= prev5_avg * 5:
        signals.append(f'量爆{cur_bar_vol/prev5_avg:.1f}x前5均')

    lk = min(20, len(close) - 1)
    if lk >= 5:
        p_min = close.iloc[-lk-1:-1].min()
        p_max = close.iloc[-lk-1:-1].max()
        d_min = dif.iloc[-lk-1:-1].min()
        d_max = dif.iloc[-lk-1:-1].max()
        bull_div = close.iloc[last] <= p_min * 1.002 and dif.iloc[last] > d_min
        bear_div = close.iloc[last] >= p_max * 0.998 and dif.iloc[last] < d_max
        if bull_div or bear_div:
            div_type = '底背離' if bull_div else '頂背離'
            signals.append(f'MACD{div_type}')

    return signals


_SIGNAL_WEIGHTS = {
    'VWAP突破':    1.0,
    'OBV領先':     1.0,
    'KD鈍化':      1.0,
    'MACD':        1.0,
    'RSI5':        1.0,
    '預估量':      1.5,
    '外盤比':      1.5,
    '昨量':        1.5,
    '單K':         1.5,
    '超越開盤量':  1.5,
    '掛單委買':    0.5,
}
_MAX_SCORE = sum(_SIGNAL_WEIGHTS.values())


def calc_confidence(signals):
    score = 0.0
    for sig in signals:
        for key, w in _SIGNAL_WEIGHTS.items():
            if key in sig:
                score += w
                break
    return min(100, round(score / _MAX_SCORE * 100))


def has_vol_signal(signals):
    return any(any(k in s for k in VOL_SIGNAL_KEYS) for s in signals)


def has_signal_12(signals):
    return any('量爆' in s for s in signals)


# ── 回放包裝 ────────────────────────────────────────────────────────────────

# 13個固定訊號名稱對應（用於前端亮燈）
SIGNAL_NAMES = [
    ('VWAP突破',   'VWAP突破↑'),
    ('OBV領先創高', 'OBV領先創高'),
    ('KD鈍化≥80',  'KD鈍化≥80'),
    ('MACD0軸上金叉', 'MACD0軸上金叉'),
    ('RSI5穿RSI10', 'RSI5穿RSI10↑'),
    ('預估量爆增',  '預估量'),
    ('外盤比≥65%',  None),           # None = 永遠 null（歷史無資料）
    ('委買委賣差',  None),           # None = 永遠 null
    ('昨量單K',    '昨量'),
    ('單K倍量',    '單K'),
    ('超越開盤量',  '超越開盤量'),
    ('量爆top30',  '量爆'),
    ('MACD背離',   '背離'),
]


def run_signals(bars: list, avg5: int, yday_vol: int) -> dict:
    """
    傳入截至當根的所有 bar（list of dict），回傳13個訊號狀態。
    bars 格式：[{ts, open, high, low, close, volume}, ...]
    """
    df = pd.DataFrame(bars)
    if df.empty or len(df) < 2:
        result = {name: None if kw is None else False for name, kw in SIGNAL_NAMES}
        return {'signals': result, 'confidence': 0, 'trigger': False, 'active': []}

    snap = {'buy_volume': 0, 'sell_volume': 0}
    raw_signals = detect_signals(df, snap, avg5, yday_vol)

    result = {}
    for display_name, keyword in SIGNAL_NAMES:
        if keyword is None:
            result[display_name] = None  # 歷史無資料
        else:
            result[display_name] = any(keyword in s for s in raw_signals)

    confidence = calc_confidence(raw_signals)
    trigger = has_signal_12(raw_signals) or (
        len(raw_signals) >= SIGNAL_THRESHOLD and has_vol_signal(raw_signals)
    )

    return {
        'signals': result,
        'confidence': confidence,
        'trigger': trigger,
        'active': raw_signals,
    }
