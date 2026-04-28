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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import threading
from datetime import time as _time

import pandas as pd


class RealTimeFeed:
    """
    訂閱 Shioaji tick 串流，在記憶體組合 1 分 K 棒。
    執行緒安全（_lock 保護所有狀態）。
    只保留「已完成」的 bar（minute 已結束），避免前端顯示不完整 bar。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stock_id: str | None = None
        self._completed: list[dict] = []   # [{ts, open, high, low, close, volume}, ...]
        self._forming: dict | None = None  # 目前正在形成的 bar

    # ── 供外部呼叫 ────────────────────────────────────────────────────────────

    def set_stock(self, stock_id: str) -> None:
        """切換訂閱股票，清空舊資料。由 subscribe_realtime() 呼叫。"""
        with self._lock:
            if self._stock_id == stock_id:
                return
            self._stock_id = stock_id
            self._completed = []
            self._forming = None
        print(f'[feed] 切換訂閱股票 → {stock_id}')

    def on_tick(self, tick) -> None:
        """
        Shioaji on_tick_stk_v1 callback 呼叫此方法。
        tick 為 TickSTKv1 instance，有 code, datetime, close, volume 等欄位。
        """
        try:
            # 每隻股票只 log 第一筆 tick，確認 callback 有在運作
            with self._lock:
                if not hasattr(self, '_first_tick_logged'):
                    self._first_tick_logged = set()
            code = getattr(tick, 'code', '?')
            with self._lock:
                if code not in self._first_tick_logged:
                    print(f'[feed] ✓ 收到第一筆 tick {code} @ {getattr(tick, "datetime", "?")} close={getattr(tick, "close", "?")}')
                    self._first_tick_logged.add(code)

            tick_dt = tick.datetime
            t = tick_dt.time()
            # 只處理盤中 09:00~13:30
            if t < _time(9, 0) or t > _time(13, 30):
                return

            # 取「分鐘開始」時間字串，例如 "2026-04-16 10:41"
            minute_str = tick_dt.strftime('%Y-%m-%d %H:%M')
            price  = round(float(tick.close), 2)
            volume = int(tick.volume)

            with self._lock:
                # 不是我們訂閱的股票，略過
                if self._stock_id and tick.code != self._stock_id:
                    return

                if self._forming is None:
                    # 第一根 bar
                    self._forming = _make_bar(minute_str, price, volume)
                elif self._forming['ts'] == minute_str:
                    # 同一分鐘，更新 forming bar
                    b = self._forming
                    b['high']   = max(b['high'],  price)
                    b['low']    = min(b['low'],   price)
                    b['close']  = price
                    b['volume'] += volume
                else:
                    # 新的一分鐘開始 → 把 forming bar 存入 completed
                    self._completed.append(dict(self._forming))
                    self._forming = _make_bar(minute_str, price, volume)
        except Exception as e:
            print(f'[feed] on_tick error: {e}')

    def get_bars(self, date_str: str, include_forming: bool = False) -> list[dict]:
        """回傳 date_str 當日所有已完成的 1 分 K bar。
        include_forming=True 時也包含正在形成的 bar（收盤最後一根用）。"""
        prefix = date_str + ' '
        with self._lock:
            bars = [dict(b) for b in self._completed if b['ts'].startswith(prefix)]
            if include_forming and self._forming and self._forming['ts'].startswith(prefix):
                bars.append(dict(self._forming))
            return bars

    def current_stock(self) -> str | None:
        with self._lock:
            return self._stock_id


def _make_bar(ts: str, price: float, volume: int) -> dict:
    return {'ts': ts, 'open': price, 'high': price,
            'low': price, 'close': price, 'volume': volume}


# 全域單例
_realtime_feed = RealTimeFeed()

_LAST_STOCK_FILE = '/tmp/daytrade_last_stock.txt'


def _save_last_stock(stock_id: str) -> None:
    try:
        with open(_LAST_STOCK_FILE, 'w') as f:
            f.write(stock_id)
    except Exception:
        pass


def _load_last_stock() -> str | None:
    try:
        if os.path.exists(_LAST_STOCK_FILE):
            s = open(_LAST_STOCK_FILE).read().strip()
            return s if s else None
    except Exception:
        pass
    return None


def _is_trading_time() -> bool:
    """是否在台股盤中（週一~五 09:00~13:30）。"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return _time(9, 0) <= t <= _time(13, 30)


