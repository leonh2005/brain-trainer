#!/usr/bin/env python3
"""RHH 軟纖補貨監控：抓各來源 → 篩軟纖 → 缺→有 轉換推 Telegram → 寫面板檔。

只通知，絕不自動下單。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import requests

import sources

SEEN_FILE = os.path.expanduser("~/CCProject/scripts/hay_seen.json")
CURRENT_FILE = os.path.expanduser("~/CCProject/scripts/hay_current.json")
RESTOCK_FILE = os.path.expanduser("~/CCProject/scripts/hay_restock_dates.json")
LOG_FILE = os.path.expanduser("~/CCProject/logs/hay_monitor.log")
TOKEN_FILE = os.path.expanduser("~/CCProject/.secrets/tgclaude_token.txt")
CHAT_ID = "7556217543"
TPE = timezone(timedelta(hours=8))

SOURCES = (
    ("豬寶窩窩", sources.fetch_piggybabies),
    ("魏啥麻", sources.fetch_weyyngbuy),
)


def log(msg: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(f"[{datetime.now(TPE):%Y-%m-%d %H:%M:%S}] {msg}\n")


def _key(item: dict) -> str:
    return f"{item['shop']}|{item['url']}|{item['variant']}"


def collect() -> list[dict]:
    """抓所有來源，單一來源失敗不影響其他來源。"""
    items = []
    for label, fn in SOURCES:
        try:
            items.extend(fn())
        except Exception as e:
            log(f"{label} 抓取失敗: {e}")
    return items


def send(item: dict) -> None:
    token = open(TOKEN_FILE).read().strip()
    price = f"${item['price']}" if item.get("price") else "價格見頁面"
    text = (
        "🌾 <b>Rabbit Hole Hay 軟纖補貨！</b>\n\n"
        f"{item['shop']}｜{item['variant']}｜{price}\n🔗 {item['url']}"
    )
    requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": CHAT_ID, "parse_mode": "HTML",
              "text": text, "disable_web_page_preview": False},
        timeout=15,
    )


def _load_seen() -> set | None:
    try:
        return set(json.load(open(SEEN_FILE)))
    except (OSError, ValueError):
        return None


def _load_restock_dates() -> dict:
    try:
        return json.load(open(RESTOCK_FILE))
    except (OSError, ValueError):
        return {}


def _update_restock_dates(items: list[dict], seen: set | None) -> dict:
    """有貨起算日：首次被偵測到有貨的日期，缺貨後重新有貨會更新為新日期。"""
    dates = _load_restock_dates()
    today = datetime.now(TPE).strftime("%Y-%m-%d")
    seen = seen or set()
    for it in items:
        key = _key(it)
        if it["in_stock"]:
            if key not in seen or key not in dates:
                dates[key] = today
        else:
            dates.pop(key, None)
    json.dump(dates, open(RESTOCK_FILE, "w"), ensure_ascii=False)
    return dates


def _persist(items: list[dict], in_stock_keys: set, restock_dates: dict) -> None:
    json.dump(sorted(in_stock_keys), open(SEEN_FILE, "w"), ensure_ascii=False)
    payload = {
        "updated": f"{datetime.now(TPE):%Y-%m-%d %H:%M}",
        "items": [
            {"shop": it["shop"], "title": it["title"], "variant": it["variant"],
             "price": it["price"], "in_stock": it["in_stock"], "url": it["url"],
             "since": restock_dates.get(_key(it)) if it["in_stock"] else None}
            for it in items
        ],
    }
    json.dump(payload, open(CURRENT_FILE, "w"), ensure_ascii=False)


def main() -> None:
    seen = _load_seen()
    items = collect()
    in_stock_keys = {_key(it) for it in items if it["in_stock"]}
    restock_dates = _update_restock_dates(items, seen)

    if seen is None:
        _persist(items, in_stock_keys, restock_dates)
        log(f"baseline 建立：{len(items)} 筆軟纖品項（有貨 {len(in_stock_keys)}），不推播")
        return

    restocked = [it for it in items if it["in_stock"] and _key(it) not in seen]
    for it in restocked:
        send(it)

    _persist(items, in_stock_keys, restock_dates)
    log(f"檢查完成：軟纖 {len(items)} 筆（有貨 {len(in_stock_keys)}），補貨推播 {len(restocked)} 筆")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR: {e}")
        raise
