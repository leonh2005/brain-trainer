#!/usr/bin/env python3
"""從 daytrade.log 解析每檔候選的完整特徵，輸出 /tmp/dt_features.json"""
import json, re
import os as _os
_BASE = _os.path.dirname(_os.path.abspath(__file__))
_CACHE = _os.path.join(_BASE, 'cache')
_LOG = _os.path.join(_BASE, '..', '..', 'logs', 'daytrade.log')

LOG = _LOG
OUT = _os.path.join(_CACHE, 'dt_features.json')

lines = open(LOG, encoding='utf-8', errors='ignore').read().split('\n')

cur_date = cur_time = None
feats = {}       # (date, code) -> {...}
order = []       # 保留每日候選順序
pending = None

re_hdr = re.compile(r'當沖候選</b>｜(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})')
re_name = re.compile(r'<b>(\d{4,6})\s+([^<]+)</b>')
re_p1 = re.compile(r'收:\s*([\d.]+)\s+漲:\s*([+-][\d.]+)%\s+振:\s*([\d.]+)%')
re_p2 = re.compile(r'量:\s*([\d,]+)張\s+近5均:\s*([\d,]+)張.*?收盤位:\s*([\d.]+)%')

for ln in lines:
    m = re_hdr.search(ln)
    if m:
        cur_date, cur_time = m.group(1), m.group(2)
        continue
    m = re_name.search(ln)
    if m and cur_date:
        pending = {'date': cur_date, 'time': cur_time, 'code': m.group(1), 'name': m.group(2).strip()}
        continue
    if pending is not None:
        m = re_p1.search(ln)
        if m:
            pending['close'] = float(m.group(1))
            pending['chg'] = float(m.group(2))
            pending['amp'] = float(m.group(3))
            continue
        m = re_p2.search(ln)
        if m:
            pending['vol'] = int(m.group(1).replace(',', ''))
            pending['avg5'] = int(m.group(2).replace(',', ''))
            pending['pos'] = float(m.group(3))
            key = (pending['date'], pending['code'])
            if key not in feats:
                feats[key] = pending
                order.append(key)
            pending = None
            continue
        if ln.strip() and not ln.strip().startswith(('🤖', '💬', '   ')):
            pending = None

print(f'解析出 {len(feats)} 筆 (date,code) 特徵')
recs = list(feats.values())
if recs:
    print('樣本:')
    for r in recs[:3] + recs[-3:]:
        print(' ', r)
by_month = {}
for r in recs:
    by_month.setdefault(r['date'][:7], 0)
    by_month[r['date'][:7]] += 1
print('每月檔次:', dict(sorted(by_month.items())))
json.dump({'feats': {f'{k[0]}|{k[1]}': v for k, v in feats.items()}}, open(OUT, 'w'), ensure_ascii=False)
print(f'已寫 {OUT}')
