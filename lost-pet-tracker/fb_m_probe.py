import os, sys
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import load_cookies, CHROME

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, executable_path=CHROME,
                           args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(locale="zh-TW",
        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                   "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        viewport={"width": 430, "height": 932}, is_mobile=True, has_touch=True)
    ctx.add_cookies(load_cookies("facebook.com"))
    p = ctx.new_page()
    for url in ["https://m.facebook.com/search/posts/?q=%E5%8D%94%E5%B0%8B",
                "https://m.facebook.com/search/posts/?q=%E8%B5%B0%E5%A4%B1"]:
        p.goto(url, wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(5000)
        for _ in range(3):
            p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            p.wait_for_timeout(2000)
        txt = p.inner_text("body")
        print(f"[{url[-20:]}] len={len(txt)} 協尋={txt.count('協尋')} 走失={txt.count('走失')} 懸賞={txt.count('懸賞')}")
        print("   ", txt[:300].replace("\n", " | "))
    b.close()
