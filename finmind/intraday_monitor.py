#!/usr/bin/env python3
"""
盤中多方主力發動偵測器 — 每 5 分鐘執行（09:05–13:30 交易時段）
偵測 7 項指標，≥3 個同時觸發時推播 Telegram 通知。

指標：
  1. VWAP 突破（股價上穿均價線）
  2. OBV 領先創高（底背離）
  3. KD 鈍化（K值≥80 持續3根）
  4. MACD 0軸上金叉
  5. RSI5 陡峭上穿 RSI10
  6. 預估量爆增（>近5日均量×2）
  7. 委買委賣差（買量>賣量×1.5）
"""

import json
import os
import warnings
from datetime import date, datetime, timedelta

import pandas as pd
import requests
import shioaji as sj
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
warnings.filterwarnings('ignore')

# ── 設定 ─────────────────────────────────────────────────────────────────────
BOT_TOKEN        = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID          = "7556217543"
TOP_N            = 24                   # 監控量前 N 名
SIGNAL_THRESHOLD = 3                    # 觸發推播的最低訊號數
# 成交量相關訊號關鍵字（至少 1 個才推播）
# 量能類訊號（全部來自 1 分 K，至少 1 個才推播）
VOL_SIGNAL_KEYS  = ['預估量', '外盤比', '昨量', '單K', '超越開盤量']
COOLDOWN_MINUTES = 30                   # 同一股票冷卻時間（分鐘）
COOLDOWN_FILE    = '/tmp/intraday_cooldown.json'
AVG5_CACHE_FILE  = '/tmp/intraday_avg5_cache.json'  # 每日均量快取

