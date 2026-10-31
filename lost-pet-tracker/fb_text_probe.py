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
    for q in ["協尋 台北", "懸賞 走失"]:
        p.goto(f"https://www.facebook.com/search/posts?q={q.replace(' ','%20')}",
               wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(5000)
        for _ in range(5):
            p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            p.wait_for_timeout(2200)
        txt = p.inner_text("body")
        longs = [l.strip() for l in txt.split("\n") if len(l.strip()) > 40 and l.count("Facebook") < 3]
        print(f"=== {q} === body={len(txt)} 長句={len(longs)}")
        for l in longs[:6]:
            print("   ", l[:90])
    b.close()
