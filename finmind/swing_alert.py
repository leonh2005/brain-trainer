#!/usr/bin/env python3
"""
隔日沖候選推播 — 週間 12:30 執行
依據今日盤中即時資料篩選隔日沖候選，收盤前 2 小時送出 Telegram
條件：今日量>5000張 + 漲幅>2% + 收盤位置>70% + 外資(昨)買超
"""

import requests
import pandas as pd
import warnings
from datetime import datetime, timedelta
from FinMind.data import DataLoader

warnings.filterwarnings('ignore')

# ── 設定 ──────────────────────────────────────────
BOT_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID   = "7556217543"
TOKEN     = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
TODAY     = datetime.today().strftime('%Y-%m-%d')
D10       = (datetime.today() - timedelta(days=15)).strftime('%Y-%m-%d')
D5        = (datetime.today() - timedelta(days=8)).strftime('%Y-%m-%d')

# 自選股 watchlist（可自行增減）
WATCHLIST = {
    '3006': '晶豪科',
    '2344': '華邦電',
}

api = DataLoader()
api.login_by_token(api_token=TOKEN)

def send_telegram(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )

def get_twse_intraday():
    """TWSE 全市場今日盤中最新資料"""
    r = requests.get(
        'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
        timeout=20, verify=False
    )
    df = pd.DataFrame(r.json())
    num_cols = ['TradeVolume','OpeningPrice','HighestPrice','LowestPrice','ClosingPrice','Change']
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['TradeVolume','ClosingPrice'])
    df['vol_k']   = df['TradeVolume'] / 1000
    df['chg_pct'] = ((df['Change'] / (df['ClosingPrice'] - df['Change'])) * 100).round(2)
    df['amp_pct'] = (((df['HighestPrice'] - df['LowestPrice']) / df['LowestPrice']) * 100).round(2)
    rng = df['HighestPrice'] - df['LowestPrice']
    df['close_pos'] = ((df['ClosingPrice'] - df['LowestPrice']) / rng.replace(0, float('nan')) * 100).round(1)
    df = df[df['Code'].str.match(r'^\d{4}$')]
    return df

def get_fi_yesterday(stock_id):
    """外資昨日買賣超（張）"""
    try:
        df = api.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=D5)
        fi = df[df['name']=='Foreign_Investor']
        last = fi.iloc[-1]
        return int((last['buy'] - last['sell']) / 1000), str(last['date'])[:10]
    except:
        return 0, ''

def get_avg5_vol(stock_id):
    """近5日均量（張）"""
    try:
        h = api.taiwan_stock_daily(stock_id=stock_id, start_date=D10)
        if len(h) >= 5:
            return round(h['Trading_Volume'].tail(5).mean() / 1000, 0)
    except:
        pass
    return 0

def vol_signal(vol_k, avg5, chg_pct, close_pos):
    """量價訊號"""
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
        fut = api.taiwan_futures_institutional_investors(futures_id="TX", start_date=D5)
        fi = fut[fut['institutional_investors']=='外資']
        last = fi.iloc[-1]
        diff = int(last['long_open_interest_balance_volume']) - int(last['short_open_interest_balance_volume'])
        return diff, str(last['date'])[:10]
    except:
        return 0, ''

# ── 主程式 ────────────────────────────────────────

df = get_twse_intraday()

# 隔日沖篩選
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
        h = api.taiwan_stock_daily(stock_id=code, start_date=D10)
        h['vol_k'] = h['Trading_Volume'] / 1000
        avg5 = round(h['vol_k'].tail(5).mean(), 0)
        last = h.iloc[-1]
        chg_pct = round((last['close'] - last['open']) / last['open'] * 100, 2)
        rng = last['max'] - last['min']
        close_pos = round((last['close'] - last['min']) / rng * 100, 1) if rng > 0 else 50
        amp_pct = round(rng / last['min'] * 100, 2) if last['min'] > 0 else 0
        sig = vol_signal(last['vol_k'], avg5, chg_pct, close_pos)
        fi_net, fi_date = get_fi_yesterday(code)
        watchlist_data.append({
            'code': code, 'name': name,
            'close': last['close'], 'chg_pct': chg_pct,
            'vol_k': int(last['vol_k']), 'avg5': int(avg5),
            'signal': sig, 'fi_net': fi_net,
        })
    except:
        pass

fut_diff, fut_date = get_futures_direction()
mkt_dir = "偏多 ↑" if fut_diff > 0 else "偏空 ↓"

# ── 組訊息 ────────────────────────────────────────

lines = [f"🌙 <b>隔日沖候選</b>｜{TODAY} 12:30\n"]

lines.append(f"🌐 大盤外資期貨（{fut_date}）：{mkt_dir}（{fut_diff:+,} 口）\n")

lines.append("📋 <b>篩選條件</b>")
lines.append("量&gt;5000張 ＋ 漲&gt;2% ＋ 收高70%+ ＋ 外資買超\n")

if candidates:
    lines.append(f"✅ 符合 {len(candidates)} 檔：\n")
    for c in candidates[:8]:  # 最多顯示8檔
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

# 自選股
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
