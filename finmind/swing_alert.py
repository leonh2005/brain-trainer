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
BOT_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID   = "7556217543"
TOKEN     = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
TODAY     = datetime.today().strftime('%Y-%m-%d')
D10 = trading_days_ago(15)
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
    except:
        return 0, ''


def get_avg5_vol(stock_id):
    """近5日均量（張）—— Shioaji kbars；失敗時 fallback FinMind"""
    df = get_sj_daily_kbars(stock_id, days=20)
    # 排除今日（盤中 K 棒不完整，會拉偏均值）
    if len(df) >= 2:
        df = df.iloc[:-1]
    if len(df) >= 5:
        return round(df['volume'].tail(5).mean(), 0)
    # Fallback: FinMind
    try:
        h = finmind.taiwan_stock_daily(stock_id=stock_id, start_date=D10)
        if len(h) >= 5:
            return round(h['Trading_Volume'].tail(5).mean() / 1000, 0)
    except Exception:
        pass
    return 0


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

pool = df[
    (df['vol_k'] >= 5000) &
    (df['chg_pct'] >= 2.0) &
    (df['close_pos'] >= 70)
].nlargest(20, 'vol_k')

candidates = []
for _, row in pool.iterrows():
    code = row['Code']
    fi_net, fi_date = get_fi_yesterday(code)
    avg5 = get_avg5_vol(code)
    signal = vol_signal(row['vol_k'], avg5, row['chg_pct'], row['close_pos'])

    if fi_net > 0:
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
            'fi_date':   fi_date,
            'signal':    signal,
        })

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
lines.append("📋 <b>篩選條件</b>")
lines.append("量&gt;5000張 ＋ 漲&gt;2% ＋ 收高70%+ ＋ 外資買超\n")

if candidates:
    lines.append(f"✅ 符合 {len(candidates)} 檔：\n")
    for c in candidates[:8]:
        lines.append(
            f"🟢 <b>{c['code']} {c['name']}</b>\n"
            f"   收:{c['close']:.1f}  漲:{c['chg_pct']:+.1f}%  收盤位:{c['close_pos']:.0f}%\n"
            f"   量:{c['vol_k']:,}張（均{c['avg5']:,}）  外資:{c['fi_net']:+,}張\n"
            f"   {c['signal']}\n"
        )
    lines.append("⚡ 進場：收盤前30分鐘（14:00~14:30）確認量增收高再買")
    lines.append("🛑 出場：隔日開盤漲2~4%賣，開盤跳空綠立刻出\n")
else:
    lines.append("❌ 今日無符合隔日沖條件標的（外資未買超）\n")

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
