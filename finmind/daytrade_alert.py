#!/usr/bin/env python3
"""
當沖候選推播 — 週間 09:30 執行
資料來源：TWSE 公開 API（昨日收盤，盤前最適合）+ 永豐金 Shioaji（K棒均量）+ FinMind（期貨法人）
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
D5  = trading_days_ago(7)


# FinMind（期貨法人）
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
    """Shioaji 歷史日K，只取量"""
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
        daily = df.groupby('_d').agg(volume=('Volume', 'sum')).reset_index()
        daily['volume'] = daily['volume'] / 1000  # 股→張
        return daily.sort_values('_d').reset_index(drop=True)
    except Exception as e:
        print(f'[sj] kbars {symbol} 失敗: {e}')
        return pd.DataFrame()


def send_telegram(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )


def get_twse_yesterday():
    """TWSE 全市場昨日收盤（盤前使用）"""
    r = requests.get(
        'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
        timeout=20, verify=False
    )
    df = pd.DataFrame(r.json())
    num_cols = ['TradeVolume','OpeningPrice','HighestPrice','LowestPrice','ClosingPrice','Change']
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['TradeVolume','ClosingPrice'])
    df['vol_k']    = df['TradeVolume'] / 1000
    df['chg_pct']  = ((df['Change'] / (df['ClosingPrice'] - df['Change'])) * 100).round(2)
    df['amp_pct']  = (((df['HighestPrice'] - df['LowestPrice']) / df['LowestPrice']) * 100).round(2)
    rng = df['HighestPrice'] - df['LowestPrice']
    df['close_pos'] = ((df['ClosingPrice'] - df['LowestPrice']) / rng.replace(0, float('nan')) * 100).round(1)
    df = df[df['Code'].str.match(r'^\d{4}$')]
    return df


def get_avg5_vol(stock_id):
    """近5日均量（張）—— Shioaji kbars；失敗時 fallback FinMind"""
    df = get_sj_daily_kbars(stock_id, days=20)
    if len(df) >= 5:
        return round(df['volume'].tail(5).mean(), 0)
    # Fallback: FinMind
    try:
        D10 = trading_days_ago(15)
        h = finmind.taiwan_stock_daily(stock_id=stock_id, start_date=D10)
        if len(h) >= 5:
            return round(h['Trading_Volume'].tail(5).mean() / 1000, 0)
    except Exception:
        pass
    return 0


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

df = get_twse_yesterday()
top20 = df.nlargest(20, 'TradeVolume')

candidates = []
for _, row in top20.iterrows():
    code = row['Code']
    avg5 = get_avg5_vol(code)

    if row['amp_pct'] >= 3 and avg5 >= 3000 and row['chg_pct'] >= 1.5:
        candidates.append({
            'code':      code,
            'name':      row['Name'],
            'close':     row['ClosingPrice'],
            'chg_pct':   row['chg_pct'],
            'amp_pct':   row['amp_pct'],
            'vol_k':     int(row['vol_k']),
            'avg5':      int(avg5),
            'close_pos': row['close_pos'],
        })

fut_diff, fut_date = get_futures_direction()
mkt_dir = "偏多 ↑" if fut_diff > 0 else "偏空 ↓"

# ── 組訊息 ────────────────────────────────────────

lines = [f"📊 <b>當沖候選</b>｜{TODAY} 09:30\n"]
lines.append(f"🌐 大盤外資期貨（{fut_date}）：{mkt_dir}（多空差 {fut_diff:+,} 口）\n")
lines.append("📋 <b>篩選條件</b>")
lines.append("量前20 ＋ 振幅&gt;3% ＋ 近5均量&gt;3000張 ＋ 漲幅&gt;1.5%\n")

if candidates:
    lines.append(f"✅ 符合 {len(candidates)} 檔：\n")
    for c in candidates:
        pos_emoji = "🔴" if c['close_pos'] >= 80 else "🟡" if c['close_pos'] >= 50 else "🟢"
        lines.append(
            f"{pos_emoji} <b>{c['code']} {c['name']}</b>\n"
            f"   收:{c['close']:.1f}  漲:{c['chg_pct']:+.1f}%  振:{c['amp_pct']:.1f}%\n"
            f"   量:{c['vol_k']:,}張  近5均:{c['avg5']:,}張  收盤位:{c['close_pos']:.0f}%\n"
        )
    lines.append("⚡ 進場參考：開盤後5~15分鐘確認方向再進")
    lines.append("🛑 停損：跌破進場價 -1.5% 出清")
else:
    lines.append("❌ 今日無符合當沖條件標的\n建議觀望或等待盤中突破訊號")

lines.append("\n⚠️ 數據參考，非投資建議，買賣自負")

msg = "\n".join(lines)
send_telegram(msg)
print(msg)
