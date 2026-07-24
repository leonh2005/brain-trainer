"""報價層：US→yfinance、TW→Shioaji、匯率→yfinance TWD=X。帶記憶體快取。"""
import time

_cache: dict = {}
_TTL = 3600  # 秒


def _cached(key, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _yf_close(symbol: str) -> float:
    import yfinance as yf
    data = yf.Ticker(symbol).history(period="1d")
    if data.empty:
        raise RuntimeError(f"yfinance 無資料: {symbol}")
    return float(data["Close"].iloc[-1])


_SHIOAJI_SUFFIX = {"0050": "0050", "00864B": "00864B"}  # 皆為上市代號


def _shioaji_close(ticker: str) -> float:
    """以既有 repo 的 Shioaji 登入模式取即時/收盤價。
    參考 scripts/ma_monitor.py 的 Shioaji 初始化；此處只讀 snapshot.close。"""
    import shioaji as sj
    import os
    api = sj.Shioaji()
    api.login(os.environ["SHIOAJI_API_KEY"], os.environ["SHIOAJI_SECRET_KEY"])
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
        return _cached(("TW", ticker), lambda: _shioaji_close(ticker))
    raise ValueError(f"未知市場: {market}")


def get_fx() -> float:
    return _cached(("FX", "USDTWD"), lambda: _yf_close("TWD=X"))
