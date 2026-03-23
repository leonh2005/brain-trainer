import pandas as pd
import numpy as np


def calc_ma(df: pd.DataFrame, periods=(5, 10, 20)) -> dict:
    return {f'ma{p}': df['close'].rolling(p).mean() for p in periods}


def calc_obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df['close'].diff().fillna(0))
    return (direction * df['volume']).cumsum()


def calc_macd(df: pd.DataFrame, fast=12, slow=26, signal=9):
    ema_f = df['close'].ewm(span=fast, adjust=False).mean()
    ema_s = df['close'].ewm(span=slow, adjust=False).mean()
    macd = ema_f - ema_s
    sig = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - sig
    return macd, sig, hist


def calc_rsi(df: pd.DataFrame, period=14) -> pd.Series:
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def detect_signals(df: pd.DataFrame) -> dict:
    """
    回傳四位一體買點 + 三種陷阱判斷
    需要至少 30 根日K
    """
    base = {
        'four_in_one': False,
        'vol_attack': False,
        'obv_breakout': False,
        'macd_ok': False,
        'rsi_ok': False,
        'trap': None,
    }
    if len(df) < 30:
        return base

    df = df.copy().reset_index(drop=True)
    df['obv'] = calc_obv(df)
    df['macd'], df['macd_sig'], df['macd_hist'] = calc_macd(df)
    df['rsi'] = calc_rsi(df)

    last = df.iloc[-1]
    prev = df.iloc[-2]
    window = df.iloc[-20:]

    avg_vol_5d = df['volume'].iloc[-6:-1].mean()

    # ── 四位一體 ─────────────────────────────────────────
    vol_attack = last['volume'] >= avg_vol_5d * 2
    obv_breakout = last['obv'] > df['obv'].iloc[-21:-1].max()
    macd_cross = (prev['macd'] < prev['macd_sig']) and (last['macd'] >= last['macd_sig'])
    macd_expand = (last['macd_hist'] > 0) and (last['macd_hist'] > prev['macd_hist'])
    macd_ok = macd_cross or macd_expand
    rsi_ok = 50 <= last['rsi'] <= 75

    four_in_one = vol_attack and obv_breakout and macd_ok and rsi_ok

    # ── 陷阱 ─────────────────────────────────────────────
    trap = None

    # 假突破：股價創高 + 量縮 + OBV 未創高
    price_new_high = last['close'] >= window['close'].iloc[:-1].max()
    vol_shrink = last['volume'] < avg_vol_5d
    obv_no_high = last['obv'] < df['obv'].iloc[-21:-1].max()
    if price_new_high and vol_shrink and obv_no_high:
        trap = 'false_breakout'

    # 主力出貨：漲但 OBV 掉頭 + RSI 頂背離
    obv_turn_down = (last['obv'] < prev['obv']) and (prev['obv'] < df['obv'].iloc[-3])
    rsi_div = (last['rsi'] < prev['rsi']) and (last['close'] >= prev['close'])
    if (last['close'] > prev['close']) and obv_turn_down and rsi_div:
        trap = 'distribution'

    # 底部洗盤：量縮至極 + OBV 平穩 + MACD 負值收斂 + RSI 脫離超賣 + 股價平
    vol_extreme_low = last['volume'] < avg_vol_5d * 0.3
    obv_stable = abs(last['obv'] - prev['obv']) < abs(df['obv'].diff().iloc[-10:-1]).mean() * 0.3 + 1
    macd_conv = (last['macd_hist'] < 0) and (abs(last['macd_hist']) < abs(prev['macd_hist']))
    rsi_recover = 20 < last['rsi'] < 40 and last['rsi'] > prev['rsi']
    price_flat = abs(last['close'] - prev['close']) / prev['close'] < 0.01
    if vol_extreme_low and obv_stable and macd_conv and rsi_recover and price_flat:
        trap = 'bottom_wash'

    return {
        'four_in_one': bool(four_in_one),
        'vol_attack': bool(vol_attack),
        'obv_breakout': bool(obv_breakout),
        'macd_ok': bool(macd_ok),
        'rsi_ok': bool(rsi_ok),
        'trap': trap,
    }


def build_chart_payload(df: pd.DataFrame, time_col='date', is_intraday=False) -> dict:
    """
    將 DataFrame 轉為前端 Lightweight Charts 所需格式
    time_col: 'date'（日K）或 'datetime'（分K）
    """
    ma = calc_ma(df)
    obv = calc_obv(df)
    macd, macd_sig, macd_hist = calc_macd(df)
    rsi = calc_rsi(df)

    def fmt_time(val):
        if is_intraday:
            ts = pd.Timestamp(val)
            # Lightweight Charts 分K 用 Unix timestamp（秒）
            return int(ts.timestamp())
        else:
            return pd.Timestamp(val).strftime('%Y-%m-%d')

    times = [fmt_time(df[time_col].iloc[i]) for i in range(len(df))]

    candles = [
        {'time': times[i],
         'open': float(df['open'].iloc[i]),
         'high': float(df['high'].iloc[i]),
         'low':  float(df['low'].iloc[i]),
         'close': float(df['close'].iloc[i])}
        for i in range(len(df))
    ]

    volumes = [
        {'time': times[i],
         'value': float(df['volume'].iloc[i]),
         'color': '#ef5350' if df['close'].iloc[i] >= df['open'].iloc[i] else '#26a69a'}
        for i in range(len(df))
    ]

    def series(s):
        return [{'time': times[i], 'value': float(v)}
                for i, v in enumerate(s) if pd.notna(v)]

    def hist_series(s):
        return [{'time': times[i], 'value': float(v),
                 'color': '#ef5350' if v >= 0 else '#26a69a'}
                for i, v in enumerate(s) if pd.notna(v)]

    return {
        'candles': candles,
        'volumes': volumes,
        'ma5':  series(ma['ma5']),
        'ma10': series(ma['ma10']),
        'ma20': series(ma['ma20']),
        'macd':       series(macd),
        'macd_signal': series(macd_sig),
        'macd_hist':  hist_series(macd_hist),
        'rsi': series(rsi),
        'obv': series(obv),
    }
