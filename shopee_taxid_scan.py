"""
蝦皮「高營業額 + 未揭露統編」賣家掃描（保守版）

背景：
  蝦皮搜尋 API 有 anti-bot，實測會 tarpit（不回應也不拒絕，請求直接掛住），
  且行為不可預測——同樣的程式碼時好時壞。單次成功不代表能連續跑。
  因此本腳本採「寧慢勿擋」策略：

    - 每個關鍵字開一個全新 browser session，只發一次請求
    - 關鍵字之間間隔 GAP 秒（預設 300）
    - 失敗自動重試（最多 RETRY 次），成功即跳過
    - 每個關鍵字的结果立刻寫入斷點檔，中途掛掉不會全部重來

用法：
    python3 shopee_taxid_scan.py                # 預設間隔 300 秒
    python3 shopee_taxid_scan.py --gap 120      # 改間隔
    python3 shopee_taxid_scan.py --keywords 寵物用品 手機殼

注意：
  賣場名沒寫統編 ≠ 未辦稅籍登記。很多合法登記的賣家也不在店名放統編。
  產出僅為「未揭露統編」的線索名單，需人工查證。

依賴：selenium / geckodriver / requests，以及一份已登入蝦皮的 Firefox profile。
"""
import argparse
import json
import os
import re
import sys
import time

import requests
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.firefox.options import Options

PROFILE = os.path.expanduser(
    '~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release'
)
STATE = os.path.expanduser('~/CCProject/.shopee_taxid_state.json')
OUT = os.path.expanduser('~/CCProject/shopee_taxid_result.json')

DEFAULT_KEYWORDS = [
    '寵物用品', '手機殼', '保養品', '女裝',
    '零食', '收納', '藍牙耳機', '居家清潔',
]
GAP_DEFAULT = 300      # 每個關鍵字之間的間隔(秒)
RETRY_DEFAULT = 2      # 每個關鍵字失敗後重試次數
LIMIT = 60             # 單次搜尋筆數；太高實測容易被 tarpit，先用 60 觀察
TIMEOUT = 45           # 單次請求最長等待(秒)


def search_once(keyword):
    """開一個新 session，對搜尋 API 發一次請求。回傳 item_basic 清單或 None。"""
    opts = Options()
    opts.add_argument('--headless')
    opts.profile = PROFILE
    driver = webdriver.Firefox(options=opts)
    driver.set_window_size(1440, 900)
    driver.set_page_load_timeout(TIMEOUT)
    driver.set_script_timeout(TIMEOUT)
    try:
        driver.get('https://shopee.tw/')
        time.sleep(5)
        url = ('/api/v4/search/search_items?by=relevancy&keyword='
               + requests.utils.quote(keyword)
               + f'&limit={LIMIT}&newest=0&order=desc&page_type=search&version=2')
        raw = driver.execute_async_script("""
          const cb = arguments[0], u = arguments[1];
          fetch(u, {credentials: 'include'})
            .then(x => x.text())
            .then(t => cb(t))
            .catch(() => cb(''));
        """, url)
        if not raw or not raw.strip().startswith('{'):
            return None
        data = json.loads(raw)
        if not data.get('items'):
            return None
        return [i['item_basic'] for i in data['items']]
    except (WebDriverException, Exception):
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {'collected': {}}


def save_state(state):
    with open(STATE, 'w') as f:
        json.dump(state, f, ensure_ascii=False)


def find_taxid(name):
    m = re.search(r'(?<!\d)(\d{8})(?!\d)', name)
    return m.group(1) if m else None


def lookup_g0v(taxid):
    try:
        d = requests.get(f'https://company.g0v.ronny.tw/api/show/{taxid}',
                         timeout=15).json().get('data', {})
        return d.get('商業名稱') or d.get('公司名稱'), d.get('登記現況') or d.get('現況')
    except Exception:
        return None, None


