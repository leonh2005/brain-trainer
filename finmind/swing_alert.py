#!/usr/bin/env python3
"""
隔日沖候選推播 — 週間 12:30 執行
資料來源：永豐金 Shioaji API（行情/K棒）+ FinMind（外資、期貨法人）
"""

import requests
import pandas as pd
import warnings
import shioaji as sj
import os
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from ai_analysis import analyze_stock, format_ai_block

def trading_days_ago(n):
    """往回數 n 個交易日（跳過週末，未排除國定假日）"""
    from datetime import date, timedelta
    d = date.today()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime('%Y-%m-%d')

from FinMind.data import DataLoader

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
warnings.filterwarnings('ignore')

# ── 設定 ──────────────────────────────────────────
BOT_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
CHAT_ID   = "7556217543"
TOKEN     = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
TODAY     = datetime.today().strftime('%Y-%m-%d')
D5  = trading_days_ago(7)

WATCHLIST = {
    '3006': '晶豪科',
    '2344': '華邦電',
}


# FinMind（法人資料）
finmind = DataLoader()
finmind.login_by_token(api_token=TOKEN)

# ── Shioaji 連線（singleton）─────────────────────
_sj_api = None

def _get_sj():
    global _sj_api
    if _sj_api is None:
        _sj_api = sj.Shioaji(simulation=False)
        _sj_api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        print('[sj] 永豐 API 登入成功')
    return _sj_api


def get_sj_daily_kbars(symbol: str, days: int = 20) -> pd.DataFrame:
    """Shioaji 歷史日K（1分K聚合）"""
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks[symbol]
        if contract is None:
            return pd.DataFrame()
        start = str(date.today() - timedelta(days=days))
        end   = str(date.today())
        kb = api.kbars(contract, start=start, end=end)
        df = pd.DataFrame({**kb})
        if df.empty:
            return pd.DataFrame()
        df['ts'] = pd.to_datetime(df['ts'])
        df['_d'] = df['ts'].dt.date
        daily = df.groupby('_d').agg(
            open=('Open', 'first'),
            high=('High', 'max'),
            low=('Low', 'min'),
            close=('Close', 'last'),
            volume=('Volume', 'sum'),
        ).reset_index()
        daily['volume'] = daily['volume'] / 1000  # 股→張
        return daily.sort_values('_d').reset_index(drop=True)
    except Exception as e:
        print(f'[sj] kbars {symbol} 失敗: {e}')
        return pd.DataFrame()


def get_sj_snapshot_all() -> pd.DataFrame:
    """Shioaji 全市場即時快照，回傳與原 TWSE 格式相容的 DataFrame"""
    api = _get_sj()

    stocks = []
    try:
        for exchange in api.Contracts.Stocks:
            for contract in exchange:
                code = contract.code
                if len(code) == 4 and code.isdigit():
                    stocks.append({'symbol': code, 'name': contract.name})
        print(f'[sj] 取得 {len(stocks)} 檔股票合約')
    except Exception as e:
        print(f'[sj] 合約清單失敗: {e}')

    rows = []
    for i in range(0, len(stocks), 100):
        batch = stocks[i:i+100]
        try:
            contracts = [api.Contracts.Stocks.get(s['symbol']) for s in batch]
            name_map  = {s['symbol']: s['name'] for s in batch}
            valid = [c for c in contracts if c is not None]
            if not valid:
                continue
            snaps = api.snapshots(valid)
            for snap in snaps:
                if snap.close <= 0 or snap.total_volume <= 0:
                    continue
                rng = snap.high - snap.low
                close_pos = ((snap.close - snap.low) / rng * 100) if rng > 0 else 50
                amp_pct   = (rng / snap.low * 100) if snap.low > 0 else 0
                rows.append({
                    'Code':        snap.code,
                    'Name':        name_map.get(snap.code, ''),
                    'ClosingPrice': snap.close,
                    'vol_k':       snap.total_volume / 1000,
                    'chg_pct':     round(snap.change_rate, 2),
                    'amp_pct':     round(amp_pct, 2),
                    'close_pos':   round(close_pos, 1),
                })
        except Exception as e:
            print(f'[sj] snapshot batch {i} 失敗: {e}')

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def send_telegram(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )


