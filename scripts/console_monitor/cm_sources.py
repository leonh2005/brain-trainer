"""三來源的抓取與純函式解析；抓取與解析分離以便離線測試。"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

import requests
from cm_config import CONSOLES, PTT_BOARDS, is_after_cutoff

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
TIMEOUT = 20

SOLD_WORDS = ("已售", "完售", "售完", "結標", "售出", "已結", "交易完成")
WANTED_WORDS = ("徵", "收購", "求購", "想買", "收 ")
SELL_WORDS = ("售", "賣", "出售", "sell")


def extract_price(text: str) -> int | None:
    """從自由文字抽價格；抽不到回 None，不猜。"""
    if not text:
        return None
    match = re.search(r"NT\$\s*([\d,]+)", text, re.I)
    if match:
        return int(match.group(1).replace(",", ""))
    match = re.search(r"([\d,]{4,})\s*元", text)
    if match:
        return int(match.group(1).replace(",", ""))
    match = re.search(r"(\d+(?:\.\d+)?)\s*k(?![A-Za-z0-9])", text, re.I)
    if match:
        return int(float(match.group(1)) * 1000)
    match = re.search(r"(?<!\d)(\d{5})(?!\d)", text)
    if match:
        return int(match.group(1))
    return None


def _price_number(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip().replace(",", ""))
    except ValueError:
        return extract_price(str(value))


def is_sell_post(title: str) -> bool:
    if any(word in title for word in SOLD_WORDS):
        return False
    if any(word in title for word in WANTED_WORDS):
        return False
    return any(word in title.lower() for word in SELL_WORDS)


def matches_console(text: str, key: str) -> bool:
    cfg = CONSOLES[key]
    if not any(re.search(pattern, text, re.I) for pattern in cfg["require"]):
        return False
    if any(re.search(pattern, text, re.I) for pattern in cfg["forbid"]):
        return False
    return True


def apply_excludes(text: str, key: str) -> bool:
    """先把『不含手把』等安全語移除，再擋孤立配件詞。"""
    cfg = CONSOLES[key]
    scrubbed = re.sub(r"\s+", "", text)
    for phrase in cfg["safe_contexts"]:
        scrubbed = scrubbed.replace(phrase, "")
    if any(word in scrubbed for word in cfg["exclude"]):
        return False
    if any(word in scrubbed.lower() for word in cfg["ambiguous"]):
        return False
    return True


def hints_for(name: str, key: str) -> list[str]:
    lowered = name.lower()
    return [hint for hint in CONSOLES[key]["hints"] if hint in lowered]


def parse_ruten_search(payload: dict[str, Any]) -> list[str]:
    return [str(row["Id"]) for row in payload.get("Rows", []) if row.get("Id")]


def parse_ruten_detail(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    minimum = CONSOLES[key]["price_min"]
    for row in payload.get("data", []):
        name = row.get("name") or ""
        if not matches_console(name, key) or not apply_excludes(name, key):
            continue
        price = _price_number(row.get("goods_price"))
        if price is None or price < minimum:
            continue
        num = _price_number(row.get("num"))
        if num is None or num <= 0:
            continue
        # 露天 API 的 is_goods_sale 實測全部回 False，不是可購買旗標，不能用來過濾
        if row.get("available") is False:
            continue
        try:
            posted = int(str(row.get("post_time")))
        except (TypeError, ValueError):
            posted = None
        if not is_after_cutoff(posted):
            continue
        ident = str(row.get("id") or "")
        if not ident:
            continue
        out.append({
            "uid": f"ruten|{key}|{ident}",
            "source": "ruten",
            "console": key,
            "name": name[:120],
            "price": price,
            "seller": row.get("store_name") or row.get("user") or "",
            "posted_ts": posted,
            "url": f"https://www.ruten.com.tw/item/show?{ident}",
            "hints": hints_for(name, key),
        })
    return out


def parse_ptt_search(html: str, board: str, key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    minimum = CONSOLES[key]["price_min"]
    pattern = re.compile(r'<div class="title">\s*<a href="([^"]+)">([^<]+)</a>')
    for href, raw_title in pattern.findall(html):
        title = raw_title.strip()
        if not is_sell_post(title) or not matches_console(title, key) or not apply_excludes(title, key):
            continue
        price = extract_price(title)
        if price is None or price < minimum:
            continue
        match = re.search(r"M\.(\d{10})\.", href)
        posted = int(match.group(1)) if match else None
        if not is_after_cutoff(posted):
            continue
        mid = href.split("/")[-1].replace(".html", "")
        out.append({
            "uid": f"ptt|{board}|{mid}",
            "source": "ptt",
            "console": key,
            "name": title[:120],
            "price": price,
            "seller": "",
            "posted_ts": posted,
            "url": "https://www.ptt.cc" + href,
            "hints": hints_for(title, key),
        })
    return out


def rel_time_to_ts(text: str, now_ts: int) -> int | None:
    """只接受可精確換算的相對時間；模糊或過舊的一律不通知。"""
    if not text:
        return None
    lowered = text.strip().lower()
    if any(word in lowered for word in ("昨天", "天前", "週前", "星期前", "月前", "yesterday", "days ago", "weeks ago")):
        return None
    if lowered in ("今天", "today"):
        return now_ts
    match = re.search(r"(\d+)\s*(秒|分鐘|分|小時| hours?| minutes?| seconds?)\s*(前|ago)?", lowered)
    if not match or not match.group(3):
        return None
    amount = int(match.group(1))
    unit = match.group(2).strip()
    if unit in ("秒", "seconds", "second"):
        seconds = 1
    elif unit in ("分鐘", "分", "minutes", "minute"):
        seconds = 60
    else:
        seconds = 3600
    return now_ts - amount * seconds


def parse_carousell_cards(cards: list[dict[str, Any]], now_ts: int, key: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    minimum = CONSOLES[key]["price_min"]
    for card in cards:
        name = card.get("title") or ""
        if not matches_console(name, key) or not apply_excludes(name, key):
            continue
        price = extract_price(card.get("price_text") or "")
        if price is None or price < minimum:
            continue
        posted = rel_time_to_ts(card.get("time_text") or "", now_ts)
        if not is_after_cutoff(posted):
            continue
        pid = str(card.get("pid") or "")
        if not pid:
            continue
        out.append({
            "uid": f"carousell|{key}|{pid}",
            "source": "carousell",
            "console": key,
            "name": name[:120],
            "price": price,
            "seller": "",
            "posted_ts": posted,
            "url": card.get("url") or f"https://tw.carousell.com/p/{pid}/",
            "hints": hints_for(name, key),
        })
    return out


def fetch_ruten(key: str) -> list[dict[str, Any]]:
    """露天：搜尋取 id → 批量詳情；日期用詳情 post_time。"""
    query = quote(CONSOLES[key]["ruten_query"])
    search = requests.get(
        f"https://rtapi.ruten.com.tw/api/search/v3/index.php/core/prod?q={query}&sort=new/dc&limit=60",
        headers=HEADERS, timeout=TIMEOUT).json()
    ids = parse_ruten_search(search)
    items: list[dict[str, Any]] = []
    for start in range(0, len(ids), 30):
        batch = ",".join(ids[start:start + 30])
        detail = requests.get(
            f"https://rapi.ruten.com.tw/api/items/v2/list?gno={batch}&level=simple",
            headers=HEADERS, timeout=TIMEOUT).json()
        items.extend(parse_ruten_detail(detail, key))
        time.sleep(0.5)
    return items


def fetch_ptt(key: str) -> list[dict[str, Any]]:
    """PTT：兩板搜尋；日期從文章代碼 M.<epoch>.A 抽。"""
    out: list[dict[str, Any]] = []
    for board in PTT_BOARDS:
        for query in CONSOLES[key]["ptt_queries"]:
            url = f"https://www.ptt.cc/bbs/{board}/search?q={quote(query)}"
            html = requests.get(url, headers={**HEADERS, "Cookie": "over18=1"}, timeout=TIMEOUT).text
            out.extend(parse_ptt_search(html, board, key))
            time.sleep(0.5)
    return out


def fetch_carousell(key: str) -> list[dict[str, Any]]:
    """旋轉：headless Firefox + cookies 過 Cloudflare；只回精確可解析的卡片。"""
    import cm_carousell
    cards = cm_carousell.fetch(CONSOLES[key]["carousell_query"], key)
    return parse_carousell_cards(cards, int(time.time()), key)