def _auto_subscribe_worker() -> None:
    """背景執行緒：每天 09:00 自動訂閱上次股票。"""
    import shioaji as sj  # noqa: F401 (確認 import 可用)
    print('[auto-sub] 背景排程執行緒啟動')
    last_subscribed_day: date | None = None
    last_stock: str | None = None

    while True:
        try:
            now = datetime.now()
            today = now.date()

            if now.weekday() < 5:  # 週一～五
                t = now.time()
                # 在 09:00~09:05 之間，且今天還沒訂閱過
                if _time(9, 0) <= t <= _time(9, 5) and last_subscribed_day != today:
                    stock_id = _realtime_feed.current_stock() or _load_last_stock()
                    if stock_id:
                        try:
                            subscribe_realtime(stock_id)
                            last_subscribed_day = today
                            last_stock = stock_id
                            print(f'[auto-sub] 09:00 自動訂閱 {stock_id}')
                        except Exception as e:
                            print(f'[auto-sub] 訂閱失敗: {e}')

                # 若目前是盤中且訂閱的股票跟 feed 不同步（切換後沒觸發），補訂
                current = _realtime_feed.current_stock()
                if (current and current != last_stock and
                        _time(9, 0) <= t <= _time(13, 30)):
                    last_stock = current

        except Exception as e:
            print(f'[auto-sub] worker error: {e}')

        time.sleep(30)  # 每 30 秒檢查一次


def _start_auto_subscribe() -> None:
    """啟動自動訂閱背景執行緒（只啟動一次）。
    同時若服務啟動時已在盤中，立即訂閱上次股票。"""
    t = threading.Thread(target=_auto_subscribe_worker, daemon=True, name='auto-sub')
    t.start()

    # 服務啟動時若在盤中，立即訂閱
    if _is_trading_time():
        stock_id = _load_last_stock()
        if stock_id:
            def _delayed():
                time.sleep(8)  # 等 SJ 登入完成
                try:
                    subscribe_realtime(stock_id)
                    print(f'[auto-sub] 服務啟動盤中，自動訂閱 {stock_id}')
                except Exception as e:
                    print(f'[auto-sub] 啟動訂閱失敗: {e}')
            threading.Thread(target=_delayed, daemon=True, name='auto-sub-init').start()


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
            with open(CACHE_FILE) as f:
                cached = json.load(f)
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

    with open(CACHE_FILE, 'w') as f:
        json.dump({'date': today_str, 'stocks': stocks}, f, ensure_ascii=False)
    if stocks:
        print(f'[data] top30 完成，第1名: {stocks[0]["code"]} {stocks[0]["name"]} {stocks[0]["avg_vol"]:,}張')
    return stocks


# ── 1分K（Shioaji 優先，yfinance fallback）──────────────────────────────────

def _yf_ticker(stock_id: str) -> str:
    return stock_id + '.TW'


