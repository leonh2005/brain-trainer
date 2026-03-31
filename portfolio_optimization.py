import yfinance as yf
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from datetime import datetime, timedelta

# ── 參數設定 ──
tickers = {
    '2408.TW': '南亞科',
    '1303.TW': '南亞',
    '2481.TW': '強茂',
    '2301.TW': '光寶科',
    '3105.TWO': '穩懋',
    '1802.TW': '台玻',
}
risk_free_rate = 0.015  # 台灣無風險利率約 1.5%
end_date = datetime(2026, 3, 31)
start_date = end_date - timedelta(days=730)

# ── 抓取資料 ──
print("正在下載股價資料...")
data = yf.download(list(tickers.keys()), start=start_date, end=end_date, auto_adjust=True)

# 處理 MultiIndex columns
if isinstance(data.columns, pd.MultiIndex):
    close = data['Close']
else:
    close = data

close = close.dropna()
print(f"資料期間: {close.index[0].strftime('%Y-%m-%d')} ~ {close.index[-1].strftime('%Y-%m-%d')}")
print(f"交易日數: {len(close)}")

# ── 計算報酬率 ──
returns = close.pct_change().dropna()
n_assets = len(tickers)

# 年化參數（台股約 245 交易日）
trading_days = 245
mean_returns = returns.mean() * trading_days
cov_matrix = returns.cov() * trading_days

# ── 個股統計 ──
print("\n" + "="*70)
print("個股年化統計")
print("="*70)
print(f"{'股票':<10} {'代號':<10} {'年化報酬率':>12} {'年化波動率':>12} {'夏普比率':>10}")
print("-"*70)
for t in tickers:
    r = mean_returns[t]
    v = np.sqrt(cov_matrix.loc[t, t])
    sr = (r - risk_free_rate) / v
    print(f"{tickers[t]:<10} {t:<10} {r:>11.2%} {v:>11.2%} {sr:>10.3f}")

# ── 相關係數矩陣 ──
corr = returns.corr()
print("\n" + "="*70)
print("相關係數矩陣")
print("="*70)
labels = [tickers[t] for t in corr.columns]
header = f"{'':>8}" + "".join(f"{l:>8}" for l in labels)
print(header)
for i, t in enumerate(corr.columns):
    row = f"{labels[i]:>8}"
    for j, t2 in enumerate(corr.columns):
        row += f"{corr.iloc[i,j]:>8.3f}"
    print(row)

# ── 投資組合優化函數 ──
def portfolio_stats(weights, mean_returns, cov_matrix):
    port_return = np.dot(weights, mean_returns)
    port_vol = np.sqrt(np.dot(weights.T, np.dot(cov_matrix.values, weights)))
    sharpe = (port_return - risk_free_rate) / port_vol
    return port_return, port_vol, sharpe

def neg_sharpe(weights, mean_returns, cov_matrix):
    return -portfolio_stats(weights, mean_returns, cov_matrix)[2]

def portfolio_vol(weights, mean_returns, cov_matrix):
    return portfolio_stats(weights, mean_returns, cov_matrix)[1]

constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
bounds = tuple((0, 1) for _ in range(n_assets))
init_weights = np.array([1/n_assets] * n_assets)

# ── 最大夏普比率組合 ──
result_sharpe = minimize(neg_sharpe, init_weights,
                         args=(mean_returns, cov_matrix),
                         method='SLSQP', bounds=bounds, constraints=constraints,
                         options={'maxiter': 1000, 'ftol': 1e-12})

# ── 最小波動率組合 ──
result_minvol = minimize(portfolio_vol, init_weights,
                         args=(mean_returns, cov_matrix),
                         method='SLSQP', bounds=bounds, constraints=constraints,
                         options={'maxiter': 1000, 'ftol': 1e-12})

# ── 輸出結果 ──
def print_portfolio(name, weights, mean_returns, cov_matrix):
    ret, vol, sr = portfolio_stats(weights, mean_returns, cov_matrix)
    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"{'='*70}")
    print(f"  預期年化報酬率: {ret:.2%}")
    print(f"  年化波動率:     {vol:.2%}")
    print(f"  夏普比率:       {sr:.3f}")
    print(f"  無風險利率:     {risk_free_rate:.2%}")
    print(f"\n  {'股票':<10} {'代號':<10} {'權重':>10}")
    print(f"  {'-'*30}")
    ticker_list = list(tickers.keys())
    for i, t in enumerate(ticker_list):
        w = weights[i]
        if w > 0.001:
            print(f"  {tickers[t]:<10} {t:<10} {w:>9.1%}")
        else:
            print(f"  {tickers[t]:<10} {t:<10} {'--':>10}")

print_portfolio("最大夏普比率組合 (Max Sharpe Ratio)", result_sharpe.x, mean_returns, cov_matrix)
print_portfolio("最小波動率組合 (Minimum Volatility)", result_minvol.x, mean_returns, cov_matrix)

# ── 效率前緣 ──
print(f"\n{'='*70}")
print("效率前緣取樣（10 個點）")
print(f"{'='*70}")
target_returns = np.linspace(mean_returns.min(), mean_returns.max(), 10)
print(f"{'目標報酬':>10} {'波動率':>10} {'夏普比率':>10}")
print("-"*35)
for target in target_returns:
    cons = (
        {'type': 'eq', 'fun': lambda x: np.sum(x) - 1},
        {'type': 'eq', 'fun': lambda x, t=target: np.dot(x, mean_returns) - t},
    )
    res = minimize(portfolio_vol, init_weights,
                   args=(mean_returns, cov_matrix),
                   method='SLSQP', bounds=bounds, constraints=cons,
                   options={'maxiter': 1000, 'ftol': 1e-12})
    if res.success:
        r, v, s = portfolio_stats(res.x, mean_returns, cov_matrix)
        print(f"{r:>9.2%} {v:>9.2%} {s:>10.3f}")

print("\n分析完成。")
