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
import sqlite3
import shutil
import tempfile
from datetime import datetime, timedelta

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('shopee_boost')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'last_success.json')
COOLDOWN = timedelta(hours=4)

FIREFOX_PROFILE = os.path.expanduser(
    '~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release'
)
PRODUCT_URL = 'https://seller.shopee.tw/portal/product/list/live/all'
PRODUCT_NAME_KEYWORD = '恩雅'  # 全新恩雅 Enya Inspire電吉他 特價
PRODUCT_ID = '57064166103'

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def load_last_success():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding='utf-8') as f:
        data = json.load(f)
    return datetime.fromisoformat(data['last_success'])


def save_last_success(dt: datetime):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'last_success': dt.isoformat()}, f)


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


def run():
    last_success = load_last_success()
    if last_success is not None:
        elapsed = datetime.now() - last_success
        if elapsed < COOLDOWN:
            remain = COOLDOWN - elapsed
            logger.info(f'距上次成功僅 {elapsed}，未滿4小時，跳過（還需等 {remain}）')
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

        # 商品資訊表與操作按鈕表是兩張分開的 <table>（同步捲動用，欄位用 row index 對齊）
        tables = page.locator('table')
        info_table = None
        action_table = None
        for i in range(tables.count()):
            t = tables.nth(i)
            if t.locator('tbody tr').count() == 0:
                continue
            txt = t.inner_text() or ''
            if info_table is None and ('商品 ID' in txt or PRODUCT_NAME_KEYWORD in txt):
                info_table = t
            if action_table is None and '更多' in txt:
                action_table = t
        if info_table is None or action_table is None:
            notify('⚠️ 蝦皮置頂推廣：頁面結構改變，找不到商品表格或操作表格')
            logger.error(f'info_table found={info_table is not None} action_table found={action_table is not None}')
            browser.close()
            return

        info_rows = info_table.locator('tbody tr')
        target_idx = None
        for i in range(info_rows.count()):
            if PRODUCT_NAME_KEYWORD in (info_rows.nth(i).inner_text() or ''):
                target_idx = i
                break
        if target_idx is None:
            notify(f'⚠️ 蝦皮置頂推廣：商品列表找不到「{PRODUCT_NAME_KEYWORD}」，可能已下架或改名')
            logger.error('找不到目標商品列')
            browser.close()
            return

        more_btn = action_table.locator('tbody tr').nth(target_idx).locator('button:has-text("更多")')
        more_btn.click()
        page.wait_for_timeout(800)

        # 「置頂推廣」在下拉選單裡；頁面上其他商品列的隱藏選單模板也含「已被全部使用」提示字串，
        # 所以只能在「目前彈出來、真的看得到的那個選單」裡找，不能整頁原始碼搜尋（否則永遠誤判成冷卻中）
        visible_menu = page.locator('ul.eds-dropdown-menu:visible')
        boost_item = visible_menu.locator('li:has-text("置頂推廣")').first
        if boost_item.count() == 0:
            notify('⚠️ 蝦皮置頂推廣：下拉選單找不到「置頂推廣」選項，UI可能改版了')
            logger.error('找不到置頂推廣選項，UI可能改版')
            browser.close()
            return

        item_text = boost_item.inner_text()
        if '點我置頂推廣' not in item_text:
            m = re.search(r'(\d{2}:\d{2}:\d{2})', item_text)
            remain = m.group(1) if m else '未知'
            logger.info(f'置頂推廣冷卻中（頁面偵測），剩餘 {remain}')
            browser.close()
            return

        boost_item.click()
        page.wait_for_timeout(1500)
        save_last_success(datetime.now())
        logger.info('已點擊置頂推廣，記錄成功時間')
        notify(f'✅ 蝦皮置頂推廣：「{PRODUCT_NAME_KEYWORD}」已成功置頂，下次約4小時後再試')
        browser.close()


if __name__ == '__main__':
    try:
        run()
    except Exception as e:
        logger.exception('執行失敗')
        notify(f'🔴 蝦皮置頂推廣執行失敗：{e}')
