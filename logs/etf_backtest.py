#!/usr/bin/env python3
"""ETF 三年回測分析：0050, 0052, 00881, 006208"""

import sys
sys.path.insert(0, '/Users/steven/CCProject/finmind/venv/lib/python3.14/site-packages')

import pandas as pd
import numpy as np
from FinMind.data import DataLoader
from datetime import datetime

TOKEN = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()

api = DataLoader()
api.login_by_token(api_token=TOKEN)

etfs = {
    '0050': '元大台灣50',
    '0052': '富邦科技',
    '00881': '國泰永續高股息',
    '006208': '富邦台50',
}

START = '2022-04-04'
END = '2025-04-04'
RF_RATE = 0.015  # 無風險利率 1.5%
TRADING_DAYS = 252

results = []

for ticker, name in etfs.items():
    print(f"下載 {ticker} ({name}) 股價資料...")
    df = api.taiwan_stock_daily(
        stock_id=ticker,
        start_date=START,
        end_date=END,
    )
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date').reset_index(drop=True)

    # 嘗試取得股利資料
    print(f"下載 {ticker} 股利資料...")
    try:
        div_df = api.taiwan_stock_dividend_result(
            stock_id=ticker,
            start_date=START,
            end_date=END,
        )
        if div_df is not None and len(div_df) > 0:
            # 只取現金股利（息），排除股票股利
            cash_div = div_df[div_df['stock_or_cache_dividend'].str.contains('息', na=False)]
            total_div = cash_div['stock_and_cache_dividend'].sum()
            div_count = len(cash_div)
            print(f"  {ticker} 期間配息 {div_count} 次，總現金股利: {total_div:.2f}")
        else:
            total_div = 0
    except Exception as e:
        print(f"  股利資料取得失敗: {e}")
        total_div = 0

    # 使用收盤價
    prices = df.set_index('date')['close']

    # 日報酬率
    daily_returns = prices.pct_change().dropna()

    # 總報酬率（純股價）
    price_return = (prices.iloc[-1] / prices.iloc[0]) - 1

    # 加上股利的總報酬率
    div_return = total_div / prices.iloc[0]
    total_return = price_return + div_return

    # 實際天數計算年化
    actual_days = (prices.index[-1] - prices.index[0]).days
    years = actual_days / 365.25
    annualized_return = (1 + total_return) ** (1 / years) - 1

    # 年化波動度
    volatility = daily_returns.std() * np.sqrt(TRADING_DAYS)

    # 夏普比率
    sharpe = (annualized_return - RF_RATE) / volatility

    # 最大回撤 (Max Drawdown)
    cummax = prices.cummax()
    drawdown = (prices - cummax) / cummax
    max_dd = drawdown.min()

    # 最大回撤區間
    dd_end_idx = drawdown.idxmin()
    dd_start_idx = prices[:dd_end_idx].idxmax()
    # 恢復點
    recovery = prices[dd_end_idx:]
    recovery_idx = recovery[recovery >= prices[dd_start_idx]]
    if len(recovery_idx) > 0:
        dd_recovery = recovery_idx.index[0]
        recovery_str = dd_recovery.strftime('%Y-%m-%d')
    else:
        recovery_str = '尚未恢復'

    results.append({
        'ETF': f"{ticker} {name}",
        '起始價': prices.iloc[0],
        '結束價': prices.iloc[-1],
        '期間股利': total_div,
        '總報酬率': total_return,
        '年化報酬率': annualized_return,
        '年化波動度': volatility,
        '夏普比率': sharpe,
        '最大回撤': max_dd,
        'MDD起始': dd_start_idx.strftime('%Y-%m-%d'),
        'MDD谷底': dd_end_idx.strftime('%Y-%m-%d'),
        'MDD恢復': recovery_str,
        '資料筆數': len(prices),
        '期間(年)': round(years, 2),
    })

# 輸出結果
print("\n" + "=" * 80)
print("  台灣 ETF 三年回測分析 (2022-04-04 ~ 2025-04-04)")
print("  無風險利率: 1.5%")
print("=" * 80)

