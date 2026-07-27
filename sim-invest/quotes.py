"""報價層：US→yfinance、TW→Shioaji、匯率→yfinance TWD=X。帶記憶體快取。"""
import time

_cache: dict = {}
_TTL = 3600  # 秒


def _cached(key, fn, ttl=_TTL):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


_DETAIL_TTL = 60  # 手動刷新即時報價用的短快取（秒）


def _yf_close(symbol: str) -> float:
    import yfinance as yf
    data = yf.Ticker(symbol).history(period="1d")
    if data.empty:
        raise RuntimeError(f"yfinance 無資料: {symbol}")
    return float(data["Close"].iloc[-1])


_SECRETS_DIR = "/Users/steven/CCProject/.secrets"


def _shioaji_keys():
    """先讀環境變數，否則讀 .secrets 檔案。"""
    import os
    api_key = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    if api_key and secret_key:
        return api_key, secret_key
    with open(f"{_SECRETS_DIR}/shioaji_api.txt") as f:
        api_key = f.read().strip()
    with open(f"{_SECRETS_DIR}/shioaji_secret.txt") as f:
        secret_key = f.read().strip()
    return api_key, secret_key


def _shioaji_close(ticker: str) -> float:
    """以既有 repo 的 Shioaji 登入模式取即時/收盤價。
    參考 scripts/ma_monitor.py 的 Shioaji 初始化；此處只讀 snapshot.close。"""
    import shioaji as sj
    api_key, secret_key = _shioaji_keys()
    api = sj.Shioaji()
    api.login(api_key=api_key, secret_key=secret_key)
    try:
        contract = api.Contracts.Stocks[ticker]
        snap = api.snapshots([contract])[0]
        return float(snap.close)
    finally:
        api.logout()


def get_quote(ticker: str, market: str) -> float:
    if market == "US":
        return _cached(("US", ticker), lambda: _yf_close(ticker))
    if market == "TW":
        try:
            return _cached(("TW", ticker), lambda: _shioaji_close(ticker))
        except Exception:
            try:
                return _cached(("TW", ticker), lambda: _yf_close(f"{ticker}.TW"))
            except Exception:
                return _cached(("TW", ticker), lambda: _yf_close(f"{ticker}.TWO"))
    raise ValueError(f"未知市場: {market}")


def _yf_detail(symbol: str) -> dict:
    import yfinance as yf
    closes = yf.Ticker(symbol).history(period="5d")["Close"].dropna()
    if closes.empty:
        raise RuntimeError(f"yfinance 無資料: {symbol}")
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else last
    change_pct = (last - prev) / prev * 100 if prev else 0.0
    return {"last": last, "prev_close": prev, "change_pct": change_pct}


def _shioaji_detail(ticker: str) -> dict:
    import shioaji as sj
    api_key, secret_key = _shioaji_keys()
    api = sj.Shioaji()
    api.login(api_key=api_key, secret_key=secret_key)
    try:
        snap = api.snapshots([api.Contracts.Stocks[ticker]])[0]
        last = float(snap.close)
        return {"last": last, "prev_close": last - float(snap.change_price),
                "change_pct": float(snap.change_rate)}
    finally:
        api.logout()


def get_quote_detail(ticker: str, market: str) -> dict:
    """回 {last, prev_close, change_pct}，供即時看盤用（短快取）。"""
    if market == "US":
        return _cached(("USD_DET", ticker), lambda: _yf_detail(ticker), ttl=_DETAIL_TTL)
    if market == "TW":
        try:
            return _cached(("TWD_DET", ticker), lambda: _shioaji_detail(ticker), ttl=_DETAIL_TTL)
        except Exception:
            try:
                return _cached(("TWD_DET", ticker), lambda: _yf_detail(f"{ticker}.TW"), ttl=_DETAIL_TTL)
            except Exception:
                return _cached(("TWD_DET", ticker), lambda: _yf_detail(f"{ticker}.TWO"), ttl=_DETAIL_TTL)
    raise ValueError(f"未知市場: {market}")


def get_fx() -> float:
    return _cached(("FX", "USDTWD"), lambda: _yf_close("TWD=X"))
