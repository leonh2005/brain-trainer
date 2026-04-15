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


# ── 1分K（Shioaji 優先，yfinance fallback）──────────────────────────────────

def _yf_ticker(stock_id: str) -> str:
    return stock_id + '.TW'


def _sj_stock_1min(stock_id: str, date_str: str) -> list:
    """Shioaji 取個股 1分K。今日不快取（盤中資料持續更新）。"""
    today_str = str(date.today())
    cache_path = f'/tmp/sj_kbar_{stock_id}_{date_str}.json'
    if date_str != today_str and os.path.exists(cache_path):
        return json.load(open(cache_path))
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks[stock_id]
        if contract is None:
            return []
        kb = api.kbars(contract, start=date_str, end=date_str)
        df = pd.DataFrame({**kb})
        if df.empty:
            return []
        df['ts'] = pd.to_datetime(df['ts'])
        # Shioaji 時戳為收盤時間（end-time），往前移 1 分鐘轉為開盤時間（start-time）
        df['ts'] = df['ts'] - pd.Timedelta(minutes=1)
        t_open  = pd.Timestamp('09:00').time()
        t_close = pd.Timestamp('13:29').time()
        df = df[(df['ts'].dt.time >= t_open) & (df['ts'].dt.time <= t_close)]
        if df.empty:
            return []
        result = [{'ts':     row['ts'].strftime('%Y-%m-%d %H:%M'),
                   'open':   round(float(row['Open']),   2),
                   'high':   round(float(row['High']),   2),
                   'low':    round(float(row['Low']),    2),
                   'close':  round(float(row['Close']),  2),
                   'volume': int(row['Volume'])} for _, row in df.iterrows()]
        if date_str != today_str:
            json.dump(result, open(cache_path, 'w'), ensure_ascii=False)
        return result
    except Exception as e:
        print(f'[sj] stock kbar {stock_id} {date_str} 失敗: {e}')
        return []


def get_1min_kbars(stock_id: str, date_str: str) -> list:
    """
    取指定股票指定日的1分K（Shioaji 優先，失敗 fallback yfinance）。
    只保留 09:01~13:30，volume 單位：股。
    今日資料不快取（持續更新）。
    """
    bars = _sj_stock_1min(stock_id, date_str)
    if bars:
        return bars

    # Fallback: yfinance（最近7天）
    today_str_yf = str(date.today())
    cache_path = f'/tmp/kbar_{stock_id}_{date_str}.json'
    if date_str != today_str_yf and os.path.exists(cache_path):
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
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_convert('Asia/Taipei')
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
    if date_str != today_str_yf:
        json.dump(result, open(cache_path, 'w'), ensure_ascii=False)
    return result


def get_available_dates(stock_id: str) -> list:
    """
    列出最近 10 個交易日（週一到週五）直接回傳，不預先驗資料。
    今日（若是交易日）必然列在最前面。
    K 棒實際有無由 get_1min_kbars() 處理。
    """
    today = date.today()
    dates = []
    for i in range(0, 30):
        if len(dates) >= 10:
            break
        d = today - timedelta(days=i)
        if d.weekday() < 5:          # 只列交易日（週一到週五）
            dates.append(str(d))
    return dates


def get_daily_stats(stock_id: str, date_str: str) -> tuple:
    """
    取 date_str 前一個交易日的量/高/低/收，以及前5日均量。
    回傳 (avg5, yday_vol, yday_high, yday_low, yday_close)
    """
    import yfinance as yf
    ticker = _yf_ticker(stock_id)
    try:
        d = date.fromisoformat(date_str)
        start = (d - timedelta(days=30)).strftime('%Y-%m-%d')
        df = yf.download(ticker, start=start, end=date_str, interval='1d',
                         progress=False, auto_adjust=True)
        if df.empty:
            return 0, 0, 0, 0, 0
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        vols  = df['Volume'].dropna().astype(int)
        avg5      = int(vols.tail(5).mean()) if len(vols) >= 3 else 0
        yday_vol  = int(vols.iloc[-1])              if len(vols) >= 1 else 0
        yday_high  = round(float(df['High'].iloc[-1]),  2) if len(df) >= 1 else 0
        yday_low   = round(float(df['Low'].iloc[-1]),   2) if len(df) >= 1 else 0
        yday_close = round(float(df['Close'].iloc[-1]), 2) if len(df) >= 1 else 0
        return avg5, yday_vol, yday_high, yday_low, yday_close
    except Exception as e:
        print(f'[yf] daily_stats {stock_id} 失敗: {e}')
        return 0, 0, 0, 0, 0


# 向後相容別名
def get_avg5_and_yday(stock_id: str, date_str: str) -> tuple:
    avg5, yday_vol, *_ = get_daily_stats(stock_id, date_str)
    return avg5, yday_vol


# ── Shioaji singleton（指數用）───────────────────────────────────────────────

_sj_api = None

def _get_sj():
    global _sj_api
    import shioaji as sj
    from dotenv import load_dotenv

    def _login():
        load_dotenv(os.path.join(os.path.dirname(__file__), '../finmind/.env'))
        api = sj.Shioaji(simulation=False)
        api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        print('[sj] 永豐 API 登入成功')
        return api

    if _sj_api is None:
        _sj_api = _login()
        return _sj_api

    # 每次使用前用 heartbeat 確認 session，斷線則重連
    try:
        _sj_api.Contracts.Indexs.TSE['001']  # 輕量測試
    except Exception:
        print('[sj] session 失效，重新登入...')
        try:
            _sj_api.logout()
        except Exception:
            pass
        _sj_api = _login()

    return _sj_api


