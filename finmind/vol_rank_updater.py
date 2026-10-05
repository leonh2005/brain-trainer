#!/usr/bin/env python3
"""
每 30 分鐘執行一次，取全市場成交量排名並快取到 /tmp/intraday_vol_rank_cache.json
daytrade-sim 的 /api/movers 讀此快取（依 details[code].total_vol 門檻篩選）。

資料源走 shioaji-gateway(5455)，不自行 login——多支腳本各自直連會互搶 Shioaji
單帳號連線額度，曾導致 451 Too Many Connections（2026-07-27、2026-10-05）。

crontab：
*/30 9-13 * * 1-5 /Users/steven/CCProject/finmind/venv/bin/python3 /Users/steven/CCProject/finmind/vol_rank_updater.py >> /Users/steven/CCProject/logs/vol_rank_updater.log 2>&1
"""

import json
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

VOL_RANK_CACHE = '/tmp/intraday_vol_rank_cache.json'
GATEWAY = 'http://localhost:5455'
RANK_TOP_N = 50     # 存前 50 名，給 monitor 查 top30 用


def fetch_via_gateway() -> dict | None:
    """走 shioaji-gateway 取全市場即時快照，回傳 {code: {'name','total_vol'}} 或 None。
    只取上市(.TW)，與舊版直接列舉 TSE 合約的行為一致。"""
    try:
        import requests
        r = requests.get(f'{GATEWAY}/market_snapshot', timeout=120)
        r.raise_for_status()
        data = r.json()
        if not data.get('ok'):
            print(f"[gateway] 失敗: {data.get('error')}")
            return None
        result = {}
        for s in data.get('items', []):
            code = s.get('code', '')
            if s.get('suffix') != '.TW' or not (code.isdigit() and len(code) == 4):
                continue
            result[code] = {
                'name': s.get('name') or code,
                'total_vol': int(s.get('total_volume') or 0),
            }
        print(f'[gateway] 取得 {len(result)} 筆快照')
        return result if result else None
    except Exception as e:
        print(f'[gateway] 失敗: {e}')
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

    all_vols = fetch_via_gateway()
    if not all_vols:
        print('[fallback] gateway 失敗，改用 TWSE openapi')
        all_vols = fetch_via_twse()

    if not all_vols:
        print('[error] 所有來源都失敗，快取未更新')
        return

    update_cache(all_vols)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] vol_rank_updater 完成")


if __name__ == '__main__':
    main()
