import os, sys
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import load_cookies, CHROME

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, executable_path=CHROME,
                           args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(locale="zh-TW",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    ctx.add_cookies(load_cookies("facebook.com"))
    p = ctx.new_page()
    p.goto("https://www.facebook.com/search/posts?q=%E5%8D%94%E5%B0%8B%20%E8%B5%B0%E5%A4%B1",
           wait_until="domcontentloaded", timeout=45000)
    p.wait_for_timeout(5000)
    print("URL:", p.url[:100])
    for sel in ['div[role="article"]', 'div[role="feed"]', 'a[href*="/posts/"]',
                'a[href*="story_fbid"]', 'a[href*="/permalink/"]']:
        print(f"  {sel}: {p.locator(sel).count()}")
    # 用 evaluate 滾到底再數
    for i in range(5):
        p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        p.wait_for_timeout(2500)
    print("滾動後：")
    for sel in ['div[role="article"]', 'a[href*="/posts/"]', 'a[href*="/permalink/"]']:
        print(f"  {sel}: {p.locator(sel).count()}")
    body = p.inner_text("body")
    print("body 長度:", len(body))
    print("body 前 600 字:", body[:600].replace("\n", " | "))
    print("是否被要求登入:", "登入" in body[:300] or "Log in" in body[:300])
    b.close()
