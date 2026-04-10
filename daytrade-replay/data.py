"""
資料層：
- top30 股票清單：TWSE API（最近15交易日前20大量）
- 1分K：yfinance（最近7天免費）
- 日K均量：yfinance 日線
"""

import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

import pandas as pd

CACHE_FILE = '/tmp/daytrade_top30_cache.json'


def _trading_days_ago(n: int) -> str:
    d = date.today()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime('%Y-%m-%d')


# ── Top30 ────────────────────────────────────────────────────────────────────

def _fetch_twse_top20(date_str: str) -> list:
    """用 TWSE API 取單日成交量前20名，回傳 [{'code','name','vol_k'}, ...]"""
    import requests
    d = date_str.replace('-', '')
    try:
        res = requests.get(
            f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX20?date={d}&type=MS&response=json',
            timeout=10, verify=False
        )
        data = res.json()
        if data.get('stat') != 'OK' or not data.get('data'):
            return []
        rows = []
        for item in data['data']:
            code = str(item[1]).strip()
            name = str(item[2]).strip()
            if not code.isdigit() or len(code) != 4:
                continue
            try:
                # item[3] = 成交量（股），item[4] = 成交筆數
                vol_k = int(str(item[3]).replace(',', '')) // 1000
            except Exception:
                vol_k = 0
            rows.append({'code': code, 'name': name, 'vol_k': vol_k})
        return rows
    except Exception as e:
        print(f'[twse] {date_str} 失敗: {e}')
        return []


def get_top30_stocks() -> list:
    """
    取最近15交易日 TWSE 成交量前20名，統計平均量取前30。
    快取到 /tmp/daytrade_top30_cache.json（當日有效）。
    """
    today_str = str(date.today())
    if os.path.exists(CACHE_FILE):
        try:
            cached = json.load(open(CACHE_FILE))
            if cached.get('date') == today_str:
                return cached['stocks']
        except Exception:
            pass

    print('[data] 取最近15交易日 TWSE top20...')
    vol_sum = defaultdict(int)
    vol_cnt = defaultdict(int)
    name_map = {}
    got = 0

    for i in range(1, 25):
        if got >= 15:
            break
        d = _trading_days_ago(i)
        rows = _fetch_twse_top20(d)
        if not rows:
            continue
        got += 1
        for r in rows:
            vol_sum[r['code']] += r['vol_k']
            vol_cnt[r['code']] += 1
            name_map[r['code']] = r['name']
        time.sleep(0.15)

    stocks_raw = []
    for code, cnt in vol_cnt.items():
        avg = vol_sum[code] // cnt
        stocks_raw.append({'code': code, 'name': name_map.get(code, code), 'avg_vol': avg})

    stocks = sorted(stocks_raw, key=lambda x: x['avg_vol'], reverse=True)[:30]

    json.dump({'date': today_str, 'stocks': stocks}, open(CACHE_FILE, 'w'), ensure_ascii=False)
    if stocks:
        print(f'[data] top30 完成，第1名: {stocks[0]["code"]} {stocks[0]["name"]} {stocks[0]["avg_vol"]:,}張')
    return stocks


# ── 1分K（yfinance）────────────────────────────────────────────────────────

def _yf_ticker(stock_id: str) -> str:
    return stock_id + '.TW'


def get_1min_kbars(stock_id: str, date_str: str) -> list:
    """
    取指定股票指定日的1分K（yfinance，最近7天）。
    時間轉為台灣時間（+8），只保留 09:01~13:30。
    回傳 [{'ts': '2026-04-01 09:01', 'open':..., 'high':..., 'low':..., 'close':..., 'volume':...}]
    volume 單位：股（整股）
    """
    cache_path = f'/tmp/kbar_{stock_id}_{date_str}.json'
    if os.path.exists(cache_path):
        return json.load(open(cache_path))

    import yfinance as yf

    ticker = _yf_ticker(stock_id)
    try:
        df = yf.download(ticker, interval='1m', period='7d', progress=False, auto_adjust=True)
    except Exception as e:
        print(f'[yf] kbar {stock_id} 失敗: {e}')
        return []

    if df.empty:
        return []

    # 多層欄位處理
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)

    # UTC → 台灣時間
    df.index = df.index.tz_convert('Asia/Taipei')

    # 只取目標日期，且限制在交易時段
    df = df[df.index.date == date.fromisoformat(date_str)]
    df = df.between_time('09:01', '13:30')

    if df.empty:
        return []

    df = df.reset_index()
    df.rename(columns={'Datetime': 'ts', 'Open': 'open', 'High': 'high',
                       'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
    df['ts'] = df['ts'].dt.strftime('%Y-%m-%d %H:%M')
    df['volume'] = df['volume'].fillna(0).astype(int)

    result = df[['ts', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    json.dump(result, open(cache_path, 'w'), ensure_ascii=False)
    return result


def get_available_dates(stock_id: str) -> list:
    """取 yfinance 最近7天有1分K資料的交易日"""
    import yfinance as yf
    ticker = _yf_ticker(stock_id)
    try:
        df = yf.download(ticker, interval='1m', period='7d', progress=False, auto_adjust=True)
        if df.empty:
            return []
        df.index = df.index.tz_convert('Asia/Taipei')
        df = df.between_time('09:01', '13:30')
        dates = sorted(set(df.index.date.astype(str).tolist()), reverse=True)
        return dates
    except Exception:
        return []


def get_avg5_and_yday(stock_id: str, date_str: str) -> tuple:
    """
    取 date_str 前5個交易日的平均量（股）和前一個交易日的總量（股）。
    用 yfinance 日線資料。
    """
    import yfinance as yf
    ticker = _yf_ticker(stock_id)
    try:
        d = date.fromisoformat(date_str)
        start = (d - timedelta(days=30)).strftime('%Y-%m-%d')
        df = yf.download(ticker, start=start, end=date_str, interval='1d',
                         progress=False, auto_adjust=True)
        if df.empty:
            return 0, 0
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        vols = df['Volume'].dropna().astype(int)
        avg5     = int(vols.tail(5).mean()) if len(vols) >= 3 else 0
        yday_vol = int(vols.iloc[-1]) if len(vols) >= 1 else 0
        return avg5, yday_vol
    except Exception as e:
        print(f'[yf] avg5 {stock_id} 失敗: {e}')
        return 0, 0
