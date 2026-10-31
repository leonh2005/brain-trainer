import os, sys, re
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
    for gid in ["528489597325122", "648807822465616"]:
        p.goto(f"https://www.facebook.com/groups/{gid}", wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(5000)
        for _ in range(3):
            p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            p.wait_for_timeout(2500)
        name = p.inner_text("body")[:80].replace("\n", " ")
        arts = p.locator('div[role="article"]').count()
        posts = p.eval_on_selector_all(
            'div[role="article"]',
            "els => els.map(e => ({href: (e.querySelector('a[href*=\"/posts/\"],a[href*=\"permalink\"]')||{}).href||'', text: (e.innerText||'').slice(0,200)}))"
        )
        print(f"[{gid}] {name} | article={arts}")
        for x in posts[:3]:
            print("   ", (x['href'] or 'no-link')[-40:], "|", x['text'][:70].replace("\n"," "))
    b.close()
