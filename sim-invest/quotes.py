"""報價層：US→yfinance、TW→shioaji-gateway(5455)、匯率→yfinance TWD=X。帶記憶體快取。"""
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


_GATEWAY_URL = "http://localhost:5455/snapshot"


def _gateway_snapshot(ticker: str) -> dict:
    """呼叫 shioaji-gateway 的單一持久連線，避免各服務直連 Shioaji 互踢/觸發登入限流。"""
    import requests
    resp = requests.get(_GATEWAY_URL, params={"codes": ticker}, timeout=8)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("ok"):
        raise RuntimeError(f"gateway 回應失敗: {payload}")
    data = payload["data"].get(ticker)
    if data is None:
        raise RuntimeError(f"gateway 無此代碼資料: {ticker}")
    return data


def get_quote(ticker: str, market: str) -> float:
    if market == "US":
        return _cached(("US", ticker), lambda: _yf_close(ticker))
    if market == "TW":
        try:
            return _cached(("TW", ticker), lambda: _gateway_snapshot(ticker)["close"])
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


def _gateway_detail(ticker: str) -> dict:
    d = _gateway_snapshot(ticker)
    last = float(d["close"])
    return {"last": last, "prev_close": last - float(d["change_price"]),
            "change_pct": float(d["change_rate"])}


def get_quote_detail(ticker: str, market: str) -> dict:
    """回 {last, prev_close, change_pct}，供即時看盤用（短快取）。"""
    if market == "US":
        return _cached(("USD_DET", ticker), lambda: _yf_detail(ticker), ttl=_DETAIL_TTL)
    if market == "TW":
        try:
            return _cached(("TWD_DET", ticker), lambda: _gateway_detail(ticker), ttl=_DETAIL_TTL)
        except Exception:
            try:
                return _cached(("TWD_DET", ticker), lambda: _yf_detail(f"{ticker}.TW"), ttl=_DETAIL_TTL)
            except Exception:
                return _cached(("TWD_DET", ticker), lambda: _yf_detail(f"{ticker}.TWO"), ttl=_DETAIL_TTL)
    raise ValueError(f"未知市場: {market}")


def get_fx() -> float:
    return _cached(("FX", "USDTWD"), lambda: _yf_close("TWD=X"))