# 族群定義（觀察用，不計入訊號）
SECTOR_PEERS = {
    '2344': ['2337', '2408'],
    '3006': ['2344', '2337'],
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


# ── 資料取得 ─────────────────────────────────────────────────────────────────

def get_1min_kbars(code: str) -> pd.DataFrame:
    """用 ticks resample 成今日 1 分鐘 K 棒，並附帶 bid_vol/ask_vol 欄位"""
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks.get(code)
        if contract is None:
            return pd.DataFrame()
        today = str(date.today())
        ticks = api.ticks(contract, date=today)
        df = pd.DataFrame({**ticks})
        if df.empty:
            return df
        df['ts'] = pd.to_datetime(df['ts'], unit='ns')
        df = df.set_index('ts').sort_index()
        # resample 成 1 分鐘 OHLCV
        ohlcv = df['close'].resample('1min').ohlc()
        ohlcv['volume'] = df['volume'].resample('1min').sum()
        # 外盤/內盤量（tick_type==1 外盤, ==2 內盤）
        ohlcv['ask_vol'] = df[df['tick_type']==1]['volume'].resample('1min').sum()
        ohlcv['bid_vol'] = df[df['tick_type']==2]['volume'].resample('1min').sum()
        ohlcv = ohlcv.dropna(subset=['open']).reset_index()
        ohlcv.rename(columns={'ts': 'ts'}, inplace=True)
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
            'name':        name or s.code,
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


def get_top_volume_stocks(n: int = TOP_N) -> list:
    """取即時成交量前 n 名的股票代碼（僅 TSE 上市，4碼純數字）"""
    try:
        api = _get_sj()
        # 取所有 TSE 合約，分批 200 筆送 snapshots
        all_contracts = [
            c for c in api.Contracts.Stocks.TSE
            if hasattr(c, 'code') and c.code.isdigit() and len(c.code) == 4
        ]
        cmap = {c.code: c for c in all_contracts}
        all_snaps = {}
        batch = 200
        for i in range(0, len(all_contracts), batch):
            chunk = all_contracts[i:i+batch]
            try:
                snaps = api.snapshots(chunk)
                all_snaps.update(_parse_snaps(snaps, cmap))
            except Exception:
                pass
        if not all_snaps:
            return [], {}
        # 依總量排序取前 n
        sorted_codes = sorted(all_snaps, key=lambda c: all_snaps[c]['total_vol'], reverse=True)
        top = sorted_codes[:n]
        print(f'[top{n}] ' + ' '.join(f"{c}({all_snaps[c]["total_vol"]//1000}K)" for c in top[:8]) + ' ...')
        return top, all_snaps   # 順便回傳快照節省重複呼叫
    except Exception as e:
        print(f'[top_vol] 失敗: {e}')
        return [], {}


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
    """近 5 日均量（張），優先從快取取，快取沒有才呼叫 API"""
    if code in cache:
        return cache[code]
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks.get(code)
        if contract is None:
            return 0
        start = str(date.today() - timedelta(days=20))
        end   = str(date.today() - timedelta(days=1))
        kb = api.kbars(contract, start=start, end=end)
        df = pd.DataFrame({**kb})
        if df.empty:
            return 0
        df['ts'] = pd.to_datetime(df['ts'])
        df['_d'] = df['ts'].dt.date
        daily = df.groupby('_d')['Volume'].sum().reset_index()
        result = int(round(daily['Volume'].tail(5).mean(), 0)) if len(daily) >= 5 else 0
        cache[code] = result  # 存入快取
        return result
    except Exception as e:
        print(f'[avg5] {code} 失敗: {e}')
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


# ── 訊號偵測 ─────────────────────────────────────────────────────────────────

def detect_signals(df: pd.DataFrame, snap: dict, avg5: int, yday_vol: int = 0) -> list:
    """回傳觸發訊號名稱清單"""
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
    last  = -1  # 最新一根
    prev  = -2  # 前一根

    # 1. VWAP 突破：前一根在 VWAP 下，現在在上方
    if close.iloc[prev] < vwap.iloc[prev] and close.iloc[last] > vwap.iloc[last]:
        signals.append('VWAP突破↑')

    # 2. OBV 領先創高：OBV 創盤中新高，但股價仍未突破近30根高點
    price_high30 = close.iloc[-30:].max()
    obv_is_high  = obv.iloc[last] >= obv.iloc[-30:].max() * 0.99
    price_lag    = close.iloc[last] < price_high30 * 0.995
    if obv_is_high and price_lag:
        signals.append('OBV領先創高')

    # 3. KD 鈍化：最近 3 根 K 值均 ≥ 80
    if all(K.iloc[i] >= 80 for i in [-3, -2, -1]):
        signals.append('KD鈍化≥80')

    # 4. MACD 0軸上金叉：DIF 上穿 MACD，且兩者均 > 0
    macd_cross = dif.iloc[prev] < macd_sig.iloc[prev] and dif.iloc[last] > macd_sig.iloc[last]
    if macd_cross and dif.iloc[last] > 0 and macd_sig.iloc[last] > 0:
        signals.append('MACD0軸上金叉')

    # 5. RSI5 陡峭上穿 RSI10：前一根差距<-2，現在差距>+2
    rsi_diff_prev = rsi5.iloc[prev] - rsi10.iloc[prev]
    rsi_diff_now  = rsi5.iloc[last] - rsi10.iloc[last]
    if rsi_diff_prev < -2 and rsi_diff_now > 2:
        signals.append('RSI5穿RSI10↑')

    # 6. 預估量爆增：從 1 分 K 累計量估算全日量 > 均量 × 2
    if avg5 > 0 and len(df) >= 1:
        today_vol = df['volume'].sum()          # 今日 1 分 K 累計量
        elapsed_bars = max(len(df), 1)
        est_daily_vol = today_vol / elapsed_bars * 270  # 估算全日（270根1分K）
        if est_daily_vol > avg5 * 2:
            signals.append(f'預估量{est_daily_vol/avg5:.1f}x均量')

    # 7. 外盤比例 > 65%（主力主動買，tick_type==1 外盤）
    if 'ask_vol' in df.columns and 'bid_vol' in df.columns:
        total_tick_vol = df['ask_vol'].fillna(0).sum() + df['bid_vol'].fillna(0).sum()
        ask_ratio = df['ask_vol'].fillna(0).sum() / total_tick_vol if total_tick_vol > 0 else 0.5
        if ask_ratio >= 0.65:
            signals.append(f'外盤比{ask_ratio*100:.0f}%')

    # 8. 委買委賣差（快照掛單，非 1 分 K 成交量，僅作輔助參考）
    bv = snap.get('buy_volume', 0)
    sv = snap.get('sell_volume', 0)
    if sv > 0 and bv > sv * 1.5:
        signals.append(f'掛單委買>{bv//sv}x委賣')
    elif bv > 0 and sv == 0:
        signals.append('掛單委買大幅領先')

    cur_bar_vol = df['volume'].iloc[last]  # 最新一根 1 分 K 量

    # 9. 昨量對比法：最新 1 分 K >= 昨日總量 1%
    if yday_vol > 0 and cur_bar_vol >= yday_vol * 0.01:
        signals.append(f'昨量{cur_bar_vol/yday_vol*100:.1f}%單K')

    # 10. 均量倍數法：最新 1 分 K >= 前 5 根平均量 × 3
    if len(df) >= 6:
        prev5_avg = df['volume'].iloc[-6:-1].mean()
        if prev5_avg > 0 and cur_bar_vol >= prev5_avg * 3:
            signals.append(f'單K{cur_bar_vol/prev5_avg:.1f}x前5均')

    # 11. 開盤量對比法：最新 1 分 K 接近或超越 9:01 開盤首根量
    open_bar = df[df['ts'].dt.hour == 9]
    if not open_bar.empty:
        open_vol = open_bar['volume'].iloc[0]
        if open_vol > 0 and cur_bar_vol >= open_vol * 0.9:
            signals.append(f'超越開盤量({cur_bar_vol}/{open_vol}張)')

    return signals


# ── 族群連動 ─────────────────────────────────────────────────────────────────

def get_sector_status(code: str, all_snaps: dict) -> str:
    peers = SECTOR_PEERS.get(code, [])
    if not peers:
        return ''
    parts = []
    for p in peers:
        if p in all_snaps:
            chg = all_snaps[p]['chg_pct']
            emoji = '🟢' if chg > 1 else ('🔴' if chg < -1 else '⬜')
            parts.append(f"{emoji}{p} {chg:+.1f}%")
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

# 各訊號權重（動能/籌碼類 1.0，量能類 1.5，因量能是最直接的主力行為）
_SIGNAL_WEIGHTS = {
    # 動能類（來自 1 分 K 價格）
    'VWAP突破':    1.0,
    'OBV領先':     1.0,
    'KD鈍化':      1.0,
    'MACD':        1.0,
    'RSI5':        1.0,
    # 量能類（來自 1 分 K 成交量）
    '預估量':      1.5,
    '外盤比':      1.5,
    '昨量':        1.5,
    '單K':         1.5,
    '超越開盤量':  1.5,
    # 掛單類（來自快照 order book，輔助）
    '掛單委買':    0.5,
}
_MAX_SCORE = sum(_SIGNAL_WEIGHTS.values())  # 14.0

def calc_confidence(signals: list) -> int:
    """根據觸發的訊號計算上漲信心百分比（0~100）"""
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


def build_message(code: str, signals: list, snap: dict, avg5: int, sector_txt: str) -> str:
    now_str    = datetime.now().strftime('%H:%M')
    name       = snap.get('name', code)
    close      = snap.get('close', 0)
    chg_pct    = snap.get('chg_pct', 0)
    vol_k      = snap.get('total_vol', 0)
    confidence = calc_confidence(signals)

    # 信心條
    filled = round(confidence / 10)
    bar = '█' * filled + '░' * (10 - filled)

    sig_lines   = '\n'.join(f"  ✅ {s}" for s in signals)
    sector_line = f"\n🔗 族群：{sector_txt}" if sector_txt else ''

    return (
        f"🚨 <b>主力發動訊號</b>｜{code} {name}｜{now_str}\n"
        f"💰 {close:.1f}（{chg_pct:+.1f}%）  量 {vol_k:,}張  均量 {avg5:,}張\n"
        f"📈 上漲信心：{bar} {confidence}%\n"
        f"\n觸發 {len(signals)} 項訊號：\n{sig_lines}"
        f"{sector_line}\n"
        f"\n⚠️ 數據參考，非投資建議"
    )


# ── 主程式 ─────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] intraday_monitor 開始執行")

    # 非交易時段略過（09:00–13:30）
    if not (now.hour == 13 and now.minute <= 30) and not (9 <= now.hour <= 12):
        print('非交易時段，略過')
        return

    # 取量前 24 名 + 全市場快照（一次完成）
    codes, all_snaps = get_top_volume_stocks(TOP_N)
    if not codes:
        print('無法取得成交量排行，略過')
        return
    # 補抓族群股快照（若不在 top24 內）
    peer_codes = [p for c in codes for p in SECTOR_PEERS.get(c, []) if p not in all_snaps]
    if peer_codes:
        all_snaps.update(get_snapshot(peer_codes))

    cooldown   = load_cooldown()
    avg5_cache = _load_avg5_cache()

    for code in codes:
        name = all_snaps.get(code, {}).get('name', '')
        print(f"  檢查 {code} {name} ...", end=' ')

        if is_in_cooldown(code, cooldown):
            print(f'冷卻中，跳過')
            continue

        snap = all_snaps.get(code)
        if not snap:
            print('無快照資料')
            continue

        avg5     = get_avg5_vol(code, avg5_cache)
        yday_vol = snap.get('yday_vol', 0)
        df = get_1min_kbars(code)

        if df.empty:
            print('無 K 棒資料')
            continue

        signals = detect_signals(df, snap, avg5, yday_vol)
        vol_ok  = has_vol_signal(signals)
        print(f'訊號 {len(signals)}: {signals}  量能訊號:{vol_ok}')

        if len(signals) >= SIGNAL_THRESHOLD and vol_ok:
            sector_txt = get_sector_status(code, all_snaps)
            msg = build_message(code, signals, snap, avg5, sector_txt)
            send_telegram(msg)
            cooldown[code] = datetime.now().isoformat()
            print(f'  → 推播已送出（信心 {calc_confidence(signals)}%）')

    save_cooldown(cooldown)
    _save_avg5_cache(avg5_cache)
    print('完成')


if __name__ == '__main__':
    main()
