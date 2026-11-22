"""二手主機監控的常數、兩台設定與憑證延遲讀取。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

TPE = timezone(timedelta(hours=8))
CUTOFF_DT = datetime(2026, 11, 22, 0, 0, 0, tzinfo=TPE)
CUTOFF_TS = int(CUTOFF_DT.timestamp())

TOKEN_FILE = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
CHAT_ID = "7556217543"

PTT_BOARDS = ("Gamesale", "PC_Shopping")

COMMON_FILTERS: dict[str, Any] = {
    "exclude": ("保護貼", "貼膜", "螢幕貼", "收納包", "背包", "皮套", "支架", "底座",
                "散熱", "充電線", "傳輸線", "記憶卡", "遊戲片", "光碟收納", "模型", "公仔"),
    "ambiguous": ("手把", "搖桿", "controller", "dualsense"),
    "safe_contexts": ("不含手把", "無手把", "未附手把", "含手把", "附手把",
                      "含原廠手把", "附原廠手把", "含搖桿", "附搖桿", "含原廠搖桿", "附原廠搖桿"),
}

CONSOLES: dict[str, dict[str, Any]] = {
    "ps5_pro": {
        "label": "PS5 Pro",
        "ruten_query": "PS5 Pro",
        "ptt_queries": ("PS5 Pro",),
        "carousell_query": "PS5 Pro",
        "price_min": 18000,
        "require": (r"ps5\s*pro",),
        "forbid": (r"series\s*s", r"(?<![A-Za-z0-9])xss(?![A-Za-z0-9])", r"xbox", r"steam\s*deck", r"rog\s*ally"),
        **COMMON_FILTERS,
        "hints": (),
    },
    "xsx": {
        "label": "Xbox Series X",
        "ruten_query": "Xbox Series X",
        "ptt_queries": ("Xbox Series X", "XSX"),
        "carousell_query": "Xbox Series X",
        "price_min": 10000,
        "require": (r"xbox\s*series\s*x", r"(?<![A-Za-z0-9])xsx(?![A-Za-z0-9])"),
        "forbid": (r"series\s*s", r"(?<![A-Za-z0-9])xss(?![A-Za-z0-9])", r"ps5", r"steam\s*deck", r"rog\s*ally"),
        **COMMON_FILTERS,
        "hints": ("1tb", "光碟", "disc"),
    },
}


def validate_cutoff() -> None:
    """防止時區或常數漂移讓日期門檻失效。"""
    if int(CUTOFF_DT.timestamp()) != CUTOFF_TS:
        raise ValueError("CUTOFF_TS 與 CUTOFF_DT 不一致")
    if datetime.fromtimestamp(CUTOFF_TS, TPE) != CUTOFF_DT:
        raise ValueError("CUTOFF_TS 不是台北時間 2026-11-22 00:00")


def is_after_cutoff(posted_ts: int | None) -> bool:
    return posted_ts is not None and posted_ts >= CUTOFF_TS


def read_token() -> str:
    with open(TOKEN_FILE, encoding="utf-8") as handle:
        return handle.read().strip()
