#!/usr/bin/env python3
"""蝦皮「置頂推廣」自動化 — 對指定商品點置頂推廣（免費功能，一次最多5個商品，冷卻4小時，從上次成功時間算起）。
登入方式：直接讀取系統 Firefox（ro7nczf2.default-release）的 shopee cookies，不走帳密登入流程。

排程策略：crontab 每小時跑一次當輪詢（安全網，容錯電腦睡眠/漏跑），
腳本內部用 last_success.json 記錄上次成功時間，未滿4小時直接跳過不開瀏覽器，
滿4小時才真的嘗試點擊，這樣即使排程是整點觸發，仍能貼齊「上次成功後滿4小時」的實際冷卻窗口。
"""
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('shopee_boost')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'last_success.json')
MISSING_STATE_FILE = os.path.join(BASE_DIR, 'data', 'missing.json')
FAILED_STATE_FILE = os.path.join(BASE_DIR, 'data', 'boost_failed.json')
COOLDOWN = timedelta(hours=4)
# cron 固定在 :19:00 觸發，但上次成功可能落在 :19:16，elapsed 會差十幾秒不滿 4 小時，
# 因而整整跳過一小時（歷史上一律變成 5 小時一輪）。給 60 秒容差視為已滿。
COOLDOWN_TOLERANCE = timedelta(seconds=60)

FIREFOX_PROFILE = os.path.expanduser(
    '~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release'
)
PRODUCT_URL = 'https://seller.shopee.tw/portal/product/list/live/all'
PRODUCTS = [
    '宜康寵物製氧機',  # 宜康寵物製氧機 3L，商品ID 46017697143
    'IBIYAYA',  # 強力磁吸扣版 IBIYAYA依比呀呀 愛的抱抱敞篷寵物背包，商品ID 54767799663
]

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip())
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def load_last_success() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, encoding='utf-8') as f:
        data = json.load(f)
    if 'last_success' in data:  # 舊格式（單商品）相容：搬進第一個商品名下
        return {PRODUCTS[0]: data['last_success']}
    return data


def save_last_success(all_success: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_success, f)


def load_missing() -> set:
    if not os.path.exists(MISSING_STATE_FILE):
        return set()
    with open(MISSING_STATE_FILE, encoding='utf-8') as f:
        return set(json.load(f))


