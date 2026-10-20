#!/usr/bin/env python3
"""每日收集個股期貨收盤，供「期貨基差 vs 隔日報酬」影子驗證累積。

資料源：期交所 OpenAPI（免費、只給最近交易日，故必須每天累積）
輸出：data/futures_daily/<date>.json
用法：python3 collect_futures_daily.py
"""
import json
import os
import re
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'data', 'futures_daily')
CONTRACT_MAP_PATH = os.path.join(ROOT, 'data', 'futures_contract_map.json')
API = 'https://openapi.taifex.com.tw/v1/DailyMarketReportFut'


def build_contract_map():
    """期交所商品代碼 → 股票代碼（CA.. → 1303，API 用 CAF）。快取一份。"""
    if os.path.exists(CONTRACT_MAP_PATH):
        with open(CONTRACT_MAP_PATH, encoding='utf-8') as f:
            return json.load(f)
    req = urllib.request.Request('https://www.taifex.com.tw/cht/2/stockLists',
                                 headers={'User-Agent': 'Mozilla/5.0'})
    html = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', 'ignore')
    m = {}
    for tr in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', html):
        cells = [re.sub('<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>([\s\S]*?)</td>', tr)]
        if len(cells) >= 4 and re.fullmatch(r'\d{4}', cells[2] or ''):
            m[cells[0] + 'F'] = cells[2]
    os.makedirs(os.path.dirname(CONTRACT_MAP_PATH), exist_ok=True)
    with open(CONTRACT_MAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(m, f, ensure_ascii=False)
    return m


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        cmap = build_contract_map()
        data = json.loads(urllib.request.urlopen(API, timeout=30).read())
    except Exception as e:
        print(f'[futures] 資料取得失敗（保留既有累積，不中斷）: {e}')
        return
    if not data:
        print('OpenAPI 回空，跳過')
        return
    day = data[0]['Date']
    date_str = f'{day[:4]}-{day[4:6]}-{day[6:]}'
    path = os.path.join(OUT_DIR, f'{date_str}.json')
    if os.path.exists(path):
        print(f'{date_str} 已存在，跳過')
        return
    # 每個商品取成交量最大的月份（近月）
    best = {}
    for r in data:
        code = r['Contract']
        if code not in cmap:
            continue
        try:
            vol = int(r.get('Volume') or 0)
            last = float(r.get('Last') or 0)
        except ValueError:
            continue
        if last <= 0:
            continue
        cur = best.get(code)
        if cur is None or vol > cur['volume']:
            best[code] = {'stock': cmap[code], 'last': last, 'volume': vol,
                          'oi': int(r.get('OpenInterest') or 0), 'month': r['ContractMonth(Week)']}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'date': date_str, 'collected_at': datetime.now().isoformat(),
                   'contracts': best}, f, ensure_ascii=False)
    print(f'{date_str} 已存 {len(best)} 檔個股期貨 → {path}')


if __name__ == '__main__':
    main()
