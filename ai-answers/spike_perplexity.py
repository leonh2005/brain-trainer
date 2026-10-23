import sys
import time
from urllib.parse import quote

from playwright.sync_api import sync_playwright

Q = sys.argv[1] if len(sys.argv) > 1 else "台灣的首都是哪裡？用一句話回答"
PROFILE = "/Users/steven/CCProject/ai-answers/profile"

with sync_playwright() as p:
    ctx = p.firefox.launch_persistent_context(PROFILE, headless=True)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(f"https://www.perplexity.ai/search?q={quote(Q)}", wait_until="domcontentloaded")
    t0 = time.time()
    last, stable = "", 0
    while time.time() - t0 < 60:
        time.sleep(1.5)
        html = page.content()
        txt = page.inner_text("body")
        print(f"[{time.time()-t0:4.1f}s] title={page.title()!r} len={len(txt)}")
        if txt == last:
            stable += 1
            if stable >= 3:
                break
        else:
            stable, last = 0, txt
    page.screenshot(path="/Users/steven/CCProject/ai-answers/debug/perplexity.png")
    open("/Users/steven/CCProject/ai-answers/debug/perplexity.html", "w").write(page.content())
    print("=== BODY TEXT (first 1500) ===")
    print(last[:1500])
    ctx.close()
