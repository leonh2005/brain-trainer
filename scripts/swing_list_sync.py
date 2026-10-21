# -*- coding: utf-8 -*-
"""把當日「隔日沖候選」與「只差一項」同步成 chip-tracker 的兩個清單。

- 今日候選 → 放進對應清單（已在其他清單的用 PATCH 換清單，不重跑回補）
- 昨天以前留在該清單、今天不再是名單的 → 移到「觀察」
- 庫存持股一律不動（避免把持倉搬出庫存）
- 資料檔日期不是今天就等（screener 13:00 跑完才寫檔，13:01 進來通常還在寫）
- 等超過 WAIT_SEC 仍沒更新就中止，避免拿舊檔清空清單

預設走 command-center 代理（http://127.0.0.1:5950/svc/5850），可用環境變數 SWING_SYNC_BASE 覆寫。
"""
import json
import os
import sys
import time
import urllib.request
from datetime import date

CC = os.path.expanduser('~/CCProject')
BASE = os.environ.get('SWING_SYNC_BASE', 'http://127.0.0.1:5950/svc/5850').rstrip('/')
HOLDING_LIST = '庫存'
FALLBACK_LIST = '觀察'
WAIT_SEC = int(os.environ.get('SWING_SYNC_WAIT', '600'))
POLL = 20


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


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
    deadline = time.time() + WAIT_SEC
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


def sync_list(name, wanted, holding_id, fallback_id, target_id, current):
    """回傳 (added, moved_in, moved_out, skipped_holding)"""
    added = moved_in = moved_out = skipped = 0
    for code, list_id in list(current.items()):
        if list_id == target_id and code not in wanted:
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
    print(f'{name}：今日 {len(wanted)} 檔（新增 {added}／移入 {moved_in}），'
          f'移出舊名單 {moved_out} 檔{"，持倉未動 " + str(skipped) + " 檔" if skipped else ""}')
    return added + moved_in + moved_out


if __name__ == '__main__':
    today = date.today().isoformat()

    targets = [
        ('隔日沖', wait_for(swing_codes, today)),
        ('只差一項', wait_for(near_miss_codes, today)),
    ]
    missing = [n for n, d in targets if d is None]
    if missing:
        print(f'等 {WAIT_SEC}s 後仍拿不到今天的資料（{"/".join(missing)}），中止不動清單')
        sys.exit(1)

    lists = call('GET', '/api/lists')
    by_name = {l['name']: l['id'] for l in lists}
    holding_id = ensure_list(HOLDING_LIST, by_name)
    fallback_id = ensure_list(FALLBACK_LIST, by_name)

    data = call('GET', '/api/data')
    current = {s['code']: lst['id'] for lst in data['lists'] for s in lst['stocks']}

    for name, wanted in targets:
        target_id = ensure_list(name, by_name)
        sync_list(name, wanted, holding_id, fallback_id, target_id, current)
