#!/usr/bin/env python3
"""Steam Deck OLED 二手監控 — 露天 + PTT，每天掃一次，有新標的（有庫存）推 Telegram"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime

QUERY = 'steam deck oled'
MIN_PRICE = 15000          # 露天：只看 15000 以上（濾掉配件/仿品）
PTT_BOARDS = ('Gamesale', 'PC_Shopping')
EXCLUDE = ('適用', '保護貼', '貼膜', '螢幕貼', '包膜', '收納包', '背包', '皮套',
           '支架', '底座', '散熱', '握把', '搖桿套', '轉接', '擴充塢', '行動電源',
           '充電線', '傳輸線', '果凍套', '矽膠套', '防塵', '貼紙', '模型', '公仔')
PTT_SOLD = ('已售', '完售', '售完', '結標', '售出', '已結', '交易完成', '謝謝')

SEEN_FILE = os.path.expanduser('~/CCProject/scripts/steamdeck_seen.json')
CURRENT_FILE = os.path.expanduser('~/CCProject/scripts/steamdeck_current.json')  # 供儀表板讀
LOG_FILE = os.path.expanduser('~/CCProject/logs/steamdeck_monitor.log')
BOT_TOKEN = open(os.path.expanduser('~/CCProject/.secrets/telegram_token.txt')).read().strip()
CHAT_ID = '7556217543'


def _get(url, headers=None):
    h = {'User-Agent': 'Mozilla/5.0'}
    if headers:
        h.update(headers)
    last = None
    for _ in range(3):                       # 偶發 connection reset → 重試
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20).read()
        except Exception as e:
            last = e
    raise last


def _get_json(url):
    return json.loads(_get(url))


# ── 露天 ──────────────────────────────────────────
def fetch_ruten():
    q = urllib.parse.quote(QUERY)
    search = _get_json(f'https://rtapi.ruten.com.tw/api/search/v3/index.php/core/prod'
                       f'?q={q}&sort=new/dc&limit=60')
    ids = [r['Id'] for r in search.get('Rows', [])]
    items = []
    for i in range(0, len(ids), 30):
        detail = _get_json(f'https://rapi.ruten.com.tw/api/items/v2/list'
                           f'?gno={",".join(ids[i:i + 30])}&level=simple')
        items.extend(detail.get('data', []))

    out = {}
    for it in items:
        name = it.get('name', '')
        if 'deck' not in name.lower():
            continue
        price = it.get('goods_price', 0) or 0
        if price < MIN_PRICE:
            continue
        if any(w in name for w in EXCLUDE):
            continue
        if (it.get('num', 0) or 0) <= 0:            # 有庫存
            continue
        if it.get('available') is False or it.get('is_goods_sale') is False:
            continue
        out[f"ruten_{it['id']}"] = {
            'source': '露天', 'name': name[:80], 'price': f"${price:,}",
            'seller': it.get('store_name') or it.get('user') or '',
            'url': f"https://www.ruten.com.tw/item/show?{it['id']}",
        }
    return out


# ── PTT ───────────────────────────────────────────
def fetch_ptt():
    out = {}
    for board in PTT_BOARDS:
        try:
            html = _get(f'https://www.ptt.cc/bbs/{board}/search?q=steam+deck',
                        headers={'Cookie': 'over18=1'}).decode('utf-8', 'replace')
        except Exception as e:
            log(f'PTT {board} 抓取失敗: {e}')
            continue
        for href, title in re.findall(r'<div class="title">\s*<a href="([^"]+)">([^<]+)</a>', html):
            t = title.strip()
            low = t.lower()
            if 'deck' not in low or 'oled' not in low:
                continue
            if '徵' in t or '售' not in t:            # 只要「售」，排除「徵」
                continue
            if any(w in t for w in PTT_SOLD):         # 排除已售出（＝無庫存）
                continue
            mid = href.rstrip('.html').split('/')[-1]
            out[f'ptt_{mid}'] = {
                'source': f'PTT/{board}', 'name': t, 'price': '見內文',
                'seller': '', 'url': f'https://www.ptt.cc{href}',
            }
    return out


def send(item):
    seller = f"　賣家：{item['seller']}" if item['seller'] else ''
    urllib.request.urlopen(urllib.request.Request(
        f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
        data=json.dumps({'chat_id': CHAT_ID, 'parse_mode': 'HTML',
                         'text': f"🎮 <b>Steam Deck OLED 新標的</b>（{item['source']}）\n\n"
                                 f"{item['name']}\n💰 <b>{item['price']}</b>{seller}\n🔗 {item['url']}"}
                        ).encode(),
        headers={'Content-Type': 'application/json'}), timeout=15)


def _safe(fn, label):
    """單一來源失敗不影響其他來源"""
    try:
        return fn()
    except Exception as e:
        log(f'{label} 抓取失敗: {e}')
        return {}


def fetch_carousell():
    import steamdeck_carousell
    return steamdeck_carousell.fetch()


def main():
    try:
        seen = set(json.load(open(SEEN_FILE)))
    except (OSError, ValueError):
        seen = None

    current = {}
    current.update(_safe(fetch_ruten, '露天'))
    current.update(_safe(fetch_ptt, 'PTT'))
    current.update(_safe(fetch_carousell, '旋轉'))

    if seen is None:
        _persist(current)
        log(f'baseline 建立：{len(current)} 筆現有標的，不推播')
        return

    new = [item for uid, item in current.items() if uid not in seen]
    for item in new:
        send(item)
    _persist(current)
    log(f'檢查完成：現有 {len(current)} 筆（露天+PTT+旋轉），新增 {len(new)} 筆')


def _persist(current):
    json.dump(list(current), open(SEEN_FILE, 'w'))
    payload = {'updated': f'{datetime.now():%Y-%m-%d %H:%M}', 'items': list(current.values())}
    json.dump(payload, open(CURRENT_FILE, 'w'), ensure_ascii=False)


def log(msg):
    with open(LOG_FILE, 'a') as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log(f'ERROR: {e}')
        raise
