import os, sys, re
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import load_cookies, CHROME

urls = [
    ("搜尋預設", "https://www.threads.com/search?q=%E5%8D%94%E5%B0%8B%20%E7%8B%97"),
    ("tag 頁", "https://www.threads.com/tag/%E5%8D%94%E5%B0%8B"),
    ("搜尋 recent", "https://www.threads.com/search?q=%E5%8D%94%E5%B0%8B%20%E7%8B%97&serp_type=recent"),
]
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, executable_path=CHROME,
                           args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(locale="zh-TW",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    ctx.add_cookies(load_cookies("threads.com"))
    p = ctx.new_page()
    for label, url in urls:
        try:
            p.goto(url, wait_until="domcontentloaded", timeout=45000)
            p.wait_for_timeout(4500)
            for _ in range(4):
                p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                p.wait_for_timeout(2000)
            arts = p.locator('article, div[data-pressable-container]').count()
            txt = p.inner_text("body")
            dates = re.findall(r"(20\d{2}-\d{1,2}-\d{1,2}|\d+\s*(?:小時|天|分鐘)前)", txt)
            print(f"[{label}] article={arts} body={len(txt)} 日期樣本={dates[:6]}")
        except Exception as e:
            print(f"[{label}] ERR {type(e).__name__} {str(e)[:60]}")
    b.close()
