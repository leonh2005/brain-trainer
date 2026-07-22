#!/usr/bin/env python3
"""RHH 軟纖來源 adapter：豬寶窩窩（WooCommerce）、魏啥麻（官方代理）。

每個 fetch_* 回傳 list[dict]，dict 欄位：
    {shop, title, variant, price(str|None), in_stock(bool), url}
只保留 title 或 variant 含「軟纖」或「soft」(不分大小寫) 的品項。
"""
from __future__ import annotations

import html as htmllib
import json
import re

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 20

PIGGY_URL = "https://www.piggybabiesbnb.com/product/rabbitholehaysofttimothy/"
WEYYNG_CATEGORY = "https://www.weyyngbuy.com/categories/RabbitHoleHay"
WEYYNG_PRODUCT = "https://www.weyyngbuy.com/products/{id}"


def _is_soft(*texts: str) -> bool:
    for t in texts:
        if not t:
            continue
        low = t.lower()
        if "軟纖" in t or "soft" in low:
            return True
    return False


def _get(url: str) -> str:
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT).text


# ── 豬寶窩窩（WooCommerce）────────────────────────────
def parse_piggybabies(page: str, url: str = PIGGY_URL) -> list[dict]:
    soup = BeautifulSoup(page, "html.parser")
    title_tag = soup.title
    title = title_tag.string.strip() if title_tag and title_tag.string else ""

    m = re.search(r'data-product_variations="(.*?)"', page, re.S)
    if not m:
        return []
    variations = json.loads(htmllib.unescape(m.group(1)))

    items = []
    for v in variations:
        attrs = v.get("attributes") or {}
        variant = " / ".join(str(x) for x in attrs.values() if x)
        price = v.get("display_price")
        item = {
            "shop": "豬寶窩窩",
            "title": title,
            "variant": variant,
            "price": None if price is None else str(price),
            "in_stock": bool(v.get("is_in_stock")),
            "url": url,
        }
        if _is_soft(item["title"], item["variant"]):
            items.append(item)
    return items


def fetch_piggybabies() -> list[dict]:
    return parse_piggybabies(_get(PIGGY_URL), PIGGY_URL)


# ── 魏啥麻（官方代理）─────────────────────────────────
def parse_weyyngbuy_product(page: str, url: str) -> list[dict]:
    """單一商品頁 → list[dict]。非軟纖商品回傳 []。"""
    soup = BeautifulSoup(page, "html.parser")
    title_tag = soup.title
    title = title_tag.string.strip() if title_tag and title_tag.string else ""
    if not _is_soft(title):
        return []

    items = []
    for span in soup.select(".select-variant span[data-value]"):
        variant = span.get("data-value", "").strip()
        in_stock = "sold-out" not in (span.get("class") or [])
        items.append({
            "shop": "魏啥麻",
            "title": title,
            "variant": variant,
            "price": None,
            "in_stock": in_stock,
            "url": url,
        })
    return items


def parse_weyyngbuy_category_ids(page: str) -> list[str]:
    return sorted(set(re.findall(r"/products/(Rabbit\w+)", page)))


def fetch_weyyngbuy() -> list[dict]:
    cat = _get(WEYYNG_CATEGORY)
    items = []
    for pid in parse_weyyngbuy_category_ids(cat):
        url = WEYYNG_PRODUCT.format(id=pid)
        items.extend(parse_weyyngbuy_product(_get(url), url))
    return items
