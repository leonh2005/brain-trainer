import time
from pathlib import Path

import requests as _req

FINMIND_TOKEN = Path('/Users/steven/CCProject/.secrets/finmind_token.txt').read_text().strip()

_cache: dict = {"data": {}, "time": 0}
_TTL = 3600 * 24  # 24 小時，股票代號/名稱不常變動


def load_stock_names() -> dict:
    """FinMind TaiwanStockInfo 全表快取 {代號: {name, suffix}}。"""
    global _cache
    now = time.time()
    if _cache["data"] and now - _cache["time"] < _TTL:
        return _cache["data"]
    r = _req.get("https://api.finmindtrade.com/api/v4/data", params={
        "dataset": "TaiwanStockInfo", "token": FINMIND_TOKEN
    }, timeout=20)
    rows = r.json().get("data", [])
    result = {}
    for row in rows:
        suffix = ".TW" if row.get("type") == "twse" else ".TWO"
        result[row["stock_id"]] = {"name": row["stock_name"], "suffix": suffix}
    _cache = {"data": result, "time": now}
    return result
