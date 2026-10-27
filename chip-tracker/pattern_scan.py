# -*- coding: utf-8 -*-
"""幾何型態掃描（stock-pattern），結果寫進 chip.db 的 chart_patterns。

必須用 stock-pattern-probe/.venv（pandas 2.2）執行：chip-tracker 本身跑
pandas 3，與 stock-pattern 不相容。由 app 的「重算型態」工作或 updater
以子程序呼叫，app 端只讀 DB。

只保留實測有方向性的型態（2026-10-21 以本檔自選股回測，扣 0050 超額報酬）：
BATU/ABCDU/CRABU 偏多、HNSD/DNTL 偏空；BFLYU 無效、ABCDD 方向矛盾，皆不存。
"""
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

PROBE_DIR = os.path.expanduser('~/CCProject/stock-pattern-probe')
sys.path.insert(0, os.path.join(PROBE_DIR, 'stock-pattern', 'src'))

import db
import pandas as pd
import utils

KEEP = ('BATU', 'ABCDU', 'CRABU', 'HNSD', 'DNTL')
DETECTORS = [n for n in dir(utils) if n.startswith('find_')]
MIN_BARS = 200


def load_df(con, code):
    rows = con.execute(
        "SELECT date, open, high, low, close, volume FROM daily "
        "WHERE code = ? AND close > 0 AND volume > 0 ORDER BY date", (code,)
    ).fetchall()
    if len(rows) < MIN_BARS:
        return None
    df = pd.DataFrame(rows, columns=['date', 'Open', 'High', 'Low', 'Close', 'Volume'])
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date')


def scan(df, code):
    pivots = utils.get_max_min(df, 6, 6, 'both')
    found = []
    for fn in DETECTORS:
        try:
            res = getattr(utils, fn)(code, df, pivots, {})
        except Exception:
            continue
        if res and res['pattern'] in KEEP:
            found.append({
                'pattern': res['pattern'],
                'start': str(pd.Timestamp(res['start']).date()),
                'end': str(pd.Timestamp(res['end']).date()),
            })
    return found


def main():
    conn = db.get_conn()
    codes = [(r['code'], r['name']) for r in conn.execute("SELECT code, name FROM stocks ORDER BY code")]
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    hit = 0
    for code, name in codes:
        df = load_df(conn, code)
        if df is None:
            conn.execute("DELETE FROM chart_patterns WHERE code = ?", (code,))  # 資料不足就不留舊結果
            continue
        found = scan(df, code)
        conn.execute(
            "INSERT INTO chart_patterns (code, date, patterns, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET date=excluded.date, patterns=excluded.patterns, "
            "updated_at=excluded.updated_at",
            (code, str(df.index[-1].date()), json.dumps(found, ensure_ascii=False), now),
        )
        if found:
            hit += 1
            print(f"{code} {name}: " + "、".join(p['pattern'] for p in found))
    conn.commit()
    conn.close()
    print(f"型態掃描完成：{hit}/{len(codes)} 檔有型態")


if __name__ == '__main__':
    main()