def get_fi_yesterday(stock_id):
    """外資昨日買賣超（張）—— FinMind"""
    try:
        df = finmind.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=D5)
        fi = df[df['name']=='Foreign_Investor']
        last = fi.iloc[-1]
        return int((last['buy'] - last['sell']) / 1000), str(last['date'])[:10]
    except Exception as e:
        print(f'[finmind] fi_yesterday {stock_id} 失敗: {e}')
        return 0, ''


def get_trust_yesterday(stock_id):
    """投信昨日買賣超（張）—— FinMind"""
    try:
        df = finmind.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=D5)
        it = df[df['name']=='Investment_Trust']
        last = it.iloc[-1]
        return int((last['buy'] - last['sell']) / 1000)
    except Exception as e:
        print(f'[finmind] trust_yesterday {stock_id} 失敗: {e}')
        return 0


def compute_score(kbars: pd.DataFrame, chg_pct: float, close_pos: float, fi_net: int, trust_net: int) -> dict:
    """隔日沖7條件評分（2026-08-03 策略更新）。kbars 需含今日，按日期升冪排列。

    回傳 dict：score(0-7)、grade(A/B/C)、excluded(bool，位階排除)、reasons(list)
    """
    if len(kbars) < 21:
        return {'score': 0, 'grade': 'C', 'excluded': True, 'reasons': ['歷史K棒不足'], 'avg5_vol': 0}

    today = kbars.iloc[-1]
    hist = kbars.iloc[:-1]  # 不含今日

    avg5_vol = hist['volume'].tail(5).mean()
    avg20_vol = hist['volume'].tail(20).mean()
    ma5 = hist['close'].tail(5).mean()
    ma20 = hist['close'].tail(20).mean()
    prev_high = hist.iloc[-1]['high']

    # 連漲根數（含今日，往回數收盤價遞增的天數）
    closes = list(hist['close'].tail(5)) + [today['close']]
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] > closes[i - 1]:
            streak += 1
        else:
            break

    conditions = {
        '量能2倍+':   bool(avg20_vol > 0 and today['volume'] >= avg20_vol * 2),
        '站上5日線':  bool(today['close'] > ma5),
        '站上月線':   bool(today['close'] > ma20),
        '突破前高':   bool(today['close'] > prev_high),
        '收盤近高':   bool(close_pos >= 75),
        '漲幅甜蜜區': bool(3 <= chg_pct <= 8),
        '法人買超':   bool(fi_net > 0 or trust_net > 0),
    }
    score = sum(conditions.values())

    # 位階排除（不計分，直接不列入候選）
    deviated = bool(ma20 > 0 and abs(today['close'] - ma20) / ma20 > 0.15)
    over_streak = streak >= 4
    excluded = deviated or over_streak

    grade = 'A' if score >= 6 else ('B' if score == 5 else 'C')
    reasons = [k for k, v in conditions.items() if not v]
    if deviated:
        reasons.append('偏離月線>15%')
    if over_streak:
        reasons.append(f'已連漲{streak}根')

    return {'score': score, 'grade': grade, 'excluded': excluded, 'reasons': reasons, 'avg5_vol': round(avg5_vol, 0)}



def vol_signal(vol_k, avg5, chg_pct, close_pos):
    if avg5 == 0:
        return "量資料不足"
    ratio = vol_k / avg5
    if ratio >= 2:
        tag = f"爆量{ratio:.1f}x"
        if chg_pct > 2 and close_pos >= 70:
            return f"{tag} 收高 → 強勢吸貨 ✅"
        elif abs(chg_pct) < 1:
            return f"{tag} 漲幅縮 → 出貨警訊 ⚠️"
        elif chg_pct < -2:
            return f"{tag} 大跌 → 賣壓重 ❌"
        else:
            return f"{tag} 方向待確認 🔍"
    elif ratio < 0.6:
        return "縮量回檔 → 健康整理 ✅" if chg_pct < 0 else "縮量上漲 → 力道偏弱 🔍"
    else:
        return "量平穩"


