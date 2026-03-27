#!/usr/bin/env python3
"""
每日台股篩選報告
- 當沖候選：量前20 + 振幅>3% + 近5日均量>3000張 + 今日漲幅>1.5%
- 隔日沖候選：收盤收高 + 外資買超 + 突破壓力 + 借券少
- 量價訊號：判斷今日是吸貨還是出貨
- 大盤籌碼：外資期貨多空
"""

import requests
import pandas as pd
import warnings
from datetime import datetime, timedelta
from FinMind.data import DataLoader

warnings.filterwarnings('ignore')

TOKEN = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
TODAY = datetime.today().strftime('%Y-%m-%d')
D30 = (datetime.today() - timedelta(days=45)).strftime('%Y-%m-%d')
D10 = (datetime.today() - timedelta(days=15)).strftime('%Y-%m-%d')
D5  = (datetime.today() - timedelta(days=8)).strftime('%Y-%m-%d')

api = DataLoader()
api.login_by_token(api_token=TOKEN)

SEP = "=" * 58

def section(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)

def get_twse_today():
    """TWSE 全市場今日收盤資料"""
    r = requests.get(
        'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
        timeout=20, verify=False
    )
    df = pd.DataFrame(r.json())
    num_cols = ['TradeVolume','TradeValue','OpeningPrice','HighestPrice','LowestPrice','ClosingPrice','Change']
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=['TradeVolume','ClosingPrice'])
    df['vol_k'] = (df['TradeVolume'] / 1000).round(0)
    # 漲幅%
    df['chg_pct'] = ((df['Change'] / (df['ClosingPrice'] - df['Change'])) * 100).round(2)
    # 振幅%
    df['amp_pct'] = (((df['HighestPrice'] - df['LowestPrice']) / df['LowestPrice']) * 100).round(2)
    # 收盤位置（收在當日區間的比例，越高越強）
    rng = df['HighestPrice'] - df['LowestPrice']
    df['close_pos'] = ((df['ClosingPrice'] - df['LowestPrice']) / rng.replace(0, float('nan')) * 100).round(1)
    return df

def get_finmind_history(stock_id, start_date):
    """FinMind 取個股歷史股價"""
    try:
        return api.taiwan_stock_daily(stock_id=stock_id, start_date=start_date)
    except:
        return pd.DataFrame()

def get_institutional(stock_id, start_date):
    """外資買賣超"""
    try:
        df = api.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date)
        fi = df[df['name']=='Foreign_Investor'].copy()
        fi['net_k'] = ((fi['buy'] - fi['sell']) / 1000).round(0)
        return fi.sort_values('date')
    except:
        return pd.DataFrame()

def vol_signal(price_row, avg5_vol):
    """量價訊號判斷"""
    chg = price_row['chg_pct'] if 'chg_pct' in price_row else 0
    vol = price_row['vol_k']
    close_pos = price_row.get('close_pos', 50)
    vol_ratio = vol / avg5_vol if avg5_vol > 0 else 1

    if vol_ratio >= 2:
        vol_str = f"爆量({vol_ratio:.1f}倍)"
        if chg > 2 and close_pos >= 70:
            return f"{vol_str} 收高 → 強勢吸貨 ✅"
        elif chg > 0 and close_pos < 50:
            return f"{vol_str} 收低 → 出貨警訊 ⚠️"
        elif abs(chg) < 1:
            return f"{vol_str} 漲幅縮 → 多空交戰/出貨 ⚠️"
        elif chg < -2:
            return f"{vol_str} 大跌收低 → 恐慌賣壓 ❌"
        else:
            return f"{vol_str} 方向不明 🔍"
    elif vol_ratio < 0.6:
        if chg < 0:
            return f"縮量回檔 → 健康整理 ✅"
        else:
            return f"縮量上漲 → 力道偏弱 🔍"
    else:
        return f"量平穩 → 觀望"

# ── 開始執行 ──────────────────────────────────────