# 產業別 → TSE 指數代碼
_INDUSTRY_TSE = {
    '半導體業':         '036',
    '電腦及週邊設備業': '037',
    '光電業':           '038',
    '通信網路業':       '039',
    '電子零組件業':     '040',
    '電子通路業':       '041',
    '資訊服務業':       '042',
    '其他電子業':       '043',
    '電子工業':         '027',
    '金融保險業':       '031',
    '金融保險':         '031',
    '建材營造業':       '028',
    '建材營造':         '028',
    '食品工業':         '016',
    '鋼鐵工業':         '024',
    '航運業':           '029',
    '生技醫療業':       '005',
    '化學工業':         '018',
    '塑膠工業':         '017',
    '紡織纖維':         '019',
    '汽車工業':         '025',
    '油電燃氣業':       '033',
    '觀光餐旅':         '032',
    '貿易百貨業':       '030',
}


def _industry_to_tse_code(industry: str) -> str:
    """模糊比對產業名稱 → TSE 指數代碼"""
    if not industry:
        return ''
    # 精確比對
    if industry in _INDUSTRY_TSE:
        return _INDUSTRY_TSE[industry]
    # 包含比對
    for key, code in _INDUSTRY_TSE.items():
        if industry in key or key in industry:
            return code
    # 前兩字比對
    for key, code in _INDUSTRY_TSE.items():
        if industry[:2] in key:
            return code
    return ''


def _sj_index_1min(tse_code: str, date_str: str) -> list:
    """
    Shioaji 取 TSE 指數 1分K。
    Shioaji 時戳已是台灣本地時間，不做 tz 轉換。
    回傳 [{'ts': 'YYYY-MM-DD HH:MM', 'close': float}]
    """
    today_str = str(date.today())
    cache_path = f'/tmp/sj_idx_{tse_code}_{date_str}.json'
    if date_str != today_str and os.path.exists(cache_path):
        return json.load(open(cache_path))
    try:
        api = _get_sj()
        contract = api.Contracts.Indexs.TSE[tse_code]
        kb = api.kbars(contract, start=date_str, end=date_str)
        df = pd.DataFrame({**kb})
        if df.empty:
            return []
        df['ts'] = pd.to_datetime(df['ts'])
        # Shioaji 時戳為收盤時間，往前移 1 分鐘轉為開盤時間
        df['ts'] = df['ts'] - pd.Timedelta(minutes=1)
        t_open  = pd.Timestamp('09:00').time()
        t_close = pd.Timestamp('13:29').time()
        df = df[(df['ts'].dt.time >= t_open) & (df['ts'].dt.time <= t_close)]
        result = [{'ts': row['ts'].strftime('%Y-%m-%d %H:%M'), 'close': round(float(row['Close']), 2)}
                  for _, row in df.iterrows()]
        json.dump(result, open(cache_path, 'w'))
        return result
    except Exception as e:
        print(f'[sj] index {tse_code} {date_str} 失敗: {e}')
        return []


# ── TAIEX 1分K ───────────────────────────────────────────────────────────────

def get_taiex_1min(date_str: str) -> list:
    """取大盤指數 1分K（Shioaji TSE001），失敗時 fallback yfinance"""
    # 先試 Shioaji
    bars = _sj_index_1min('001', date_str)
    if bars:
        return bars
    # Fallback: yfinance
    today_str_tx = str(date.today())
    cache_path = f'/tmp/taiex_1min_{date_str}.json'
    if date_str != today_str_tx and os.path.exists(cache_path):
        return json.load(open(cache_path))
    import yfinance as yf
    try:
        df = yf.download('^TWII', interval='1m', period='7d', progress=False, auto_adjust=True)
        if df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        df.index = df.index.tz_convert('Asia/Taipei')
        df_day = df[df.index.date == date.fromisoformat(date_str)]
        df_day = df_day.between_time('09:01', '13:30')
        if df_day.empty:
            return []
        df_day = df_day.reset_index()
        df_day.rename(columns={'Datetime': 'ts', 'Close': 'close'}, inplace=True)
        df_day['ts'] = df_day['ts'].dt.strftime('%Y-%m-%d %H:%M')
        result = df_day[['ts', 'close']].round({'close': 2}).to_dict('records')
        if date_str != today_str_tx:
            json.dump(result, open(cache_path, 'w'))
        return result
    except Exception as e:
        print(f'[taiex] yfinance fallback 失敗: {e}')
        return []


# ── 股票產業別 ────────────────────────────────────────────────────────────────

def get_stock_sector(stock_id: str) -> str:
    """從 FinMind TaiwanStockInfo 取股票產業類別"""
    cache_path = f'/tmp/stock_sector_{stock_id}.json'
    if os.path.exists(cache_path):
        return json.load(open(cache_path)).get('industry', '')

    try:
        import requests as _req
        token = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
        res = _req.get(
            'https://api.finmindtrade.com/api/v4/data',
            params={'dataset': 'TaiwanStockInfo', 'data_id': stock_id, 'token': token},
            timeout=10,
        )
        rows = res.json().get('data', [])
        industry = rows[0].get('industry_category', '') if rows else ''
        json.dump({'industry': industry}, open(cache_path, 'w'), ensure_ascii=False)
        return industry
    except Exception as e:
        print(f'[finmind] sector {stock_id} 失敗: {e}')
        return ''


# ── 產業指數 1分K ─────────────────────────────────────────────────────────────

def get_sector_1min(industry: str, date_str: str) -> dict:
    """
    取產業指數 1分K（Shioaji），回傳 {'name': '半導體業', 'bars': [{ts, close}, ...]}
    找不到對應代碼或 Shioaji 無資料時回傳 {}
    """
    if not industry:
        return {}
    code = _industry_to_tse_code(industry)
    if not code:
        return {}
    bars = _sj_index_1min(code, date_str)
    if not bars:
        return {}
    return {'name': industry, 'tse_code': code, 'bars': bars}
