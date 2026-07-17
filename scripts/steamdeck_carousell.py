"""旋轉拍賣（Carousell）抓取 — headless Firefox 帶 cookie 過 Cloudflare
被 steamdeck_monitor.py 匯入；獨立可執行做測試"""
import glob
import os
import re
import shutil
import sqlite3
import tempfile

SEARCH_URL = 'https://tw.carousell.com/search/steam%20deck%20oled?sort_by=3'
MIN_PRICE = 15000
EXCLUDE = ('保護貼', '貼膜', '螢幕貼', '包膜', '收納', '背包', '皮套', '支架', '底座',
           '散熱', '握把', '搖桿套', '轉接', '擴充', '行動電源', '充電', '傳輸線',
           '果凍套', '矽膠套', '防塵', '貼紙', '模型', '記憶卡', 'microsd', 'sandisk',
           'claw', 'rog ally', 'legion go', 'switch')  # 排除配件與其他掌機


def _firefox_cookies(domain='carousell.com'):
    """從 default-release profile 讀 cookie（browser_cookie3 預設 profile 是空的）"""
    dbs = glob.glob(os.path.expanduser(
        '~/Library/Application Support/Firefox/Profiles/*.default-release/cookies.sqlite'))
    if not dbs:
        return []
    tmp = tempfile.mktemp(suffix='.sqlite')
    shutil.copy2(dbs[0], tmp)
    try:
        con = sqlite3.connect(f'file:{tmp}?mode=ro', uri=True)
        rows = con.execute(
            "SELECT name, value, host, path, isSecure FROM moz_cookies WHERE host LIKE ?",
            (f'%{domain}%',)).fetchall()
        con.close()
    finally:
        os.remove(tmp)
    return [{'name': n, 'value': v, 'domain': h, 'path': p or '/',
             'secure': bool(s)} for n, v, h, p, s in rows]


def _price(text):
    m = re.search(r'NT\$([\d,]+)', text)
    return int(m.group(1).replace(',', '')) if m else 0


def fetch():
    """回傳 {uid: {source,name,price,seller,url}}；headless 過 CF 抓搜尋頁"""
    from playwright.sync_api import sync_playwright
    out = {}
    cookies = _firefox_cookies('carousell.com')
    with sync_playwright() as p:
        browser = p.firefox.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
            locale='zh-TW')
        if cookies:
            ctx.add_cookies([{
                'name': c['name'], 'value': c['value'], 'domain': c['domain'],
                'path': c['path'], 'secure': c['secure']} for c in cookies])
        page = ctx.new_page()
        page.goto(SEARCH_URL, wait_until='domcontentloaded', timeout=40000)
        page.wait_for_timeout(4000)
        cards = page.evaluate("""() => {
            const r = [];
            document.querySelectorAll('a[href*="/p/"]').forEach(a => {
                const t = a.innerText.trim();
                if (t && /deck/i.test(t)) r.push({href:a.getAttribute('href'), text:t.replace(/\\n+/g,' | ')});
            });
            return [...new Map(r.map(c=>[c.href.split('?')[0],c])).values()];
        }""")
        browser.close()

    for c in cards:
        text = c['text']
        low = text.lower()
        if 'deck' not in low or 'oled' not in low:
            continue
        if 'sold' in low:                       # 已售出＝無庫存
            continue
        if any(w in low for w in EXCLUDE):
            continue
        price = _price(text)
        if price < MIN_PRICE:
            continue
        pid = c['href'].split('?')[0].rstrip('/').split('-')[-1]
        title = text.split(' | ')[0][:80]
        out[f'carousell_{pid}'] = {
            'source': '旋轉', 'name': title, 'price': f'${price:,}', 'seller': '',
            'url': f"https://tw.carousell.com{c['href'].split('?')[0]}",
        }
    return out


if __name__ == '__main__':
    for uid, it in fetch().items():
        print(uid, it['price'], it['name'])
