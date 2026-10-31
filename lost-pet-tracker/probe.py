"""可行性探測：用 Firefox 登入 cookie 在 headless Chromium 抓 FB/Threads/PTT"""
import os
import sqlite3

from playwright.sync_api import sync_playwright

PROFILE = os.path.expanduser(
    "~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release/cookies.sqlite"
)


def load_cookies(host_like):
    con = sqlite3.connect(f"file:{PROFILE}?immutable=1", uri=True)
    rows = con.execute(
        "SELECT host,name,value,path,isSecure,isHttpOnly FROM moz_cookies WHERE host LIKE ?",
        (f"%{host_like}%",),
    ).fetchall()
    con.close()
    return [
        {
            "name": n, "value": v,
            "domain": h if h.startswith(".") else "." + h,
            "path": p, "secure": bool(s), "httpOnly": bool(hh),
        }
        for h, n, v, p, s, hh in rows
    ]


with sync_playwright() as pw:
    browser = pw.chromium.launch(
        headless=True,
        executable_path=os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ),
        args=["--disable-blink-features=AutomationControlled"],
    )
    ctx = browser.new_context(
        locale="zh-TW",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    ctx.add_cookies(load_cookies("threads.com") + load_cookies("facebook.com"))

    page = ctx.new_page()
    for label, url in [
        ("Threads 搜尋 協尋", "https://www.threads.com/search?q=%E5%8D%94%E5%B0%8B"),
        ("FB 搜尋 協尋", "https://www.facebook.com/search/top?q=%E5%8D%94%E5%B0%8B"),
    ]:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            text = page.inner_text("body")
            print(f"[{label}] url={page.url[:70]} 文字長度={len(text)} "
                  f"協尋={text.count('協尋')} 走失={text.count('走失')} 懸賞={text.count('懸賞')}")
            print("  開頭:", text[:150].replace("\n", " / "))
        except Exception as e:
            print(f"[{label}] ERR {type(e).__name__}: {str(e)[:90]}")
    browser.close()
