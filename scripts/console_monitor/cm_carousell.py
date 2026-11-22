"""旋轉拍賣：headless Firefox + cookies 過 Cloudflare，只回結構化卡片。"""
from __future__ import annotations

import glob
import os
import re
import shutil
import sqlite3
import tempfile
from typing import Any
from urllib.parse import quote

SEARCH = "https://tw.carousell.com/search/{}?sort_by=3"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

JS_CARDS = """() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/p/"]')) {
    const href = (a.getAttribute('href') || '').split('?')[0];
    if (!href || seen.has(href)) continue;
    seen.add(href);
    let card = a;
    while (card.parentElement && card.parentElement.querySelectorAll('a[href*="/p/"]').length === 1) {
      card = card.parentElement;
    }
    const text = (card.innerText || '').replace(/\\n+/g, ' | ');
    const price = text.match(/NT\\$\\s*([\\d,]+)/);
    const posted = text.match(/(\\d+\\s*(?:秒|分鐘|小時|天|週|個月|月|年)前|昨天|\\d+\\s+(?:second|minute|hour|day|week|month|year)s?\\s+ago|yesterday)/i);
    out.push({
      href: href,
      text: text,
      price_text: price ? ('NT$' + price[1]) : '',
      time_text: posted ? posted[1] : ''
    });
  }
  return out;
}"""


def firefox_cookies(domain: str = "carousell.com") -> list[dict[str, Any]]:
    dbs = glob.glob(os.path.expanduser(
        "~/Library/Application Support/Firefox/Profiles/*.default-release/cookies.sqlite"))
    if not dbs:
        return []
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    shutil.copy2(dbs[0], tmp)
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT name, value, host, path, isSecure FROM moz_cookies WHERE host LIKE ?",
            (f"%{domain}%",)).fetchall()
        con.close()
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return [{"name": n, "value": v, "domain": h, "path": p or "/", "secure": bool(s)}
            for n, v, h, p, s in rows]


def parse_card(text: str, href: str) -> dict[str, Any]:
    """卡片文字格式：卖家 | 時間 | 標題 | 價格 | 狀態 | 喜歡數。"""
    parts = [part.strip() for part in text.split("|") if part.strip()]
    price = re.search(r"NT\$\s*([\d,]+)", text)
    posted = re.search(
        r"(\d+\s*(?:秒|分鐘|小時|天|週|個月|月|年)前|昨天|"
        r"\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago|yesterday)",
        text, re.I)
    clean_href = href.split("?")[0]
    return {
        "pid": clean_href.rstrip("/").split("-")[-1],
        "seller": parts[0] if parts else "",
        "title": (parts[2] if len(parts) > 2 else "")[:120],
        "price_text": f"NT${price.group(1)}" if price else "",
        "time_text": posted.group(1) if posted else "",
        "url": f"https://tw.carousell.com{clean_href}",
    }


def fetch(query: str, key: str) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    cookies = firefox_cookies()
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context(user_agent=UA, locale="zh-TW")
        if cookies:
            context.add_cookies(cookies)
        page = context.new_page()
        page.goto(SEARCH.format(quote(query)), wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(4000)
        raw = page.evaluate(JS_CARDS)
        browser.close()

    return [parse_card(row.get("text") or "", row["href"]) for row in raw]
