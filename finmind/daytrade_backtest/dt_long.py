#!/usr/bin/env python3
"""當沖出場點長期回測：候選來自 daytrade.log（2026-03-30 起真實推播名單）
進場 = 當日執行時間 + 1 分鐘；比較各出場時點。
"""
import json, os, re, time, urllib.request
from collections import defaultdict
import os as _os
_BASE = _os.path.dirname(_os.path.abspath(__file__))
_CACHE = _os.path.join(_BASE, 'cache')
_LOG = _os.path.join(_BASE, '..', '..', 'logs', 'daytrade.log')

GATEWAY = 'http://127.0.0.1:5455'
LOG = _LOG
CACHE = _os.path.join(_CACHE, 'dt_long_cache.json')
FEE, TAX = 0.001425, 0.0015
THROTTLE = 0.30

cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

def get(path):
    for a in range(3):
        try:
            with urllib.request.urlopen(GATEWAY + path, timeout=25) as r:
                return json.loads(r.read())
        except Exception:
            if a == 2:
                return None
            time.sleep(1.0)

def intraday(code, d):
    k = f'{code}|{d}'
    if k not in cache:
        resp = get(f'/intraday?code={code}&date={d}')
        time.sleep(THROTTLE)
        cache[k] = (resp or {}).get('points') if resp and resp.get('ok') else None
    return cache[k]

def save():
    json.dump(cache, open(CACHE, 'w'))

def tm(t):
    return int(t[:2]) * 60 + int(t[3:])

def pat(pts, m, tol=3):
    if not pts:
        return None
    b = min(pts, key=lambda p: abs(tm(p['t']) - m))
    return b['price'] if abs(tm(b['t']) - m) <= tol else None

def net(e, x):
    if not e or not x:
        return None
    b, s = e * 1000, x * 1000
    return (s - b - (b * FEE + s * FEE + s * TAX)) / b * 100

# ---- 解析 log ----
recs = []
cur_date = cur_time = None
seen = {}
for ln in open(LOG, encoding='utf-8', errors='ignore'):
    m = re.search(r'當沖候選</b>｜(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})', ln)
    if m:
        cur_date, cur_time = m.group(1), m.group(2)
        continue
    m2 = re.search(r"候選清單已寫入 .*: \[(.*?)\]", ln)
    if m2 and cur_date and cur_time:
        codes = re.findall(r"'(\d{4,6})'", m2.group(1))
        if codes:
            seen[cur_date] = (cur_time, codes)
recs = [(d, t, c) for d, (t, c) in sorted(seen.items())]
print(f'候選日數={len(recs)}  總檔次={sum(len(c) for _,_,c in recs)}')
bytime = defaultdict(int)
for _, t, _ in recs:
    bytime[t] += 1
print('執行時間分布:', dict(bytime))

# ---- 抓分K ----
n = 0
for d, t, codes in recs:
    for c in codes:
        intraday(c, d)
        n += 1
    save()
    if n % 100 == 0:
        print(f'  fetched {n} ...', flush=True)
save()
print(f'分K cache={len(cache)}')

# ---- 回測 ----
EXITS = [tm(f'{h:02d}:{m:02d}') for h in (9,10,11,12,13) for m in range(0,60) if (h,m)>=(9,10)]
def hm(m):
    h, mi = divmod(m, 60)
    return f'{h:02d}:{mi:02d}'

def run(rows, label, exits):
    if len(rows) < 20:
        print(f'\n### {label} n={len(rows)} 樣本不足，略過')
        return
    print(f'\n### {label}  n={len(rows)}')
    best = None
    for m in exits:
        v = [net(r['entry'], pat(r['pts'], m)) for r in rows]
        v = [x for x in v if x is not None]
        if len(v) < len(rows) * 0.7:
            continue
        avg = sum(v) / len(v)
        wr = sum(1 for x in v if x > 0) / len(v) * 100
        if best is None or avg > best[1]:
            best = (m, avg, wr, len(v))
        if m % 5 == 0:
            print(f'  {hm(m)}  avg {avg:+6.3f}%  勝率 {wr:5.1f}%  n={len(v)}')
    if best:
        print(f'  >>> 最優出場 {hm(best[0])}  平均 {best[1]:+.3f}%  勝率 {best[2]:.1f}%  (n={best[3]})')

groups = defaultdict(list)
for d, t, codes in recs:
    entry_m = tm(t) + 1
    period = {'09:30': 'P1(09:30執行, 3月底~)', '09:19': 'P2(09:19執行)', '09:10': 'P3(09:10執行)'}[t]
    for c in codes:
        pts = intraday(c, d)
        if not pts:
            continue
        e = pat(pts, entry_m)
        if e is None:
            continue
        groups[period].append({'d': d, 'code': c, 'entry': e, 'pts': pts,
                               'entry_m': entry_m,
                               'exit_abs': {m: pat(pts, m) for m in range(entry_m, tm('13:30')+1)}})

print(f'\n{"="*74}')
ALL = [r for g in groups.values() for r in g]
# 相對進場時間（跨時期可比較）
print(f'\n### 全期 n={len(ALL)}  以「進場後 N 分鐘」為出場')
for off in [1,2,3,5,8,10,15,20,30,45,60,90,120,180,240]:
    v = [net(r['entry'], r['exit_abs'].get(r['entry_m']+off)) for r in ALL]
    v = [x for x in v if x is not None]
    if v:
        print(f'  進場後 {off:>3}分  avg {sum(v)/len(v):+6.3f}%  勝率 {sum(1 for x in v if x>0)/len(v)*100:5.1f}%')

for p in ['P1(09:30執行, 3月底~)', 'P2(09:19執行)', 'P3(09:10執行)']:
    if groups[p]:
        em = groups[p][0]['entry_m']
        run(groups[p], p, range(em, tm('13:30')+1))