def aggregate(state):
    shops = {}
    for kw, items in state['collected'].items():
        for it in items:
            sid = it.get('shopid')
            if not sid:
                continue
            price = (it.get('price') or 0) / 100000
            sold = it.get('sold') or 0
            s = shops.setdefault(sid, {
                'shopid': sid, 'name': it.get('shop_name', ''),
                'loc': it.get('shop_location', ''),
                'month_rev': 0.0, 'hist_rev': 0.0, 'n': 0,
                'verified': it.get('shopee_verified'),
                'official': it.get('is_official_shop'),
            })
            s['month_rev'] += price * sold
            s['hist_rev'] += price * (it.get('historical_sold') or 0)
            s['n'] += 1

    if shops and sum(s['month_rev'] for s in shops.values()) == 0:
        for s in shops.values():
            s['month_rev'] = s['hist_rev']

    for s in shops.values():
        s['taxid'] = find_taxid(s['name'])
        s['g0v_name'] = s['g0v_status'] = None
        if s['taxid']:
            s['g0v_name'], s['g0v_status'] = lookup_g0v(s['taxid'])
    return sorted(shops.values(), key=lambda x: -x['month_rev'])


def report(ranked):
    no_id = [s for s in ranked if not s['taxid']]
    print(f'\n{"=" * 74}')
    print(f'高營業額 + 未揭露統編：{len(no_id)} 家')
    print(f'{"=" * 74}')
    for i, s in enumerate(no_id[:30], 1):
        tag = '官方' if s.get('official') else ('優選' if s.get('verified') else '')
        print(f"{i:2}. {s['name'][:34]:36} [{tag}]")
        print(f"    月營業額估 NT${s['month_rev']:>12,.0f}"
              f"｜累計 NT${s['hist_rev']:>14,.0f}｜{s['loc']}｜搜到{s['n']}件")

    print(f'\n{"=" * 74}')
    print('對照：有揭露統編的賣場（g0v 驗證）')
    print(f'{"=" * 74}')
    for s in ranked:
        if s['taxid']:
            print(f"  {s['name'][:32]:34} {s['taxid']} → "
                  f"{s.get('g0v_name') or '(g0v 查無此統編)'} {s.get('g0v_status', '')}")

    print('\n提醒：未揭露統編 ≠ 未辦稅籍登記，僅為待查證線索。')
    with open(OUT, 'w') as f:
        json.dump(ranked, f, ensure_ascii=False, indent=1)
    print(f'完整資料: {OUT}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--gap', type=int, default=GAP_DEFAULT,
                    help=f'關鍵字間隔秒數（預設 {GAP_DEFAULT}）')
    ap.add_argument('--retry', type=int, default=RETRY_DEFAULT,
                    help=f'每字重試次數（預設 {RETRY_DEFAULT}）')
    ap.add_argument('--keywords', nargs='*', default=DEFAULT_KEYWORDS)
    args = ap.parse_args()

    if not os.path.isdir(PROFILE):
        sys.exit(f'找不到 Firefox profile: {PROFILE}')

    state = load_state()
    todo = [k for k in args.keywords if k not in state['collected']]
    print(f'待抓關鍵字 {len(todo)}/{len(args.keywords)}（已有 {len(state["collected"])} 個完成）')

    for kw in todo:
        got = None
        for attempt in range(args.retry + 1):
            print(f'  [{kw}] 第 {attempt + 1} 次嘗試...', flush=True)
            items = search_once(kw)
            if items:
                got = items
                break
            print(f'  [{kw}] 失敗（被擋或逾時）', flush=True)
            if attempt < args.retry:
                print(f'  [{kw}] 等 {args.gap}s 後重試', flush=True)
                time.sleep(args.gap)
        if got:
            state['collected'][kw] = got
            save_state(state)
            print(f'  [{kw}] ✓ 取得 {len(got)} 筆（已存斷點）', flush=True)
        else:
            print(f'  [{kw}] ✗ 放棄', flush=True)
        time.sleep(args.gap)

    if not state['collected']:
        sys.exit('完全取不到資料，風控可能仍未解除，建議改天再試。')

    report(aggregate(state))


if __name__ == '__main__':
    main()