def _yf_today_1min(stock_id: str) -> list:
    """yfinance 取今日盤中 1分K（延遲約 1~2 分鐘，不快取）。"""
    import yfinance as yf
    try:
        df = yf.download(_yf_ticker(stock_id), interval='1m', period='1d',
                         progress=False, auto_adjust=True)
    except Exception as e:
        print(f'[yf-today] {stock_id} 失敗: {e}')
        return []
    if df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_convert('Asia/Taipei')
    df = df.between_time('09:01', '13:30')
    if df.empty:
        return []
    df = df.reset_index()
    df.rename(columns={'Datetime': 'ts', 'Open': 'open', 'High': 'high',
                       'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
    df['ts'] = df['ts'].dt.strftime('%Y-%m-%d %H:%M')
    for col in ('open', 'high', 'low', 'close'):
        df[col] = df[col].round(2)
    df['volume'] = (df['volume'].fillna(0) / 1000).astype(int)
    result = df[['ts', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    print(f'[yf-today] {stock_id} 取得 {len(result)} 根')
    return result


def _sj_stock_1min(stock_id: str, date_str: str) -> list:
    """Shioaji 取個股 1分K。今日不快取（盤中資料持續更新）。"""
    today_str = str(date.today())
    cache_path = f'/tmp/sj_kbar_{stock_id}_{date_str}.json'
    if date_str != today_str and os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks[stock_id]
        if contract is None:
            return []
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                kb = ex.submit(api.kbars, contract, start=date_str, end=date_str).result(timeout=8)
            except FutureTimeoutError:
                print(f'[sj] kbars timeout {stock_id} {date_str}，跳過')
                return []
        df = pd.DataFrame({**kb})
        if df.empty:
            return []
        df['ts'] = pd.to_datetime(df['ts'])
        # Shioaji 時戳為開盤時間（start-time），不需偏移
        t_open  = pd.Timestamp('09:00').time()
        t_close = pd.Timestamp('13:30').time()
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
            with open(cache_path, 'w') as f:
                json.dump(result, f, ensure_ascii=False)
        return result
    except Exception as e:
        print(f'[sj] stock kbar {stock_id} {date_str} 失敗: {e}')
        return []


def get_1min_kbars(stock_id: str, date_str: str) -> list:
    """
    取指定股票指定日的1分K。
    今日：yfinance 1分K + 即時 tick bars 合併回傳（tick-built 優先）。
    非今日：Shioaji kbars() 優先（cache），失敗 fallback yfinance（最近30天）。
    只保留 09:00~13:30，volume 單位：張（÷1000）。
    今日資料不快取（持續更新）。
    """
    today_str = str(date.today())

    if date_str == today_str:
        from datetime import datetime as _dt
        from datetime import time as _t
        after_close = _dt.now().time() >= _t(13, 30)

        if not after_close:
            # 盤中：先用 Shioaji kbars 取已完成棒（任意標的均適用）
            bars = _sj_stock_1min(stock_id, date_str)
            if bars:
                return bars
            # kbars 無資料（剛開盤第一分鐘 / 非今日交易標的）→ 用 feed
            return _realtime_feed.get_bars(date_str, include_forming=False)

        # 盤後：Shioaji kbars 取今日完整資料，不用 yfinance
        bars = _sj_stock_1min(stock_id, date_str)
        if bars:
            return bars
        return _realtime_feed.get_bars(date_str, include_forming=False)

    # ── 非今日：Shioaji kbars() 優先，失敗 fallback yfinance ──────────────
    bars = _sj_stock_1min(stock_id, date_str)
    if bars:
        return bars

    # Fallback: yfinance（最近30天支援1分K）
    cache_path = f'/tmp/kbar_{stock_id}_{date_str}.json'
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    import yfinance as yf
    end_date = (date.fromisoformat(date_str) + timedelta(days=1)).strftime('%Y-%m-%d')
    df = pd.DataFrame()
    for suffix in ['.TW', '.TWO']:
        ticker = stock_id + suffix
        try:
            df = yf.download(ticker, interval='1m', start=date_str, end=end_date,
                             progress=False, auto_adjust=True)
        except Exception:
            continue
        if not df.empty:
            break
    if df.empty:
        print(f'[yf] kbar {stock_id} 無資料（.TW/.TWO 均失敗）')
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_convert('Asia/Taipei')
    df = df.between_time('09:01', '13:30')
    if df.empty:
        return []
    df = df.reset_index()
    df.rename(columns={'Datetime': 'ts', 'Open': 'open', 'High': 'high',
                       'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
    df['ts'] = df['ts'].dt.strftime('%Y-%m-%d %H:%M')
    for col in ('open', 'high', 'low', 'close'):
        df[col] = df[col].round(2)
    # yfinance volume 單位為股，除以 1000 轉換為張（與 Shioaji kbars 一致）
    df['volume'] = (df['volume'].fillna(0) / 1000).astype(int)
    result = df[['ts', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    with open(cache_path, 'w') as f:
        json.dump(result, f, ensure_ascii=False)
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
_sj_login_time: float = 0   # 上次登入時間戳（time.time()）
_sj_last_check: float = 0   # 上次 heartbeat 時間戳（time.time()）
_SJ_CHECK_INTERVAL = 300    # 每 5 分鐘才做一次 heartbeat，避免 FD 洩漏
_SJ_TOKEN_TTL = 22 * 3600   # JWT 24h 過期，提前 2h 主動重登（22h）

def _get_sj():
    global _sj_api, _sj_login_time, _sj_last_check
    import shioaji as sj
    from dotenv import load_dotenv

    def _login():
        global _sj_login_time, _sj_last_check
        load_dotenv(os.path.join(os.path.dirname(__file__), '../finmind/.env'))
        api = sj.Shioaji(simulation=False)
        api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        now = time.time()
        _sj_login_time = now
        _sj_last_check = now
        print('[sj] 永豐 API 登入成功')

        # 登記 tick callback（per-api-instance，重連後必須重新登記）
        @api.on_tick_stk_v1()
        def _on_tick_stk(exchange, tick):
            _realtime_feed.on_tick(tick)

        print('[sj] tick callback 已登記')
        return api

    now = time.time()

    # JWT token 快到期（22h）→ 主動重登
    if _sj_api is not None and (now - _sj_login_time) >= _SJ_TOKEN_TTL:
        print('[sj] token 即將過期，主動重新登入...')
        try:
            _sj_api.logout()
        except Exception:
            pass
        _sj_api = None

    if _sj_api is None:
        _sj_api = _login()
        return _sj_api

    # 每 5 分鐘才做一次 heartbeat，避免每次呼叫都開 FD
    if now - _sj_last_check >= _SJ_CHECK_INTERVAL:
        try:
            _sj_api.list_accounts()
            _sj_last_check = now
        except Exception:
            print('[sj] session 失效，重新登入...')
            try:
                _sj_api.logout()
            except Exception:
                pass
            _sj_api = None
            _sj_api = _login()

    return _sj_api


_stock_index: list[dict] | None = None  # [{code, name}] 全庫快取


def _build_stock_index() -> list[dict]:
    """從 Shioaji 合約庫建立代號+名稱索引（只建一次）"""
    global _stock_index
    if _stock_index is not None:
        return _stock_index
    api = _get_sj()
    idx = []
    for exchange in api.Contracts.Stocks:
        for contract in exchange:
            try:
                idx.append({'code': contract.code, 'name': contract.name})
            except Exception:
                pass
    _stock_index = idx
    print(f'[search] 股票索引建立完成，共 {len(idx)} 檔')
    return _stock_index


def search_stocks(query: str, limit: int = 8) -> list[dict]:
    """搜尋股票：代號前綴或名稱包含 query，一般股（4位代號）優先"""
    idx = _build_stock_index()
    seen = set()

    def _add(bucket, s):
        if s['code'] not in seen:
            seen.add(s['code'])
            bucket.append(s)

    # 分桶：一般股（4位）vs 其他（權證/債券）
    exact_stock, exact_other = [], []
    prefix_stock, prefix_other = [], []
    name_stock, name_other = [], []

    for s in idx:
        code, name = s['code'], s['name']
        is_stock = len(code) == 4 and code.isdigit()
        if code == query:
            _add(exact_stock if is_stock else exact_other, s)
        elif code.startswith(query):
            _add(prefix_stock if is_stock else prefix_other, s)
        elif query in name:
            _add(name_stock if is_stock else name_other, s)

    ordered = exact_stock + exact_other + prefix_stock + name_stock + prefix_other + name_other
    return ordered[:limit]


def subscribe_realtime(stock_id: str) -> None:
    """
    對指定股票開始 Shioaji tick 訂閱。
    切換股票時自動取消舊訂閱再訂新的。
    只在今日盤中才需要呼叫。
    訂閱失敗時會 raise Exception，由呼叫端處理。
    """
    import shioaji as sj
    api = _get_sj()

    old_id = _realtime_feed.current_stock()
    # 取消舊訂閱（不同股票才需要，失敗不影響後續訂閱）
    if old_id and old_id != stock_id:
        try:
            old_contract = api.Contracts.Stocks[old_id]
            api.quote.unsubscribe(
                old_contract,
                quote_type=sj.constant.QuoteType.Tick,
                version=sj.constant.QuoteVersion.v1,
            )
            print(f'[sj] 取消訂閱 {old_id}')
        except Exception as e:
            print(f'[sj] 取消訂閱失敗（繼續）: {e}')

    # 切換 feed 的目標股票（清空舊 bars）
    _realtime_feed.set_stock(stock_id)

    # 訂閱新股票（失敗時 raise）
    contract = api.Contracts.Stocks[stock_id]
    api.quote.subscribe(
        contract,
        quote_type=sj.constant.QuoteType.Tick,
        version=sj.constant.QuoteVersion.v1,
    )
    print(f'[sj] 訂閱 {stock_id} tick 成功')
    _save_last_stock(stock_id)


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
        with open(cache_path) as f:
            return json.load(f)
    try:
        api = _get_sj()
        contract = api.Contracts.Indexs.TSE[tse_code]
        with ThreadPoolExecutor(max_workers=1) as ex:
            try:
                kb = ex.submit(api.kbars, contract, start=date_str, end=date_str).result(timeout=8)
            except FutureTimeoutError:
                print(f'[sj] index kbars timeout {tse_code} {date_str}，跳過')
                return []
        df = pd.DataFrame({**kb})
        if df.empty:
            return []
        df['ts'] = pd.to_datetime(df['ts'])
        # Shioaji 時戳為開盤時間（start-time），不需偏移
        t_open  = pd.Timestamp('09:00').time()
        t_close = pd.Timestamp('13:30').time()
        df = df[(df['ts'].dt.time >= t_open) & (df['ts'].dt.time <= t_close)]
        result = [{'ts': row['ts'].strftime('%Y-%m-%d %H:%M'), 'close': round(float(row['Close']), 2)}
                  for _, row in df.iterrows()]
        with open(cache_path, 'w') as f:
            json.dump(result, f)
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
        with open(cache_path) as f:
            return json.load(f)
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
            with open(cache_path, 'w') as f:
                json.dump(result, f)
        return result
    except Exception as e:
        print(f'[taiex] yfinance fallback 失敗: {e}')
        return []


# ── 股票產業別 ────────────────────────────────────────────────────────────────

def get_stock_sector(stock_id: str) -> str:
    """從 FinMind TaiwanStockInfo 取股票產業類別"""
    cache_path = f'/tmp/stock_sector_{stock_id}.json'
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f).get('industry', '')

    try:
        import requests as _req
        with open('/Users/steven/CCProject/.secrets/finmind_token.txt') as f:
            token = f.read().strip()
        res = _req.get(
            'https://api.finmindtrade.com/api/v4/data',
            params={'dataset': 'TaiwanStockInfo', 'data_id': stock_id, 'token': token},
            timeout=10,
        )
        rows = res.json().get('data', [])
        industry = rows[0].get('industry_category', '') if rows else ''
        with open(cache_path, 'w') as f:
            json.dump({'industry': industry}, f, ensure_ascii=False)
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