def get_futures_direction():
    try:
        fut = finmind.taiwan_futures_institutional_investors(futures_id="TX", start_date=D5)
        fi = fut[fut['institutional_investors']=='外資']
        last = fi.iloc[-1]
        diff = int(last['long_open_interest_balance_volume']) - int(last['short_open_interest_balance_volume'])
        return diff, str(last['date'])[:10]
    except:
        return 0, ''


# ── 主程式 ────────────────────────────────────────

df = get_sj_snapshot_all()

if df.empty:
    print('[error] Shioaji 快照失敗，無法取得今日即時資料，結束執行')
    send_telegram(f"⚠️ {TODAY} 隔日沖掃描失敗：Shioaji 快照無法取得，請確認 API 連線")
    exit(1)

# 粗篩（快照即可判斷，避免對全市場逐檔抓K棒）：漲幅3-8%甜蜜區 + 收盤在當日區間前25% + 基本量能門檻
pool = df[
    (df['vol_k'] >= 3000) &
    (df['chg_pct'] >= 3.0) & (df['chg_pct'] <= 8.0) &
    (df['close_pos'] >= 75)
].nlargest(40, 'vol_k')

candidates = []
for _, row in pool.iterrows():
    code = row['Code']
    kbars = get_sj_daily_kbars(code, days=35)  # 含今日，至少需21筆才能算月線
    if kbars.empty:
        continue
    fi_net, fi_date = get_fi_yesterday(code)
    trust_net = get_trust_yesterday(code)
    result = compute_score(kbars, row['chg_pct'], row['close_pos'], fi_net, trust_net)
    if result['excluded'] or result['score'] < 5:  # 7條件評分制：<5分（C級）不列入
        continue
    avg5 = result['avg5_vol']  # compute_score 已從同一份 kbars 算好，不重打 Shioaji API
    signal = vol_signal(row['vol_k'], avg5, row['chg_pct'], row['close_pos'])

    candidates.append({
        'code':      code,
        'name':      row['Name'],
        'close':     row['ClosingPrice'],
        'chg_pct':   row['chg_pct'],
        'amp_pct':   row['amp_pct'],
        'vol_k':     int(row['vol_k']),
        'avg5':      int(avg5),
        'close_pos': row['close_pos'],
        'fi_net':    fi_net,
        'trust_net': trust_net,
        'fi_date':   fi_date,
        'signal':    signal,
        'reasons':   result['reasons'],
        'score':     result['score'],
        'grade':     result['grade'],
    })

candidates.sort(key=lambda c: (c['score'], c['vol_k']), reverse=True)