df_result = pd.DataFrame(results)

# 主要績效表
print("\n【績效比較表】")
print("-" * 80)
fmt = "{:<22s} {:>10s} {:>10s} {:>10s} {:>10s} {:>10s}"
print(fmt.format('ETF', '總報酬率', '年化報酬', '波動度', '夏普比率', '最大回撤'))
print("-" * 80)
for _, r in df_result.iterrows():
    print(fmt.format(
        r['ETF'],
        f"{r['總報酬率']:.2%}",
        f"{r['年化報酬率']:.2%}",
        f"{r['年化波動度']:.2%}",
        f"{r['夏普比率']:.2f}",
        f"{r['最大回撤']:.2%}",
    ))
print("-" * 80)

# 價格與股利
print("\n【價格與股利】")
print("-" * 80)
fmt2 = "{:<22s} {:>10s} {:>10s} {:>10s} {:>10s}"
print(fmt2.format('ETF', '起始價', '結束價', '期間股利', '資料期間'))
print("-" * 80)
for _, r in df_result.iterrows():
    print(fmt2.format(
        r['ETF'],
        f"{r['起始價']:.2f}",
        f"{r['結束價']:.2f}",
        f"{r['期間股利']:.2f}",
        f"{r['期間(年)']:.2f}年",
    ))
print("-" * 80)

# 最大回撤區間
print("\n【最大回撤區間】")
print("-" * 80)
fmt3 = "{:<22s} {:>10s} {:>12s} {:>12s} {:>12s}"
print(fmt3.format('ETF', '最大回撤', '高點日期', '谷底日期', '恢復日期'))
print("-" * 80)
for _, r in df_result.iterrows():
    print(fmt3.format(
        r['ETF'],
        f"{r['最大回撤']:.2%}",
        r['MDD起始'],
        r['MDD谷底'],
        r['MDD恢復'],
    ))
print("-" * 80)

# 排名
print("\n【排名分析】")
sorted_return = df_result.sort_values('總報酬率', ascending=False)
sorted_sharpe = df_result.sort_values('夏普比率', ascending=False)
sorted_mdd = df_result.sort_values('最大回撤', ascending=False)  # 越接近0越好
sorted_vol = df_result.sort_values('年化波動度', ascending=True)

print(f"  報酬率最高: {sorted_return.iloc[0]['ETF']} ({sorted_return.iloc[0]['總報酬率']:.2%})")
print(f"  報酬率最低: {sorted_return.iloc[-1]['ETF']} ({sorted_return.iloc[-1]['總報酬率']:.2%})")
print(f"  夏普最佳:   {sorted_sharpe.iloc[0]['ETF']} ({sorted_sharpe.iloc[0]['夏普比率']:.2f})")
print(f"  波動最低:   {sorted_vol.iloc[0]['ETF']} ({sorted_vol.iloc[0]['年化波動度']:.2%})")
print(f"  回撤最小:   {sorted_mdd.iloc[0]['ETF']} ({sorted_mdd.iloc[0]['最大回撤']:.2%})")

# 結論
print("\n【結論】")
best_return = sorted_return.iloc[0]
best_stable = sorted_sharpe.iloc[0]
print(f"  - 報酬最高: {best_return['ETF']}，三年總報酬 {best_return['總報酬率']:.2%}，年化 {best_return['年化報酬率']:.2%}")
print(f"  - 風險調整後最佳（夏普最高）: {best_stable['ETF']}，夏普比率 {best_stable['夏普比率']:.2f}")
print(f"  - 最穩健（波動最低）: {sorted_vol.iloc[0]['ETF']}，年化波動 {sorted_vol.iloc[0]['年化波動度']:.2%}")
print(f"  - 最大回撤最小: {sorted_mdd.iloc[0]['ETF']}，MDD {sorted_mdd.iloc[0]['最大回撤']:.2%}")
print("=" * 80)
