# -*- coding: utf-8 -*-
"""市場行情排行：向 shioaji-gateway 取排行掃描結果。

Gateway 獨佔 Shioaji 連線，這裡只做 HTTP 呼叫與錯誤轉換，不直接連 Shioaji。
"""
import requests

SHIOAJI_GATEWAY = "http://127.0.0.1:5455"  # 與 app.py 的常數一致；呼叫端仍會顯式傳入

# 卡片上可選的排行；key 是前端與 gateway 共用的簡寫
RANK_TYPES = {
    "pct": "漲跌幅",
    "volume": "成交量",
    "amount": "成交金額",
}
DEFAULT_COUNT = 20


def fetch_rank(kind: str, count: int = DEFAULT_COUNT, gateway: str = SHIOAJI_GATEWAY,
               timeout: int = 60) -> list:
    """回傳排行項目（gateway 已整理好的 list）；失敗時丟例外讓畫面容錯。

    timeout 要比 gateway 內部的掃描 timeout（30 秒）寬，否則排隊時 client 會先放棄。
    """
    if kind not in RANK_TYPES:
        raise ValueError(f"未知排行類型: {kind}")
    resp = requests.get(f"{gateway}/scanner",
                        params={"type": kind, "count": count, "ascending": "true"},
                        timeout=timeout)
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("error") or "gateway 回報失敗")
    if "items" not in body:
        # 缺欄位＝上游版本不合／回應被截斷，不能當成「市場沒資料」
        raise RuntimeError("gateway 回應缺少 items 欄位")
    return body["items"] or []
