"""
資料來源：
  - 雷達（即時量）：TWSE mis 盤中即時 API
  - 歷史日K / 分K：yfinance
無需 API Key，完全免費
"""
import requests
import pandas as pd
import yfinance as yf
import time
import warnings
warnings.filterwarnings('ignore')

MIS_HEADERS = {
    'User-Agent': 'Mozilla/5.0',
    'Referer': 'https://mis.twse.com.tw',
}

# ── 股票清單快取（TSE+OTC，每小時重建一次）──────────────────────
_stock_list = []       # [{'symbol','name','market'}]  market='tse'|'otc'
_stock_list_ts = 0

def _build_stock_list():
    """從 TWSE/TPEX 抓股票清單，篩出有成交量的標的"""
    global _stock_list, _stock_list_ts
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
            if code and vol > 5_000_000:   # 前日量 > 5千張（5千張*1000股）才列入
                stocks.append({'symbol': code, 'name': s.get('Name',''), 'market': 'tse'})
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
            if code and vol > 5_000:       # OTC 量單位不同，>5千
                stocks.append({'symbol': code, 'name': s.get('CompanyName',''), 'market': 'otc'})
    except Exception as e:
        print(f'[data] OTC 清單失敗: {e}')

    _stock_list = stocks
    _stock_list_ts = time.time()
    print(f'[data] 股票清單建立完成：{len(stocks)} 檔')
    return stocks


def _get_stock_list():
    """取得股票清單（快取 1 小時）"""
    if not _stock_list or (time.time() - _stock_list_ts) > 3600:
        _build_stock_list()
    return _stock_list


# ── 全市場即時快照（雷達用）──────────────────────────────────────
def get_snapshot_all() -> list:
    """
    使用 TWSE mis 即時 API 取得今日盤中成交量
    v 欄位 = 累計成交千股 = 張（直接使用）
    """
    stocks = _get_stock_list()
    if not stocks:
        return []

    result = []
    batch_size = 50

    for i in range(0, len(stocks), batch_size):
        batch = stocks[i:i + batch_size]
        ex_ch = '|'.join(f"{s['market']}_{s['symbol']}.tw" for s in batch)
        try:
            r = requests.get(
                f'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch={ex_ch}',
                headers=MIS_HEADERS, timeout=10, verify=False
            )
            name_map = {s['symbol']: s['name'] for s in batch}
            for item in r.json().get('msgArray', []):
                code = item.get('c', '')
                vol_str = item.get('v', '0') or '0'
                price_str = item.get('z', '') or item.get('y', '0') or '0'
                ref_str  = item.get('y', '0') or '0'   # 昨收（參考價）
                try:
                    vol   = float(vol_str.replace(',', ''))
                    price = float(price_str.replace(',', '')) if price_str not in ('-', '') else 0
                    ref   = float(ref_str.replace(',', ''))
                    chg_pct = ((price - ref) / ref * 100) if ref > 0 and price > 0 else 0
                    if vol > 0:
                        result.append({
                            'symbol':        code,
                            'name':          name_map.get(code, item.get('n', '')),
                            'closePrice':    price,
                            'changePercent': round(chg_pct, 2),
                            'tradeVolume':   vol,  # 已是張，不需轉換
                        })
                except Exception:
                    pass
        except Exception as e:
            print(f'[data] mis batch {i} 失敗: {e}')

    return result


# ── 歷史日K ─────────────────────────────────────────────────────
def get_daily_candles(symbol: str, days: int = 90) -> pd.DataFrame:
    for suffix in ['.TW', '.TWO']:
        try:
            tk = yf.Ticker(symbol + suffix)
            df = tk.history(period=f'{days}d', interval='1d', auto_adjust=True)
            if df.empty:
                continue
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
            df['volume'] = df['volume'] / 1000
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']].dropna()
            return df.sort_values('date').reset_index(drop=True)
        except Exception:
            pass
    print(f'[data] 無法取得 {symbol} 日K')
    return pd.DataFrame()


# ── 盤中 1 分K ────────────────────────────────────────────────
def get_intraday_candles(symbol: str) -> pd.DataFrame:
    for suffix in ['.TW', '.TWO']:
        try:
            tk = yf.Ticker(symbol + suffix)
            df = tk.history(period='1d', interval='1m', auto_adjust=True)
            if df.empty:
                continue
            df = df.reset_index()
            df.columns = [c.lower() for c in df.columns]
            time_col = next((c for c in df.columns if 'datetime' in c or 'date' in c), None)
            if time_col:
                df = df.rename(columns={time_col: 'datetime'})
            df['datetime'] = pd.to_datetime(df['datetime']).dt.tz_localize(None)
            df['volume'] = df['volume'] / 1000
            df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].dropna()
            return df.sort_values('datetime').reset_index(drop=True)
        except Exception:
            pass
    print(f'[data] 無法取得 {symbol} 分K')
    return pd.DataFrame()