print(f"\n台股每日報告 ── {TODAY}")

# ═══════════════════════════════════════════════════
# 1. 大盤外資期貨多空（領先指標）
# ═══════════════════════════════════════════════════
section("1｜大盤外資台指期 多空口數（領先指標）")

fut = api.taiwan_futures_institutional_investors(futures_id="TX", start_date=D5)
fi_fut = fut[fut['institutional_investors']=='外資'].copy()
fi_fut['diff'] = fi_fut['long_open_interest_balance_volume'] - fi_fut['short_open_interest_balance_volume']

print(f"  {'日期':12} {'多單':>7} {'空單':>7} {'差額':>8}  方向")
for _, r in fi_fut.tail(5).iterrows():
    dir_str = "偏多 ↑" if r['diff'] > 0 else "偏空 ↓"
    print(f"  {str(r['date'])[:10]}  {r['long_open_interest_balance_volume']:>7,}  {r['short_open_interest_balance_volume']:>7,}  {r['diff']:>+8,}  {dir_str}")

# ═══════════════════════════════════════════════════
# 2. 當沖候選篩選
# ═══════════════════════════════════════════════════
section("2｜當沖候選（上市）")
print("  條件：量前20 ＋ 振幅>3% ＋ 近5日均量>3000張 ＋ 今日漲幅>1.5%")

twse = get_twse_today()
top20 = twse.nlargest(20, 'TradeVolume')[['Code','Name','ClosingPrice','Change','chg_pct','amp_pct','vol_k','close_pos']].copy()

# 對每一檔取近5日均量
print(f"\n  {'代號':6} {'名稱':10} {'收盤':>7} {'漲幅':>7} {'振幅':>7} {'量(張)':>8} {'近5均量':>8}  判斷")
candidates = []
for _, row in top20.iterrows():
    code = row['Code']
    # 只篩選純數字代碼（排除ETF等）
    if not str(code).isdigit() or len(str(code)) != 4:
        continue
    hist = get_finmind_history(code, D10)
    if hist.empty or len(hist) < 5:
        avg5 = row['vol_k']
    else:
        avg5 = round(hist['Trading_Volume'].tail(5).mean() / 1000, 0)

    amp_ok  = row['amp_pct'] >= 3
    avg5_ok = avg5 >= 3000
    chg_ok  = row['chg_pct'] >= 1.5

    if amp_ok and avg5_ok and chg_ok:
        verdict = "✅ 符合"
        candidates.append(code)
    else:
        miss = []
        if not amp_ok:  miss.append(f"振幅{row['amp_pct']:.1f}%")
        if not avg5_ok: miss.append(f"均量{avg5:.0f}張")
        if not chg_ok:  miss.append(f"漲幅{row['chg_pct']:.1f}%")
        verdict = "✗ " + "/".join(miss)

    print(f"  {code:6} {row['Name'][:8]:10} {row['ClosingPrice']:>7.1f} {row['chg_pct']:>+6.1f}% {row['amp_pct']:>6.1f}% {int(row['vol_k']):>8,}  {int(avg5):>7,}  {verdict}")

# ═══════════════════════════════════════════════════
# 3. 隔日沖候選篩選
# ═══════════════════════════════════════════════════
section("3｜隔日沖候選（上市）")
print("  條件：量>5000張 ＋ 今日漲幅>2% ＋ 收盤位置>70% ＋ 外資買超")

twse_active = twse[
    (twse['vol_k'] >= 5000) &
    (twse['chg_pct'] >= 2.0) &
    (twse['close_pos'] >= 70) &
    (twse['Code'].str.isdigit()) &
    (twse['Code'].str.len() == 4)
].nlargest(15, 'vol_k')

swing_candidates = []
print(f"\n  {'代號':6} {'名稱':10} {'收盤':>7} {'漲幅':>7} {'收盤位':>7} {'量(張)':>8} {'外資':>8}  訊號")

