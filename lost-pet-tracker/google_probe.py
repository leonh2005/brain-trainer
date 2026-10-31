import os, sys, re
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import CHROME

queries = [
    ("FB 近一週", "https://www.google.com/search?q=site%3Afacebook.com+%E5%8D%94%E5%B0%8B+%E8%B5%B0%E5%A4%B1+%E6%87%B8%E8%B3%9E&hl=zh-TW&gl=tw&tbs=qdr:w"),
    ("FB 近一月", "https://www.google.com/search?q=site%3Afacebook.com+%E5%8D%94%E5%B0%8B+%E8%B5%B0%E5%A4%B1+%E5%8F%B0%E5%8C%97&hl=zh-TW&gl=tw&tbs=qdr:m"),
]
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, executable_path=CHROME,
                           args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(locale="zh-TW",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
    p = ctx.new_page()
    for label, url in queries:
        p.goto(url, wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(3500)
        txt = p.inner_text("body")
        blocked = any(w in txt[:400] for w in ["unusual traffic", "自動化", "CAPTCHA", "驗證"])
        hits = p.eval_on_selector_all('a[href*="facebook.com"]',
            "els => els.map(e => e.href).filter(h => h.includes('facebook.com'))")
        print(f"[{label}] 被封={blocked} 結果={len(hits)} body={len(txt)}")
        for h in hits[:4]:
            print("   ", h[:100])
        if blocked:
            print("   body:", txt[:200].replace("\n", " "))
    b.close()
