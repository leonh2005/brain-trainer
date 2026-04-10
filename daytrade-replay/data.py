"""
FinMind 資料層：top30 股票清單、1分K、日K均量
"""

import json
import os
import time
from datetime import date, timedelta

import pandas as pd

CACHE_FILE = '/tmp/daytrade_top30_cache.json'
TOKEN_FILE = '/Users/steven/CCProject/.secrets/finmind_token.txt'


def _get_loader():
    from FinMind.data import DataLoader
    dl = DataLoader()
    token = open(TOKEN_FILE).read().strip()
    dl.login_by_token(api_token=token)
    return dl


def _trading_days_ago(n: int) -> str:
    d = date.today()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime('%Y-%m-%d')


def get_top30_stocks() -> list:
    """
    取最近15交易日平均成交量前30名（非ETF，4碼純數字）。
    結果快取到 /tmp/daytrade_top30_cache.json（當日有效）。
    回傳 [{'code': '2330', 'name': '台積電', 'avg_vol': 123456}, ...]
    """
    # 讀快取
    today_str = str(date.today())
    if os.path.exists(CACHE_FILE):
        try:
            cached = json.load(open(CACHE_FILE))
            if cached.get('date') == today_str:
                return cached['stocks']
        except Exception:
            pass

    dl = _get_loader()
    start = _trading_days_ago(20)  # 多取幾天確保有15個交易日
    end   = _trading_days_ago(1)

    print(f'[data] 取 top30 日K：{start} ~ {end}')
    df = dl.taiwan_stock_daily(start_date=start, end_date=end)

    # 只保留4碼純數字（排除ETF）
    df = df[df['stock_id'].str.match(r'^\d{4}$')]
    df['Trading_Volume'] = pd.to_numeric(df['Trading_Volume'], errors='coerce').fillna(0)

    # 取近15個交易日的資料
    dates = sorted(df['date'].unique())[-15:]
    df15 = df[df['date'].isin(dates)]

    # 計算各股平均成交量（張）
    avg_vol = (
        df15.groupby('stock_id')['Trading_Volume']
        .mean()
        .div(1000)
        .round(0)
        .astype(int)
        .reset_index()
        .rename(columns={'stock_id': 'code', 'Trading_Volume': 'avg_vol'})
    )
    avg_vol = avg_vol.sort_values('avg_vol', ascending=False).head(30)

    # 取股票名稱
    name_map = df[['stock_id', 'stock_name']].drop_duplicates('stock_id').set_index('stock_id')['stock_name'].to_dict()
    stocks = []
    for _, row in avg_vol.iterrows():
        stocks.append({
            'code':    row['code'],
            'name':    name_map.get(row['code'], row['code']),
            'avg_vol': int(row['avg_vol']),
        })

    # 寫快取
    json.dump({'date': today_str, 'stocks': stocks}, open(CACHE_FILE, 'w'), ensure_ascii=False)
    print(f'[data] top30 完成，第1名: {stocks[0]["code"]} {stocks[0]["name"]} {stocks[0]["avg_vol"]:,}張')
    return stocks


def get_1min_kbars(stock_id: str, date_str: str) -> list:
    """
    取指定股票指定日的1分K。
    回傳 [{'ts': '2026-04-01 09:01:00', 'open':..., 'high':..., 'low':..., 'close':..., 'volume':...}, ...]
    """
    cache_path = f'/tmp/kbar_{stock_id}_{date_str}.json'
    if os.path.exists(cache_path):
        return json.load(open(cache_path))

    dl = _get_loader()
    time.sleep(0.3)
    try:
        df = dl.taiwan_stock_kbar(stock_id=stock_id, date=date_str)
    except Exception as e:
        print(f'[data] kbar {stock_id} {date_str} 失敗: {e}')
        return []

    if df is None or df.empty:
        return []

    # 合併 date + minute → ts
    df['ts'] = df['date'] + ' ' + df['minute'] + ':00'
    df = df.sort_values('ts')

    bars = df[['ts', 'open', 'high', 'low', 'close', 'volume']].copy()
    bars['volume'] = bars['volume'].fillna(0).astype(int) // 1000  # 股→張

    result = bars.to_dict('records')
    json.dump(result, open(cache_path, 'w'), ensure_ascii=False)
    return result


def get_avg5_and_yday(stock_id: str, date_str: str) -> tuple:
    """
    取 date_str 前5個交易日的平均量（張）和前一個交易日的總量（張）。
    回傳 (avg5, yday_vol)
    """
    dl = _get_loader()
    time.sleep(0.3)

    # 取往前推20天的日K
    d = date.fromisoformat(date_str)
    start = (d - timedelta(days=30)).strftime('%Y-%m-%d')
    end   = (d - timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start, end_date=end)
    except Exception as e:
        print(f'[data] daily {stock_id} 失敗: {e}')
        return 0, 0

    if df is None or df.empty:
        return 0, 0

    df = df.sort_values('date')
    df['vol_k'] = pd.to_numeric(df['Trading_Volume'], errors='coerce').fillna(0).astype(int) // 1000

    last5 = df['vol_k'].tail(5)
    avg5 = int(last5.mean()) if len(last5) >= 3 else 0
    yday_vol = int(df['vol_k'].iloc[-1]) if len(df) >= 1 else 0

    return avg5, yday_vol


def get_available_dates(stock_id: str, n: int = 15) -> list:
    """取最近 n 個有資料的交易日（用於日期選擇器）"""
    dl = _get_loader()
    start = _trading_days_ago(n + 5)
    end   = _trading_days_ago(1)
    try:
        df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start, end_date=end)
        if df is None or df.empty:
            return []
        return sorted(df['date'].unique().tolist())[-n:]
    except Exception:
        return []