for _, row in twse_active.iterrows():
    code = row['Code']
    insti = get_institutional(code, D5)
    if insti.empty:
        fi_net = 0
    else:
        latest = insti[insti['date'] == insti['date'].max()]
        fi_net = int(latest['net_k'].sum()) if not latest.empty else 0

    fi_str = f"{fi_net:+,}張" if fi_net != 0 else "無資料"

    # 取近5日均量判斷是否爆量
    hist = get_finmind_history(code, D10)
    if not hist.empty and len(hist) >= 5:
        avg5 = hist['Trading_Volume'].tail(5).mean() / 1000
        vol_ratio = row['vol_k'] / avg5 if avg5 > 0 else 1
        vol_flag = f"爆量{vol_ratio:.1f}x" if vol_ratio >= 1.5 else "量正常"
    else:
        vol_flag = ""

    if fi_net > 0:
        signal = f"✅ 外資買{fi_net:+,}張 {vol_flag}"
        swing_candidates.append(code)
    elif fi_net == 0:
        signal = f"🔍 外資無資料 {vol_flag}"
    else:
        signal = f"⚠️ 外資賣{fi_net:,}張 {vol_flag}"

    print(f"  {code:6} {row['Name'][:8]:10} {row['ClosingPrice']:>7.1f} {row['chg_pct']:>+6.1f}% {row['close_pos']:>6.1f}% {int(row['vol_k']):>8,} {fi_str:>8}  {signal}")

# ═══════════════════════════════════════════════════
# 4. 自選股量價訊號
# ═══════════════════════════════════════════════════
section("4｜自選股量價訊號")
WATCHLIST = {'3006': '晶豪科', '2344': '華邦電'}

for code, name in WATCHLIST.items():
    hist = get_finmind_history(code, D10)
    if hist.empty:
        print(f"  {code} {name}: 無資料")
        continue

    hist['vol_k'] = hist['Trading_Volume'] / 1000
    hist['chg_pct'] = ((hist['close'] - hist['open']) / hist['open'] * 100).round(2)
    rng = hist['max'] - hist['min']
    hist['close_pos'] = ((hist['close'] - hist['min']) / rng.replace(0, float('nan')) * 100).round(1)
    hist['amp_pct'] = (rng / hist['min'] * 100).round(2)

    avg5 = hist['vol_k'].iloc[-6:-1].mean() if len(hist) >= 6 else hist['vol_k'].mean()
    last = hist.iloc[-1]

    insti = get_institutional(code, D5)
    if not insti.empty:
        fi_today = insti[insti['date'] == insti['date'].max()]['net_k'].sum()
        fi_10d   = insti['net_k'].sum()
    else:
        fi_today = fi_10d = 0

    signal = vol_signal(last, avg5)

    print(f"\n  [{code} {name}]  收盤:{last['close']:.0f}  漲跌:{last['chg_pct']:+.1f}%  振幅:{last['amp_pct']:.1f}%  量:{int(last['vol_k']):,}張（近5均{int(avg5):,}）")
    print(f"  量價訊號 → {signal}")
    print(f"  外資今日:{fi_today:+.0f}張  近10日累計:{fi_10d:+.0f}張")

# ═══════════════════════════════════════════════════
# 5. 總結
# ═══════════════════════════════════════════════════
section("5｜今日總結")

last_fut = fi_fut.iloc[-1]
mkt_dir = "偏空 ↓" if last_fut['diff'] < 0 else "偏多 ↑"
print(f"  大盤外資期貨：{mkt_dir}（多空差 {int(last_fut['diff']):+,} 口）")
print(f"  當沖候選：{len(candidates)} 檔  {candidates if candidates else '今日無符合標的'}")
print(f"  隔日沖候選：{len(swing_candidates)} 檔  {swing_candidates if swing_candidates else '今日無符合標的'}")
print(f"\n  ⚠️  本報告為數據彙整，非投資建議，買賣自負。\n")
