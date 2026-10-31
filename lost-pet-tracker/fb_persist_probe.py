import os, sys, re
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tracker import load_cookies, CHROME

PROFILE_DIR = os.path.expanduser("~/.cache/lost-pet-chrome")

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        PROFILE_DIR, headless=True, executable_path=CHROME, locale="zh-TW",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900})
    ctx.add_cookies(load_cookies("facebook.com"))
    p = ctx.pages[0] if ctx.pages else ctx.new_page()
    p.goto("https://www.facebook.com/groups/528489597325122", wait_until="domcontentloaded", timeout=60000)
    p.wait_for_timeout(6000)
    print("URL:", p.url[:90])
    print("body 前 200:", p.inner_text("body")[:200].replace("\n"," | "))
    for _ in range(5):
        p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        p.wait_for_timeout(2500)
    arts = p.locator('div[role="article"]').count()
    print("article:", arts, "| body 長度:", len(p.inner_text("body")))
    blocks = p.eval_on_selector_all('div[role="article"]',
        "els => els.map(e => (e.innerText||'').slice(0,300))")
    for b in blocks[:4]:
        print("  --", b[:150].replace("\n"," / "))
    ctx.close()
