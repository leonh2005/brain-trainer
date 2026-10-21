# -*- coding: utf-8 -*-
"""把當日隔日沖候選同步成 chip-tracker 的「隔日沖」清單。

- 今日候選 → 放進「隔日沖」清單（已在其他清單的用 PATCH 換清單，不重跑回補）
- 昨天以前留在「隔日沖」但今天不再是候選的 → 移到「觀察」
- 候選檔日期不是今天就中止，避免拿舊檔把清單清空

預設走 command-center 代理（http://127.0.0.1:5950/svc/5850），可用環境變數 SWING_SYNC_BASE 覆寫。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

CC = os.path.expanduser('~/CCProject')
CAND_FILE = f'{CC}/telebot/data/swing_candidates.json'
BASE = os.environ.get('SWING_SYNC_BASE', 'http://127.0.0.1:5950/svc/5850').rstrip('/')
TARGET_LIST = '隔日沖'
FALLBACK_LIST = '觀察'


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def main():
    today = date.today().isoformat()
    with open(CAND_FILE, encoding='utf-8') as f:
        cand = json.load(f)
    cand_date = str(cand.get('date', ''))[:10]
    if cand_date != today:
        print(f'候選檔日期 {cand_date} 不是今天（{today}），中止以免清空清單')
        return 1

    lists = call('GET', '/api/lists')
    by_name = {l['name']: l['id'] for l in lists}
    for name in (TARGET_LIST, FALLBACK_LIST):
        if name not in by_name:
            res = call('POST', '/api/lists', {'name': name})
            by_name[name] = res['id']
    target_id, fallback_id = by_name[TARGET_LIST], by_name[FALLBACK_LIST]

    data = call('GET', '/api/data')
    current = {}
    for lst in data['lists']:
        for s in lst['stocks']:
            current[s['code']] = lst['id']

    wanted = {r['code']: r for r in cand.get('results', []) if r.get('code')}

    moved_out = 0
    for code, list_id in current.items():
        if list_id == target_id and code not in wanted:
            call('PATCH', f'/api/stocks/{code}', {'list_id': fallback_id})
            print(f'移出：{code}')
            moved_out += 1

    added, moved_in = 0, 0
    for code, row in wanted.items():
        if code in current:
            if current[code] != target_id:
                call('PATCH', f'/api/stocks/{code}', {'list_id': target_id})
                moved_in += 1
        else:
            call('POST', '/api/stocks', {'code': code, 'list_id': target_id})
            added += 1

    print(f'完成：今日候選 {len(wanted)} 檔（新增 {added}／移入 {moved_in}），移出舊候選 {moved_out} 檔')
    return 0


if __name__ == '__main__':
    sys.exit(main())