def save_missing(missing: set):
    os.makedirs(os.path.dirname(MISSING_STATE_FILE), exist_ok=True)
    with open(MISSING_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(sorted(missing), f)


def load_failed() -> set:
    if not os.path.exists(FAILED_STATE_FILE):
        return set()
    with open(FAILED_STATE_FILE, encoding='utf-8') as f:
        return set(json.load(f))


def save_failed(failed: set):
    os.makedirs(os.path.dirname(FAILED_STATE_FILE), exist_ok=True)
    with open(FAILED_STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(sorted(failed), f)


def notify(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning('未設定 Telegram，略過通知')
        return
    import requests
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': msg}, timeout=10,
        )
    except Exception as e:
        logger.error(f'Telegram 通知失敗：{e}')


def load_shopee_cookies() -> list:
    """複製 Firefox cookies.sqlite（避免鎖檔衝突）並取出 shopee 相關 cookie，轉成 Playwright 格式。"""
    src = os.path.join(FIREFOX_PROFILE, 'cookies.sqlite')
    with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as tmp:
        tmp_path = tmp.name
    shutil.copy(src, tmp_path)
    conn = sqlite3.connect(tmp_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT host, name, value, path, expiry, isSecure, isHttpOnly "
        "FROM moz_cookies WHERE host LIKE '%shopee%'"
    ).fetchall()
    conn.close()
    os.unlink(tmp_path)

    cookies = []
    for r in rows:
        cookies.append({
            'name': r['name'],
            'value': r['value'],
            'domain': r['host'],
            'path': r['path'] or '/',
            'secure': bool(r['isSecure']),
            'httpOnly': bool(r['isHttpOnly']),
            'expires': r['expiry'] / 1000 if r['expiry'] and r['expiry'] > 0 else -1,
        })
    return cookies


def dismiss_ai_tour(page) -> bool:
    """關閉蝦皮在商品列表頁推出的「AI 商品優化」導覽浮層（#aiOptimizerTour）。

    浮層外層容器本身回報不可見，但子元素 .step-mask／.step-container 是可見的、
    覆蓋整個商品列表並攔截所有點擊，害「更多」按鈕點不到而 TimeoutError。
    按「跳過」後浮層節點會整個消失。找不到浮層時什麼都不做。
    """
    skip_btn = page.locator('#aiOptimizerTour button:has-text("跳過")')
    if skip_btn.count() == 0:
        return False
    # 浮層可能在 count() 與 click() 之間消失，此時預設的 30 秒重試會拋錯並中止整輪，
    # 所以縮短 timeout 且吞掉例外——這裡只是清理動作，失敗不該讓整輪掛掉。
    try:
        skip_btn.first.click(timeout=5000)
    except Exception as e:
        logger.warning(f'關閉「AI 商品優化」導覽浮層失敗，略過：{e}')
        return False
    logger.info('已關閉「AI 商品優化」導覽浮層')
    page.wait_for_timeout(500)
    return True


def _is_boost_countdown(text) -> bool:
    """判斷「置頂推廣」選單文字是否為冷卻倒數（HH:MM:SS）＝已置頂生效。"""
    return bool(re.search(r'\d{2}:\d{2}:\d{2}', text or ''))


def _open_boost_item(page, action_table, target_idx):
    """開指定列的「更多」選單，回傳「置頂推廣」項目的 locator（找不到回 None）。

    先按 Escape 關掉任何殘留選單再開新的。只能找「目前彈出、真的看得到」的那個選單
    ——頁面上其他列隱藏的選單模板也含同樣字串，整頁搜尋會永遠誤判成冷卻中。
    """
    page.keyboard.press('Escape')
    page.wait_for_timeout(400)
    dismiss_ai_tour(page)
    try:
        action_table.locator('tbody tr').nth(target_idx).locator('button:has-text("更多")').click()
    except Exception as e:
        logger.warning(f'開「更多」選單失敗（略過此商品）：{e}')
        return None
    page.wait_for_timeout(800)
    item = page.locator('ul.eds-dropdown-menu:visible li:has-text("置頂推廣")').first
    return item if item.count() else None


def _open_product_list(page):
    page.goto(PRODUCT_URL, wait_until='domcontentloaded', timeout=30000)
    page.wait_for_timeout(3000)
    dismiss_ai_tour(page)


def _find_tables(page):
    """回傳（商品資訊表, 操作按鈕表），找不到回 (None, None)。"""
    # 商品資訊表與操作按鈕表是兩張分開的 <table>（同步捲動用，欄位用 row index 對齊）
    # 頁面偶爾要超過3秒才把表格資料載完（曾偶發誤判成「頁面結構改變」），改成輪詢最多15秒再放棄
    for _ in range(15):
        info_table = None
        action_table = None
        tables = page.locator('table')
        for i in range(tables.count()):
            t = tables.nth(i)
            if t.locator('tbody tr').count() == 0:
                continue
            txt = t.inner_text() or ''
            if info_table is None and '商品 ID' in txt:
                info_table = t
            if action_table is None and '更多' in txt:
                action_table = t
        if info_table is not None and action_table is not None:
            return info_table, action_table
        page.wait_for_timeout(1000)
    return None, None


def _locate_product(page, keyword):
    """回傳（操作按鈕表, 商品列 index）；表格找不到回 (None, None)，商品找不到回 (表格, None)。"""
    info_table, action_table = _find_tables(page)
    if info_table is None:
        return None, None
    info_rows = info_table.locator('tbody tr')
    for i in range(info_rows.count()):
        if keyword in (info_rows.nth(i).inner_text() or ''):
            return action_table, i
    return action_table, None


def run():
    all_success = load_last_success()
    now = datetime.now()
    pending = []
    for keyword in PRODUCTS:
        last = all_success.get(keyword)
        if last is not None:
            elapsed = now - datetime.fromisoformat(last)
            if elapsed < COOLDOWN - COOLDOWN_TOLERANCE:
                logger.info(f'「{keyword}」距上次成功僅 {elapsed}，未滿4小時，跳過（還需等 {COOLDOWN - elapsed}）')
                continue
        pending.append(keyword)
    if not pending:
        return

    cookies = load_shopee_cookies()
    if not any(c['name'] == 'SPC_ST' for c in cookies):
        notify('⚠️ 蝦皮置頂推廣：Firefox 找不到有效登入 session（SPC_ST cookie 不存在），請重新登入蝦皮賣家中心')
        logger.error('無 SPC_ST cookie，中止')
        return

    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto(PRODUCT_URL, wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(3000)

        if '登入' in page.title() or 'login' in page.url:
            notify('⚠️ 蝦皮置頂推廣：cookie 已失效，被導回登入頁，請重新用 Firefox 登入蝦皮賣家中心')
            logger.error('cookie 失效，被導回登入頁')
            browser.close()
            return

        dismiss_ai_tour(page)

        missing_now = set()
        failed_now = set()
        for n, keyword in enumerate(pending):
            # 每個商品都重新載入頁面：上一個商品的下拉選單關不掉（Escape 無效），會攔截下一個商品的「更多」點擊
            if n > 0:
                _open_product_list(page)
            action_table, target_idx = _locate_product(page, keyword)
            if action_table is None:
                notify('⚠️ 蝦皮置頂推廣：頁面結構改變，找不到商品表格或操作表格')
                logger.error('找不到商品表格或操作表格')
                break
            if target_idx is None:
                missing_now.add(keyword)
                logger.warning(f'商品列表找不到「{keyword}」，可能尚未過審／已下架，跳過')
                continue

            # 上一輪的下拉選單若還沒關閉，會蓋住這一行的「更多」按鈕導致點擊被攔截，
            # _open_boost_item 會先 Escape 清掉殘留選單。
            boost_item = _open_boost_item(page, action_table, target_idx)
            if boost_item is None:
                logger.warning(f'「{keyword}」下拉選單找不到「置頂推廣」選項，UI可能改版了，跳過')
                continue

            item_text = boost_item.inner_text()
            if '點我置頂推廣' not in item_text:
                m = re.search(r'(\d{2}:\d{2}:\d{2})', item_text)
                remain = m.group(1) if m else '未知'
                logger.info(f'「{keyword}」置頂推廣冷卻中（頁面偵測），剩餘 {remain}')
                page.keyboard.press('Escape')
                continue

            boost_item.click()
            page.wait_for_timeout(2000)

            # 關鍵：點擊後必須回讀選單確認真的進入冷卻倒數，否則會「假成功」——
            # Shopee 有時不受理這次點擊，但 UI 不報錯、API 也照樣回 success，
            # 只憑「已點擊」記成功會讓本地狀態與 Shopee 實際脫節（log 說成功、商品卻沒置頂）。
            # 必須重新載入後再回讀：同頁回讀會誤判（2026-10-24 實測，點擊已生效卻回讀為 None）。
            _open_product_list(page)
            action_table, target_idx = _locate_product(page, keyword)
            verify = _open_boost_item(page, action_table, target_idx) if target_idx is not None else None
            verify_text = verify.inner_text() if verify is not None else None
            page.keyboard.press('Escape')
            if _is_boost_countdown(verify_text):
                all_success[keyword] = datetime.now().isoformat()
                save_last_success(all_success)
                logger.info(f'「{keyword}」置頂成功，已確認冷卻倒數：{verify_text.strip()}')
                notify(f'✅ 蝦皮置頂推廣：「{keyword}」已成功置頂，下次約4小時後再試')
            else:
                failed_now.add(keyword)
                logger.warning(
                    f'「{keyword}」點擊後未生效，回讀選單文字仍為 {verify_text!r}，不記錄成功（下一輪重試）')

            page.wait_for_timeout(300)

        # 只在「新變成找不到」時通知一次，避免已下架商品每小時洗版
        newly_missing = missing_now - load_missing()
        if newly_missing:
            notify('⚠️ 蝦皮置頂推廣：找不到以下商品（可能已下架／未過審），本輪已跳過：'
                   + '、'.join(sorted(newly_missing)) + '。若已賣出請更新 boost.py 的 PRODUCTS 清單。')
        save_missing(missing_now)

        # 置頂失敗只在「新變成失敗」時通知一次，避免 Shopee 持續不受理時每小時洗版；
        # 失敗的商品不會更新 last_success，下一輪 cron 會自動重試，成功後即從此清單移除。
        newly_failed = failed_now - load_failed()
        if newly_failed:
            notify('⚠️ 蝦皮置頂推廣：以下商品已點擊但 Shopee 未受理（會自動重試）：'
                   + '、'.join(sorted(newly_failed)))
        save_failed(failed_now)

        browser.close()


if __name__ == '__main__':
    try:
        run()
    except Exception as e:
        logger.exception('執行失敗')
        notify(f'🔴 蝦皮置頂推廣執行失敗：{e}')
