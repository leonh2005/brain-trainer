# -*- coding: utf-8 -*-
"""把當日「隔日沖候選」與「只差一項」同步成 chip-tracker 的兩個清單。

- 今日候選 → 放進對應清單（已在其他清單的用 PATCH 換清單，不重跑回補）
- 昨天以前留在該清單、今天不再是名單的 → 移到「觀察」
- 庫存持股一律不動（避免把持倉搬出庫存）

2026-10-28 起改由 screener.py 寫檔完成後主動呼叫（不再靠 cron 13:01 進來 poll）。
大盤跳過日 screener 不會呼叫本檔 — 當天沒有新資料，清單維持前一交易日內容，
該狀態由 screener 自己的推播訊息說明，本檔不重複推播。

預設走 command-center 代理（http://127.0.0.1:5950/svc/5850），可用環境變數 SWING_SYNC_BASE 覆寫。
"""
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('swing_list_sync')

CC = os.path.expanduser('~/CCProject')
BASE = os.environ.get('SWING_SYNC_BASE', 'http://127.0.0.1:5950/svc/5850').rstrip('/')
HOLDING_LIST = '庫存'
FALLBACK_LIST = '觀察'
TOKEN_FILE = f'{CC}/.secrets/telegram_token.txt'
CHAT_ID = '7556217543'
# 主動觸發後資料檔已寫好，只剩檔案系統落盤延遲；短重試即可，不再空等 600s
RETRY_SEC = int(os.environ.get('SWING_SYNC_WAIT', '30'))
POLL = 2


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def send_telegram(text):
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
    except Exception as e:
        log.warning(f'Telegram 推播失敗: {e}')


def swing_codes(today):
    """隔日沖候選；檔案不是今天的回 None。"""
    d = read_json(f'{CC}/telebot/data/swing_candidates.json')
    if str(d.get('date', ''))[:10] != today:
        return None
    return {r['code']: r.get('name', '') for r in d.get('results', []) if r.get('code')}


def near_miss_codes(today):
    """只差一項名單；今天還沒寫入回 None。"""
    d = read_json(f'{CC}/telebot/data/near_miss_detail.json')
    if today not in d:
        return None
    return {r['code']: r.get('name', '') for r in d[today] or [] if r.get('code')}


def wait_for(loader, today):
    deadline = time.time() + RETRY_SEC
    while True:
        try:
            data = loader(today)
        except (OSError, ValueError):
            data = None
        if data is not None:
            return data
        if time.time() >= deadline:
            return None
        time.sleep(POLL)


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


def ensure_list(name, by_name):
    if name not in by_name:
        by_name[name] = call('POST', '/api/lists', {'name': name})['id']
    return by_name[name]


def sync_list(name, wanted, holding_id, fallback_id, target_id, current, on_drop='move'):
    """on_drop='move' 舊名單移到觀察；'delete' 直接移除追蹤（庫存一律不動）。"""
    added = moved_in = moved_out = skipped = 0
    for code, list_id in list(current.items()):
        if list_id == target_id and code not in wanted:
            if on_drop == 'delete':
                call('POST', '/api/stocks/remove', {'code': code})
                current.pop(code, None)
            else:
                call('PATCH', f'/api/stocks/{code}', {'list_id': fallback_id})
                current[code] = fallback_id
            moved_out += 1
    for code, stock_name in wanted.items():
        cur = current.get(code)
        if cur == holding_id:
            skipped += 1  # 持倉不動，只記數
            continue
        if cur is None:
            call('POST', '/api/stocks', {'code': code, 'list_id': target_id})
            current[code] = target_id
            added += 1
        elif cur != target_id:
            call('PATCH', f'/api/stocks/{code}', {'list_id': target_id})
            current[code] = target_id
            moved_in += 1
    action = '移除' if on_drop == 'delete' else '移到觀察'
    log.info(f'{name}：今日 {len(wanted)} 檔（新增 {added}／移入 {moved_in}），'
             f'{action}舊名單 {moved_out} 檔{"，持倉未動 " + str(skipped) + " 檔" if skipped else ""}')
    return added + moved_in + moved_out


if __name__ == '__main__':
    today = date.today().isoformat()

    targets = [
        ('隔日沖', wait_for(swing_codes, today)),
        ('只差一項', wait_for(near_miss_codes, today)),
    ]
    missing = [n for n, d in targets if d is None]
    if missing:
        msg = f'等 {RETRY_SEC}s 後仍拿不到今天的資料（{"/".join(missing)}），中止不動清單'
        log.error(msg)
        send_telegram(f'⚠️ 隔日沖清單同步失敗（{today}）\n\n{msg}\n\n清單維持舊內容。')
        sys.exit(1)

    lists = call('GET', '/api/lists')
    by_name = {l['name']: l['id'] for l in lists}
    holding_id = ensure_list(HOLDING_LIST, by_name)
    fallback_id = ensure_list(FALLBACK_LIST, by_name)

    data = call('GET', '/api/data')
    current = {s['code']: lst['id'] for lst in data['lists'] for s in lst['stocks']}

    # 隔日沖：掉出名單的移到觀察；只差一項：掉出就移除追蹤，不進觀察
    drop_actions = {'隔日沖': 'move', '只差一項': 'delete'}
    for name, wanted in targets:
        target_id = ensure_list(name, by_name)
        sync_list(name, wanted, holding_id, fallback_id, target_id, current,
                  on_drop=drop_actions[name])
