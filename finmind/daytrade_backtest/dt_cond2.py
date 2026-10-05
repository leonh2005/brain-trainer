#!/usr/bin/env python3
"""v2：診斷 + 修正 RVOL + 條件組合 + 按月穩定度"""
import json
from collections import defaultdict
import os as _os
_BASE = _os.path.dirname(_os.path.abspath(__file__))
_CACHE = _os.path.join(_BASE, 'cache')
_LOG = _os.path.join(_BASE, '..', '..', 'logs', 'daytrade.log')

FEE, TAX = 0.001425, 0.0015
feats = json.load(open(_os.path.join(_CACHE, 'dt_features.json')))['feats']
cache = json.load(open(_os.path.join(_CACHE, 'dt_long_cache.json')))
idxc = json.load(open(_os.path.join(_CACHE, 'dt_idx_cache.json')))

def tm(t):
    return int(t[:2]) * 60 + int(t[3:])

def pat(pts, m, tol=3):
    if not pts:
        return None
    b = min(pts, key=lambda p: abs(tm(p['t']) - m))
    return b['price'] if abs(tm(b['t']) - m) <= tol else None

def net(entry, exit_, side='long'):
    if not entry or not exit_:
        return None
    if side == 'short':
        b, s = exit_ * 1000, entry * 1000
    else:
        b, s = entry * 1000, exit_ * 1000
    return (s - b - (b * FEE + s * FEE + s * TAX)) / b * 100

rows = []
idx_none = 0
for key, f in feats.items():
    d, code = key.split('|')
    pts = cache.get(f'{code}|{d}')
    if not pts:
        continue
    em = tm(f['time']) + 1
    entry = pat(pts, em)
    if entry is None:
        continue
    lo = min([p['price'] for p in pts if em <= tm(p['t']) <= tm('13:30')] or [None])
    hpre = [p['price'] for p in pts if tm('09:00') <= tm(p['t']) <= tm('09:15')]
    hi_pre = max(hpre) if hpre else None
    brk = None
    if hi_pre is not None:
        for p in pts:
            if tm('09:16') <= tm(p['t']) <= tm('09:30') and p['price'] > hi_pre:
                brk = p['price']; break
    ip = idxc.get(f'IX0001|{d}')
    idx_chg = None
    if ip:
        i0 = pat(ip, tm('09:00'))
        i1 = pat(ip, tm(f['time']))
        if i0 and i1:
            idx_chg = (i1 - i0) / i0 * 100
    if idx_chg is None:
        idx_none += 1
    elapsed = tm(f['time']) - tm('09:00')          # 已交易分鐘(不含09:00本身)
    proj = f['vol'] / max(elapsed, 1) * 270 if f.get('avg5') else None
    rvol = (proj / f['avg5']) if proj and f['avg5'] else None
    rows.append({**f, 'd': d, 'code': code, 'entry': entry, 'em': em,
                 'x_close': pat(pts, tm('13:30')), 'x_45': pat(pts, tm('09:45')),
                 'lo': lo, 'brk': brk, 'idx_chg': idx_chg, 'rvol': rvol,
                 'pts': pts})

print(f'rows={len(rows)}  idx_chg 缺={idx_none} ({idx_none/len(rows)*100:.0f}%)')
rv = sorted(r['rvol'] for r in rows if r['rvol'] is not None)
if rv:
    print(f'rvol 分布: min={rv[0]:.2f} p25={rv[len(rv)//4]:.2f} 中位={rv[len(rv)//2]:.2f} p75={rv[len(rv)*3//4]:.2f} max={rv[-1]:.2f}')

def stat(rs, label, side='long', key='x_close'):
    v = [net(r['entry'], r[k if False else key], side) for r in rs]
    v = [x for x in v if x is not None]
    if len(v) < 5:
        print(f'  {label:34} n={len(v)} (樣本不足)')
        return
    sv = sorted(v)
    byday = defaultdict(list)
    for r in rs:
        x = net(r['entry'], r[key], side)
        if x is not None:
            byday[r['d']].append(x)
    da = [sum(x)/len(x) for x in byday.values()]
    print(f'  {label:34} n={len(v):4} 平均{sum(v)/len(v):+6.2f}% 中位{sv[len(sv)//2]:+6.2f}% '
          f'勝率{sum(1 for x in v if x>0)/len(v)*100:4.1f}% 日均{sum(da)/len(da):+6.2f}%({len(byday)}天)')

print('\n' + '=' * 104)
print('### 按月穩定度（基準 vs 各條件，→13:30）')
months = sorted({r['d'][:7] for r in rows})
print(f'  {"月份":8} {"基準":>16} {"振幅<5%":>16} {"股價200-500":>16} {"大盤漲":>16} {"有突破":>16}')
for mo in months:
    m = [r for r in rows if r['d'][:7] == mo]
    def a(rs, key='x_close'):
        v = [net(r['entry'], r[key]) for r in rs]
        v = [x for x in v if x is not None]
        return f'{sum(v)/len(v):+.2f}%(n={len(v)})' if v else 'n/a'
    print(f'  {mo:8} {a(m):>16} {a([r for r in m if r["amp"]<5]):>16} '
          f'{a([r for r in m if 200<=r["close"]<500]):>16} '
          f'{a([r for r in m if (r["idx_chg"] or -9)>0]):>16} {a([r for r in m if r["brk"]]):>16}')

print('\n### RVOL（時間標準化：預估全日量/近5均）')
for thr in [0, 1, 2, 3, 5]:
    stat([r for r in rows if r['rvol'] is not None and r['rvol'] >= thr], f'rvol >= {thr}')

print('\n### 條件組合（做多）')
stat(rows, '基準：全部')
stat([r for r in rows if r['amp'] < 5], '振幅<5%')
stat([r for r in rows if 200 <= r['close'] < 500], '股價200-500')
stat([r for r in rows if (r['idx_chg'] or -9) > 0], '大盤當時上漲')
stat([r for r in rows if r['brk']], '突破開盤高點')
print('  --- 兩兩組合 ---')
stat([r for r in rows if r['amp'] < 5 and (r['idx_chg'] or -9) > 0], '振幅<5% + 大盤漲')
stat([r for r in rows if r['amp'] < 5 and r['brk']], '振幅<5% + 有突破')
stat([r for r in rows if (r['idx_chg'] or -9) > 0 and r['brk']], '大盤漲 + 有突破')
stat([r for r in rows if r['amp'] < 5 and 200 <= r['close'] < 500], '振幅<5% + 股價200-500')
print('  --- 三重組合 ---')
stat([r for r in rows if r['amp'] < 5 and (r['idx_chg'] or -9) > 0 and r['brk']],
     '振幅<5% + 大盤漲 + 有突破')

print('\n### 反向（放空）組合')
stat(rows, '基準：全部放空', side='short')
stat([r for r in rows if (r['idx_chg'] or 9) <= 0], '放空：大盤當時跌', side='short')
stat([r for r in rows if r['chg'] >= 5 and (r['idx_chg'] or 9) <= 0], '放空：漲幅>=5% + 大盤跌', side='short')
stat([r for r in rows if r['amp'] >= 8], '放空：振幅>=8%', side='short')
stat([r for r in rows if r['amp'] >= 8 and r['chg'] >= 5], '放空：振幅>=8% + 漲幅>=5%', side='short')

print('\n### 放空 按月穩定度')
for mo in months:
    m = [r for r in rows if r['d'][:7] == mo]
    stat(m, f'{mo} 放空', side='short')
