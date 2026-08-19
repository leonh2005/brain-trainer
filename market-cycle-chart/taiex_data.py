# -*- coding: utf-8 -*-
"""台股加權指數(TAIEX)資料層。

- 從 FinMind 抓 TAIEX 日線(TaiwanStockPrice / data_id=TAIEX)
- resample 成月線
- 計算移動平均線(5/20/60MA)
- 本地快取(每日更新一次)，抓取失敗時回退快取

Token 路徑沿用既有專案：~/CCProject/.secrets/finmind_token.txt
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

import pandas as pd

_TOKEN_PATH = os.path.expanduser("~/CCProject/.secrets/finmind_token.txt")
_BASE_URL = "https://api.finmindtrade.com/api/v4/data"
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "taiex_daily.json")
_HISTORY_START = "2015-01-01"  # 抓取起點，讓月線有足夠歷史


class TaiexDataError(Exception):
    """資料抓取失敗且無快取可用時拋出。"""


def _read_token() -> str:
    with open(_TOKEN_PATH) as f:
        return f.read().strip()


def _fm_get_taiex(start_date: str, end_date: str) -> list:
    """呼叫 FinMind 抓 TAIEX 日線原始資料。"""
    params = urllib.parse.urlencode({
        "dataset": "TaiwanStockPrice",
        "data_id": "TAIEX",
        "start_date": start_date,
        "end_date": end_date,
        "token": _read_token(),
    })
    body = json.loads(urllib.request.urlopen(f"{_BASE_URL}?{params}", timeout=20).read())
    if body.get("status") != 200:
        raise TaiexDataError(f"FinMind API 錯誤: {body.get('msg')}")
    return body.get("data", [])


def _normalize(rows: list) -> pd.DataFrame:
    """FinMind 原始欄位 → 標準 OHLCV。"""
    df = pd.DataFrame(rows)
    df = df.rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
    df["date"] = pd.to_datetime(df["date"])
    cols = ["date", "open", "high", "low", "close", "volume"]
    return df[cols].sort_values("date").reset_index(drop=True)


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def get_daily(force_refresh: bool = False) -> pd.DataFrame:
    """取 TAIEX 日線(2015 至今)。每日抓一次並快取，失敗回退快取。"""
    cache = _load_cache()
    if not force_refresh and cache is not None and cache["fetched_date"] == _today():
        return _normalize(cache["rows"])

    try:
        rows = _fm_get_taiex(_HISTORY_START, _today())
        if not rows:
            raise TaiexDataError("FinMind 回傳空資料")
        _save_cache(rows)
        return _normalize(rows)
    except Exception as e:
        if cache is not None:
            return _normalize(cache["rows"])  # 回退舊快取
        raise TaiexDataError(f"TAIEX 抓取失敗且無快取: {e}") from e


def get_monthly(df_daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """日線 resample 成月線 OHLCV。月標籤為當月最後一日。"""
    df = df_daily if df_daily is not None else get_daily()
    m = df.set_index("date").resample("ME").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"]).reset_index()
    return m


def get_weekly(df_daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """日線 resample 成週線 OHLCV。週標籤為當週五(台股收盤日)。"""
    df = df_daily if df_daily is not None else get_daily()
    w = df.set_index("date").resample("W-FRI").agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum",
    }).dropna(subset=["close"]).reset_index()
    return w


def add_moving_averages(df: pd.DataFrame, windows=(5, 20, 60)) -> pd.DataFrame:
    """依收盤價加入移動平均線欄位(ma5/ma20/ma60)。"""
    out = df.copy()
    for w in windows:
        out[f"ma{w}"] = out["close"].rolling(window=w, min_periods=1).mean().round(2)
    return out


def _load_cache() -> dict | None:
    try:
        with open(_CACHE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_cache(rows: list) -> None:
    os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
    with open(_CACHE_PATH, "w") as f:
        json.dump({"fetched_date": _today(), "rows": rows}, f)
