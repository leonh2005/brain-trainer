#!/usr/bin/env python3
"""最終彙整：最佳條件的按月穩定度 + 停損疊加 + 總表"""
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

def net(e, x, side='long'):
    if not e or not x:
        return None
    b, s = (x * 1000, e * 1000) if side == 'short' else (e * 1000, x * 1000)
    return (s - b - (b * FEE + s * FEE + s * TAX)) / b * 100

rows = []
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
    brk = None
    if hpre:
        hi = max(hpre)
        for p in pts:
            if tm('09:16') <= tm(p['t']) <= tm('09:30') and p['price'] > hi:
                brk = p['price']; break
    ip = idxc.get(f'IX0001|{d}')
    idx_chg = None
    if ip:
        i0, i1 = pat(ip, tm('09:00')), pat(ip, tm(f['time']))
        if i0 and i1:
            idx_chg = (i1 - i0) / i0 * 100
    rows.append({**f, 'd': d, 'code': code, 'entry': entry, 'em': em,
                 'x_close': pat(pts, tm('13:30')), 'lo': lo, 'brk': brk, 'idx_chg': idx_chg})

def agg(rs, side='long', sl=None):
    v = []
    for r in rs:
        e = r['entry']
        if sl and r['lo'] is not None and r['lo'] <= e * (1 - sl / 100):
            x = e * (1 - sl / 100)
        else:
            x = r['x_close']
        n = net(e, x, side)
        if n is not None:
            v.append(n)
    return v

def line(rs, label, side='long', sl=None, show_month=False):
    v = agg(rs, side, sl)
    if len(v) < 5:
        print(f'  {label:32} n={len(v)} (不足)')
        return
    sv = sorted(v)
    print(f'  {label:32} n={len(v):4} 平均{sum(v)/len(v):+6.2f}% 中位{sv[len(sv)//2]:+6.2f}% '
          f'勝率{sum(1 for x in v if x>0)/len(v)*100:4.1f}%')
    if show_month:
        for mo in sorted({r['d'][:7] for r in rs}):
            m = [r for r in rs if r['d'][:7] == mo]
            vv = agg(m, side, sl)
            if len(vv) >= 5:
                print(f'      {mo}  n={len(vv):3}  平均{sum(vv)/len(vv):+6.2f}%  '
                      f'勝率{sum(1 for x in vv if x>0)/len(vv)*100:4.1f}%')

CORE = lambda r: r['amp'] < 5 and r['brk']

print('=' * 78)
print('### 候選核心組合「振幅<5% + 突破開盤高點」按月穩定度')
line([r for r in rows if CORE(r)], 'CORE', show_month=True)
print('\n### CORE + 停損')
for sl in [1.0, 1.5, 2.0, 3.0]:
    line([r for r in rows if CORE(r)], f'CORE + 停損{sl}%', sl=sl)
print('\n### CORE + 大盤漲（僅6-10月有大盤資料）')
line([r for r in rows if CORE(r) and (r['idx_chg'] or -9) > 0], 'CORE + 大盤漲')
print('  對照組：')
line([r for r in rows if (r['idx_chg'] or -9) > 0], '僅大盤漲')
line([r for r in rows if r['amp'] < 5], '僅振幅<5%')
line([r for r in rows if r['brk']], '僅突破')

print('\n' + '=' * 78)
print('### 全條件總表（做多，→13:30）')
line(rows, '基準(全部)')
line([r for r in rows if r['amp'] < 5], '振幅<5%')
line([r for r in rows if r['chg'] < 5], '漲幅<5%')
line([r for r in rows if r['chg'] >= 5], '漲幅>=5%(對照)')
line([r for r in rows if 200 <= r['close'] < 500], '股價200-500')
line([r for r in rows if r['pos'] >= 85], '位階>=85%')
line([r for r in rows if (r['idx_chg'] or -9) > 0], '大盤漲')
line([r for r in rows if r['brk']], '突破開盤高點')
line([r for r in rows if CORE(r)], 'CORE(振幅<5%+突破)')
line(rows, '基準 + 停損1%', sl=1.0)
line([r for r in rows if CORE(r)], 'CORE + 停損1%', sl=1.0)

print('\n### 反向（放空）')
print('  註：台股當沖平盤下不得放空、需券源，實務受限')
line(rows, '全部放空', side='short', show_month=True)
line([r for r in rows if r['chg'] >= 5], '放空 漲幅>=5%', side='short')
line([r for r in rows if r['amp'] >= 8], '放空 振幅>=8%', side='short')
line([r for r in rows if (r['idx_chg'] or 9) <= 0], '放空 大盤跌(6-10月)', side='short')