# 自選股量價訊號
watchlist_data = []
for code, name in WATCHLIST.items():
    try:
        h = get_sj_daily_kbars(code, days=20)
        if h.empty:
            continue
        h['vol_k'] = h['volume']
        # 排除今日（盤中不完整），用前5日算均量
        hist = h.iloc[:-1] if len(h) >= 2 else h
        avg5 = round(hist['vol_k'].tail(5).mean(), 0)
        last = h.iloc[-1]
        prev_close = h.iloc[-2]['close'] if len(h) >= 2 else last['open']
        chg_pct = round((last['close'] - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
        rng = last['high'] - last['low']
        close_pos = round((last['close'] - last['low']) / rng * 100, 1) if rng > 0 else 50
        amp_pct   = round(rng / last['low'] * 100, 2) if last['low'] > 0 else 0
        sig = vol_signal(last['vol_k'], avg5, chg_pct, close_pos)
        fi_net, fi_date = get_fi_yesterday(code)
        watchlist_data.append({
            'code': code, 'name': name,
            'close': last['close'], 'chg_pct': chg_pct,
            'vol_k': int(last['vol_k']), 'avg5': int(avg5),
            'signal': sig, 'fi_net': fi_net,
        })
    except Exception as e:
        print(f'watchlist {code} 失敗: {e}')

fut_diff, fut_date = get_futures_direction()
mkt_dir = "偏多 ↑" if fut_diff > 0 else "偏空 ↓"

# ── 組訊息 ────────────────────────────────────────

lines = [f"🌙 <b>隔日沖候選</b>｜{TODAY} 12:30\n"]
lines.append(f"🌐 大盤外資期貨（{fut_date}）：{mkt_dir}（{fut_diff:+,} 口）\n")
lines.append("📋 <b>篩選條件（7條件評分制，2026-08-03更新）</b>")
lines.append("量能2倍+均量 / 站上5日線 / 站上月線 / 突破前高 / 收盤近高 / 漲幅3-8% / 法人買超")
lines.append("≥6分A級 ≥5分B級，且排除連漲4根以上或偏離月線15%以上者\n")

if candidates:
    lines.append(f"✅ 符合 {len(candidates)} 檔：\n")
    for c in candidates[:8]:
        fi_icon = "🟢外資買超" if c['fi_net'] > 0 else ("🔴外資賣超" if c['fi_net'] < 0 else "⚪外資持平")
        trust_icon = "🟢投信買超" if c['trust_net'] > 0 else ("🔴投信賣超" if c['trust_net'] < 0 else "⚪投信持平")
        grade_emoji = "🅰️" if c['grade'] == 'A' else "🅱️"
        ai = analyze_stock(
            code=c['code'], name=c['name'], close=c['close'],
            chg_pct=c['chg_pct'], amp_pct=c['amp_pct'],
            vol_k=c['vol_k'], avg5=c['avg5'], close_pos=c['close_pos'],
            fi_net=c['fi_net'], signal=c['signal'], strategy="隔日沖"
        )
        reasons_txt = f"   未達：{'、'.join(c['reasons'])}\n" if c['reasons'] else ""
        lines.append(
            f"{grade_emoji} <b>{c['code']} {c['name']}</b>（{c['score']}/7分）\n"
            f"   收:{c['close']:.1f}  漲:{c['chg_pct']:+.1f}%  收盤位:{c['close_pos']:.0f}%\n"
            f"   量:{c['vol_k']:,}張（均{c['avg5']:,}）  {fi_icon} {c['fi_net']:+,}張  {trust_icon} {c['trust_net']:+,}張\n"
            f"   {c['signal']}\n"
            f"{reasons_txt}"
            f"{format_ai_block(ai)}\n"
        )
    lines.append("⚡ 進場：9:05~9:15回踩不破前高/平盤再進；尾盤(14:00~14:30)確認量增收高可提前佈局")
    lines.append(
        "🛑 出場：開高&gt;3%分批賣 ／ 開高續強看5分K跌破再出 ／ 平開看是否突破昨高 ／ 開低&gt;2%直接出場\n"
    )
else:
    lines.append("❌ 今日無符合隔日沖條件標的（7條件評分不足5分，或位階排除）\n")

if watchlist_data:
    lines.append("👀 <b>自選股狀況</b>")
    for w in watchlist_data:
        fi_str = f"外資{w['fi_net']:+,}張" if w['fi_net'] != 0 else "外資無資料"
        lines.append(
            f"📌 {w['code']} {w['name']}  收:{w['close']:.0f}  漲:{w['chg_pct']:+.1f}%  量:{w['vol_k']:,}張\n"
            f"   {w['signal']}  {fi_str}"
        )

lines.append("\n⚠️ 數據參考，非投資建議，買賣自負")

msg = "\n".join(lines)
send_telegram(msg)
print(msg)
