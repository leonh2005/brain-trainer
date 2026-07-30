"""每兩週清理 NotebookLM：刪除較舊的一半 notebook。
用 Firefox cookies 帶入 Google 登入態，NotebookLM 預設清單已依「最新」排序，
所以直接從清單尾端（最舊）刪起，刪 count//2 個即可。
"""
import json
import os
import time

import browser_cookie3
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "notebooklm_cleanup_state.json")
COOKIE_FILE = "/Users/steven/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release/cookies.sqlite"
MENU_SEL = "button[aria-label*='more'], button:has-text('more_vert')"
MIN_INTERVAL_DAYS = 13  # cron 每週觸發一次，這裡自行把關實際每 2 週才動手


def get_cookies():
    cj = browser_cookie3.firefox(cookie_file=COOKIE_FILE, domain_name="google.com")
    out = []
    for c in cj:
        expires = c.expires if c.expires else -1
        if expires > 10_000_000_000:
            expires = expires / 1000
        out.append({
            "name": c.name, "value": c.value, "domain": c.domain, "path": c.path,
            "expires": expires, "httpOnly": False, "secure": bool(c.secure), "sameSite": "Lax",
        })
    return out


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"last_run": 0}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def due(state):
    return time.time() - state.get("last_run", 0) >= MIN_INTERVAL_DAYS * 86400


def open_page(context):
    page = context.new_page()
    page.goto("https://notebooklm.google.com/", wait_until="load", timeout=30000)
    page.wait_for_timeout(6000)
    try:
        page.get_by_role("button", name="開始使用").click(timeout=5000)
        page.wait_for_timeout(1000)
    except Exception:
        pass
    for _ in range(20):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(300)
    return page


def real_count(page):
    page.reload(wait_until="load", timeout=30000)
    page.wait_for_timeout(4000)
    for _ in range(20):
        page.mouse.wheel(0, 2000)
        page.wait_for_timeout(300)
    return page.locator(MENU_SEL).count()


def delete_last(page):
    page.locator(MENU_SEL).last.click()
    page.wait_for_timeout(500)
    page.get_by_role("menuitem", name="刪除").click(timeout=5000)
    page.wait_for_timeout(500)
    page.get_by_role("button", name="Delete").click(timeout=5000)
    page.wait_for_timeout(1500)


def main():
    state = load_state()
    if not due(state):
        print(f"距上次執行未滿 {MIN_INTERVAL_DAYS} 天，跳過")
        return

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(get_cookies())
        page = open_page(context)

        total = real_count(page)
        target = total // 2
        print(f"目前共 {total} 個，計畫刪除較舊的 {target} 個")

        deleted = 0
        failures = 0
        while deleted < target:
            try:
                delete_last(page)
                deleted += 1
                failures = 0
            except Exception as e:
                failures += 1
                print(f"第 {deleted + 1} 個刪除失敗：{e}")
                page.keyboard.press("Escape")
                page.wait_for_timeout(800)
                if failures > 5:
                    print("連續失敗過多，中止本次清理")
                    break

        final = real_count(page)
        print(f"=== 完成：刪除 {deleted} 個，剩餘 {final} 個 ===")
        browser.close()

    state["last_run"] = time.time()
    save_state(state)


if __name__ == "__main__":
    main()
