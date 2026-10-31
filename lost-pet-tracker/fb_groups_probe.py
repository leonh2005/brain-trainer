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
    p.goto("https://www.facebook.com/search/groups?q=%E8%B5%B0%E5%A4%B1%20%E5%8D%94%E5%B0%8B",
           wait_until="domcontentloaded", timeout=45000)
    p.wait_for_timeout(5000)
    p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    p.wait_for_timeout(3000)
    links = p.eval_on_selector_all('a[href*="/groups/"]', "els => els.map(e => ({href:e.href, t:e.innerText.slice(0,40)}))")
    seen = set()
    print(f"社團連結 {len(links)} 個：")
    for l in links:
        m = re.search(r"/groups/(\d+|[^/?]+)", l["href"])
        if not m or m.group(1) in seen: continue
        seen.add(m.group(1))
        print(f"  {m.group(1):20} {l['t'].replace(chr(10),' ')[:35]}")
    print("body 前 400:", p.inner_text("body")[:400].replace("\n"," | "))
    b.close()
