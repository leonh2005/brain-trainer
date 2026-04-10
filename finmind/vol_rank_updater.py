#!/usr/bin/env python3
"""
每 30 分鐘執行一次，取全市場成交量排名並快取到 /tmp/intraday_vol_rank_cache.json
intraday_monitor.py 讀此快取判斷 top30 條件。

crontab：
*/30 9-13 * * 1-5 /Users/steven/CCProject/finmind/venv/bin/python3 /Users/steven/CCProject/finmind/vol_rank_updater.py >> /tmp/vol_rank_updater.log 2>&1
"""

import json
import os
import time
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

VOL_RANK_CACHE = '/tmp/intraday_vol_rank_cache.json'
MAX_WAIT = 30       # 最多等幾秒讓 Shioaji session 暖機
RANK_TOP_N = 50     # 存前 50 名，給 monitor 查 top30 用


def fetch_via_shioaji() -> dict | None:
    """用 Shioaji 取全市場快照，回傳 {code: total_vol} 或 None"""
    try:
        import shioaji as sj
        api = sj.Shioaji(simulation=False)
        api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        # 暖機：等到有快照回來或超時
        all_contracts = [
            c for c in api.Contracts.Stocks.TSE
            if hasattr(c, 'code') and c.code.isdigit() and len(c.code) == 4
        ]
        cmap = {c.code: c for c in all_contracts}

        for wait in range(1, MAX_WAIT + 1):
            time.sleep(1)
            chunk = all_contracts[:20]
            snaps = api.snapshots(chunk)
            if snaps:
                print(f'[shioaji] session 暖機完成（{wait}s）')
                break
        else:
            print('[shioaji] 暖機逾時，快照仍為空')
            return None

        # 分批取所有快照
        all_vols = {}
        batch = 200
        for i in range(0, len(all_contracts), batch):
            chunk = all_contracts[i:i + batch]
            try:
                snaps = api.snapshots(chunk)
                for s in snaps:
                    all_vols[s.code] = {
                        'name': getattr(cmap.get(s.code), 'name', s.code),
                        'total_vol': int(s.total_volume),
                    }
            except Exception as e:
                print(f'[shioaji] batch {i} 失敗: {e}')

        print(f'[shioaji] 取得 {len(all_vols)} 筆快照')
        return all_vols if all_vols else None

    except Exception as e:
        print(f'[shioaji] 失敗: {e}')
        return None


def fetch_via_twse() -> dict | None:
    """用 TWSE openapi 取當日成交量（備援，可能為前一交易日資料）"""
    try:
        import requests
        r = requests.get(
            'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=15,
            verify=False,
        )
        data = r.json()
        result = {}
        for item in data:
            code = item.get('Code', '')
            if not (code.isdigit() and len(code) == 4):
                continue
            vol_str = item.get('TradeVolume', '0').replace(',', '')
            try:
                vol = int(vol_str) // 1000  # 股 → 張
            except ValueError:
                vol = 0
            result[code] = {
                'name': item.get('Name', code),
                'total_vol': vol,
            }
        print(f'[twse] 取得 {len(result)} 筆（可能為前一交易日）')
        return result if result else None
    except Exception as e:
        print(f'[twse] 失敗: {e}')
        return None


def update_cache(all_vols: dict):
    """過濾 ETF、排序，存前 RANK_TOP_N 名到快取"""
    non_etf = {c: v for c, v in all_vols.items() if not c.startswith('0')}
    ranked = sorted(non_etf, key=lambda c: non_etf[c]['total_vol'], reverse=True)[:RANK_TOP_N]
    cache = {
        'updated_at': datetime.now().isoformat(),
        'top_codes': ranked,
        'details': {c: non_etf[c] for c in ranked},
    }
    with open(VOL_RANK_CACHE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False)

    top10 = [(c, non_etf[c]['name'], non_etf[c]['total_vol']) for c in ranked[:10]]
    print('[cache] 前10名:', ' '.join(f"{c}{n}({v//1000}K)" for c,n,v in top10))
    print(f'[cache] 已存 {len(ranked)} 筆 → {VOL_RANK_CACHE}')


def main():
    now = datetime.now()
    print(f"[{now.strftime('%H:%M:%S')}] vol_rank_updater 開始")

    all_vols = fetch_via_shioaji()
    if not all_vols:
        print('[fallback] Shioaji 失敗，改用 TWSE openapi')
        all_vols = fetch_via_twse()

    if not all_vols:
        print('[error] 所有來源都失敗，快取未更新')
        return

    update_cache(all_vols)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] vol_rank_updater 完成")


if __name__ == '__main__':
    main()
