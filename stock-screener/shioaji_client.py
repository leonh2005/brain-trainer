"""
資料來源：
  - 股票清單：TWSE/TPEX API（輕量）
  - 即時快照 / K棒：永豐 Shioaji API
"""
import shioaji as sj
import pandas as pd
import requests
import warnings
import time
import os
from datetime import date, timedelta

warnings.filterwarnings('ignore')

_api = None
_stock_list = []   # [{symbol, name, market}]
_stock_list_ts = 0

# ── Shioaji 連線（singleton）────────────────────────────────────
def _get_api():
    global _api
    if _api is None:
        _api = sj.Shioaji(simulation=False)
        _api.login(
            api_key=os.environ['API_KEY'],
            secret_key=os.environ['SECRET_KEY'],
        )
        print('[sj] 登入成功')
    return _api


# ── 股票清單（TWSE API，快取 1 小時）────────────────────────────
def _get_stock_list():
    global _stock_list, _stock_list_ts
    if _stock_list and (time.time() - _stock_list_ts) < 3600:
        return _stock_list

    stocks = []
    # TSE 上市
    try:
        r = requests.get(
            'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
            timeout=15, verify=False, headers={'User-Agent': 'Mozilla/5.0'}
        )
        for s in r.json():
            code = s.get('Code', '').strip()
            vol  = float(str(s.get('TradeVolume', '0')).replace(',', '') or 0)
            if code and code.isdigit() and len(code) == 4 and vol > 5_000_000:
                stocks.append({'symbol': code, 'name': s.get('Name', ''), 'market': 'tse'})
    except Exception as e:
        print(f'[data] TSE 清單失敗: {e}')

    # OTC 上櫃
    try:
        r = requests.get(
            'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',
            timeout=15, verify=False, headers={'User-Agent': 'Mozilla/5.0'}
        )
        for s in r.json():
            code = s.get('SecuritiesCompanyCode', '').strip()
            vol  = float(str(s.get('Volume', '0')).replace(',', '') or 0)
            if code and code.isdigit() and len(code) == 4 and vol > 5_000:
                stocks.append({'symbol': code, 'name': s.get('CompanyName', ''), 'market': 'otc'})
    except Exception as e:
        print(f'[data] OTC 清單失敗: {e}')

    _stock_list = stocks
    _stock_list_ts = time.time()
    print(f'[data] 股票清單：{len(stocks)} 檔')
    return stocks


# ── 全市場即時快照（雷達用）────────────────────────────────────
def get_snapshot_all() -> list:
    api = _get_api()
    stocks = _get_stock_list()
    if not stocks:
        return []

    result = []
    batch_size = 100

    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i + batch_size]
        try:
            contracts = [api.Contracts.Stocks[s['symbol']] for s in batch]
            contracts = [c for c in contracts if c is not None]
            if not contracts:
                continue
            snaps = api.snapshots(contracts)
            name_map = {s['symbol']: s['name'] for s in batch}
            for snap in snaps:
                if snap.close > 0:
                    result.append({
                        'symbol':        snap.code,
                        'name':          name_map.get(snap.code, ''),
                        'closePrice':    snap.close,
                        'changePercent': round(snap.change_rate, 2),
                        'tradeVolume':   snap.total_volume / 1000,
                    })
        except Exception as e:
            print(f'[sj] snapshot batch {i} 失敗: {e}')

    return result


# ── 歷史日K ─────────────────────────────────────────────────────
def get_daily_candles(symbol: str, days: int = 90) -> pd.DataFrame:
    api = _get_api()
    try:
        contract = api.Contracts.Stocks[symbol]
        if contract is None:
            raise ValueError('contract not found')
        start = str(date.today() - timedelta(days=days))
        end   = str(date.today())
        kb = api.kbars(contract, start=start, end=end)
        df = pd.DataFrame({**kb})
        if df.empty:
            return pd.DataFrame()
        df['ts'] = pd.to_datetime(df['ts'])
        df['date'] = df['ts'].dt.date
        daily = df.groupby('date').agg(
            open=('Open', 'first'),
            high=('High', 'max'),
            low=('Low', 'min'),
            close=('Close', 'last'),
            volume=('Volume', 'sum'),
        ).reset_index()
        daily['date'] = pd.to_datetime(daily['date'])
        daily['volume'] = daily['volume'] / 1000  # 股 → 張
        return daily.sort_values('date').reset_index(drop=True)
    except Exception as e:
        print(f'[sj] 日K {symbol} 失敗: {e}')
        return pd.DataFrame()


# ── 盤中 1 分K ─────────────────────────────────────────────────
def get_intraday_candles(symbol: str) -> pd.DataFrame:
    api = _get_api()
    try:
        contract = api.Contracts.Stocks[symbol]
        if contract is None:
            raise ValueError('contract not found')
        today = str(date.today())
        kb = api.kbars(contract, start=today, end=today)
        df = pd.DataFrame({**kb})
        if df.empty:
            return pd.DataFrame()
        df['ts'] = pd.to_datetime(df['ts'])
        df = df.rename(columns={
            'ts': 'datetime', 'Open': 'open', 'High': 'high',
            'Low': 'low', 'Close': 'close', 'Volume': 'volume'
        })
        df['volume'] = df['volume'] / 1000
        df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].dropna()
        df = df.sort_values('datetime').reset_index(drop=True)
        # 只保留台股交易時段 09:00–13:30（Shioaji 時間戳為台灣本地時間）
        market_open  = pd.Timestamp(today + ' 09:00:00')
        market_close = pd.Timestamp(today + ' 13:30:00')
        df = df[(df['datetime'] >= market_open) & (df['datetime'] <= market_close)]
        return df.reset_index(drop=True)
    except Exception as e:
        print(f'[sj] 分K {symbol} 失敗: {e}')
        return pd.DataFrame()
