"""個人智庫 — 抓取自己 Facebook 動態時報貼文，累積存進 personal-knowledge-base/facebook/。
用 Firefox cookies 帶入登入態（跟 notebooklm_cleanup.py 同一套手法），一天只跑一次降低帳號風控風險。
"""
import hashlib
import json
import os
import time

import browser_cookie3
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "..", "personal-knowledge-base", "facebook")
OUT_FILE = os.path.join(OUT_DIR, "posts.jsonl")
STATE_FILE = os.path.join(OUT_DIR, "seen_hashes.json")
COOKIE_FILE = "/Users/steven/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release/cookies.sqlite"
SCROLL_TIMES = 6


def _normalize_expires(expires):
    if not expires:
        return -1
    # browser_cookie3 在這個 Firefox 版本回傳的是毫秒 epoch，Playwright 要秒
    return expires / 1000 if expires > 1e12 else expires


def get_cookies():
    cj = browser_cookie3.firefox(cookie_file=COOKIE_FILE, domain_name="facebook.com")
    return [
        {
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "expires": _normalize_expires(c.expires),
            "secure": bool(c.secure),
        }
        for c in cj
    ]


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    seen = load_seen()
    new_posts = []

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(get_cookies())
        page = context.new_page()
        page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # FB 用虛擬滾動，捲太遠會把上面的 article 從 DOM 卸載，
        # 所以要邊捲邊抓，而不是捲完一次抓到底
        for _ in range(SCROLL_TIMES):
            # 留言串也用 role="article"，只取「沒有被其他 article 包住」的最外層貼文卡片，排除留言雜訊
            articles = page.query_selector_all('div[role="article"]')
            articles = [
                a for a in articles
                if a.evaluate("e => !e.parentElement.closest('div[role=\"article\"]')")
            ]
            for a in articles:
                text = (a.inner_text() or "").strip()
                if not text or len(text) < 5:
                    continue
                h = hashlib.sha256(text.encode()).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                new_posts.append({"scraped_at": int(time.time()), "hash": h, "text": text})
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(2000)

        browser.close()

    if new_posts:
        with open(OUT_FILE, "a") as f:
            for p_ in new_posts:
                f.write(json.dumps(p_, ensure_ascii=False) + "\n")
        save_seen(seen)

    print(f"新增 {len(new_posts)} 則貼文")


if __name__ == "__main__":
    main()
