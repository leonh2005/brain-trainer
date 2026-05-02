"""
週成交量前 20 標的篩選（排除美債 ETF）
使用 TWSE/TPEX API 取得近一週日量，Shioaji 補全名稱。
"""
import json
import os
import time
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import shioaji as sj

warnings.filterwarnings("ignore")

WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"

US_BOND_KEYWORDS = ["美債", "美國債", "US Bond", "UST", "Treasury"]
US_BOND_CODES = {"00779B", "00780B", "00781B", "00782B", "00783B", "00784B",
                 "00720B", "00724B", "00725B", "00726B", "00727B"}

_sj_api = None


def _get_sj():
    global _sj_api
    if _sj_api is None:
        _sj_api = sj.Shioaji(simulation=False)
        _sj_api.login(
            api_key=os.environ["SHIOAJI_API_KEY"],
            secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        )
    return _sj_api


def _is_us_bond(code: str, name: str) -> bool:
    if code in US_BOND_CODES:
        return True
    return any(kw in name for kw in US_BOND_KEYWORDS)


def fetch_weekly_top20() -> list[dict]:
    """取得本週成交量前 20 標的（以最新交易日快照為基準），回傳 [{symbol, name, weekly_vol_k}]"""
    vol_map: dict[str, float] = {}
    name_map: dict[str, str] = {}

    # 上市（TSE）
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            timeout=20, verify=False, headers={"User-Agent": "Mozilla/5.0"},
        )
        for s in r.json():
            code = s.get("Code", "").strip()
            name = s.get("Name", "").strip()
            try:
                vol = float(str(s.get("TradeVolume", "0")).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if not code.isdigit() or len(code) != 4:
                continue
            if _is_us_bond(code, name):
                continue
            vol_map[code] = vol
            name_map[code] = name
        print(f"[screener] TSE 取得 {len(vol_map)} 檔")
    except Exception as e:
        print(f"[screener] TSE 失敗: {e}")

    # ETF（含 6 碼）
    try:
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/ETF_DAY",
            timeout=20, verify=False, headers={"User-Agent": "Mozilla/5.0"},
        )
        for s in r.json():
            code = s.get("Code", "").strip()
            name = s.get("Name", "").strip()
            try:
                vol = float(str(s.get("TradeVolume", "0")).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if _is_us_bond(code, name):
                continue
            vol_map[code] = vol
            name_map[code] = name
    except Exception as e:
        print(f"[screener] ETF 失敗: {e}")

    # 上櫃（OTC）
    try:
        r = requests.get(
            "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
            timeout=20, verify=False, headers={"User-Agent": "Mozilla/5.0"},
        )
        for s in r.json():
            code = s.get("SecuritiesCompanyCode", "").strip()
            name = s.get("CompanyName", "").strip()
            try:
                vol = float(str(s.get("Volume", "0")).replace(",", ""))
            except (ValueError, TypeError):
                continue
            if _is_us_bond(code, name):
                continue
            vol_map[code] = vol_map.get(code, 0) + vol
            name_map[code] = name
    except Exception as e:
        print(f"[screener] OTC 失敗: {e}")

    ranked = sorted(vol_map.items(), key=lambda x: x[1], reverse=True)[:20]
    result = [
        {"symbol": code, "name": name_map.get(code, ""), "weekly_vol_k": round(vol / 1000, 1)}
        for code, vol in ranked
    ]
    WATCHLIST_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[screener] 前 20：{[r['symbol'] + ' ' + name_map.get(r['symbol'],'') for r in result]}")
    return result


def load_watchlist() -> list[dict]:
    if WATCHLIST_PATH.exists():
        return json.loads(WATCHLIST_PATH.read_text())
    return fetch_weekly_top20()
