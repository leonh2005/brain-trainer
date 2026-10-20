# -*- coding: utf-8 -*-
"""10 個 InStock 策略的 3 年回測：隔日開盤進場，持有 5/10/20 日，分年度統計。"""

import sqlite3
from collections import defaultdict
from datetime import datetime

import db
from strategies import STRATEGIES, prepare_frame

START = "2023-10-01"
END = "2026-10-20"
HOLD_DAYS = (5, 10, 20)


def load_frame(conn: sqlite3.Connection, code: str):
    rows = [dict(r) for r in conn.execute(
        "SELECT date, open, high, low, close, volume, foreign_net, trust_net, dealer_net "
        "FROM daily WHERE code = ? AND close IS NOT NULL AND open IS NOT NULL "
        "AND high IS NOT NULL AND low IS NOT NULL AND volume IS NOT NULL "
        "ORDER BY date", (code,))]
    return prepare_frame(rows)


def run_backtest(codes: list = None, start: str = START, end: str = END,
                 progress=None) -> list:
    """回傳 [{strategy, hold_days, year, samples, win_rate, avg_return, max_drawdown}]。"""
    conn = db.get_conn()
    try:
        if codes is None:
            codes = [r["code"] for r in conn.execute("SELECT code FROM stocks ORDER BY code")]
        names = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM stocks")}
        trades = defaultdict(list)  # (strategy, hold) -> [(year, ret, mdd)]

        for ci, code in enumerate(codes):
            if progress:
                progress(ci, len(codes), f"回測 {code} ({ci + 1}/{len(codes)})")
            df = load_frame(conn, code)
            if len(df) < 260:
                continue
            opens = df["open"].values
            closes = df["close"].values
            lows = df["low"].values
            dates = df["date"].values
            signal_idx = [i for i, d in enumerate(dates) if start <= d <= end]
            for i in signal_idx:
                entry_idx = i + 1
                if entry_idx >= len(df):
                    continue
                entry = float(opens[entry_idx])
                if not entry:
                    continue
                for name, fn in STRATEGIES:
                    if not fn((code, names.get(code, "")), df, date=dates[i]):
                        continue
                    year = str(dates[i])[:4]
                    for hold in HOLD_DAYS:
                        exit_idx = entry_idx + hold - 1
                        if exit_idx >= len(df):
                            continue
                        ret = (float(closes[exit_idx]) - entry) / entry * 100
                        mdd = (min(float(v) for v in lows[entry_idx:exit_idx + 1]) - entry) / entry * 100
                        trades[(name, hold)].append((year, ret, mdd))

        results = []
        for (name, hold), items in trades.items():
            years = sorted({y for y, _, _ in items})
            for year in years + ["ALL"]:
                subset = items if year == "ALL" else [t for t in items if t[0] == year]
                if not subset:
                    continue
                rets = [r for _, r, _ in subset]
                results.append({
                    "strategy": name, "hold_days": hold, "year": year,
                    "samples": len(rets),
                    "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 2),
                    "avg_return": round(sum(rets) / len(rets), 3),
                    "max_drawdown": round(min(m for _, _, m in subset), 3),
                })
        return results
    finally:
        conn.close()


def store_results(results: list) -> str:
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM backtest_stats")
        conn.executemany(
            "INSERT INTO backtest_stats (run_at, strategy, hold_days, year, samples, "
            "win_rate, avg_return, max_drawdown) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_at, r["strategy"], r["hold_days"], r["year"], r["samples"],
              r["win_rate"], r["avg_return"], r["max_drawdown"]) for r in results],
        )
        conn.commit()
    finally:
        conn.close()
    return run_at


def print_table(results: list) -> None:
    print(f"{'策略':<12} {'持有':>4} {'年度':>5} {'樣本':>6} {'勝率%':>7} {'平均報酬%':>9} {'最大回撤%':>9}")
    for r in results:
        print(f"{r['strategy']:<12} {r['hold_days']:>4} {r['year']:>5} {r['samples']:>6} "
              f"{r['win_rate']:>7.2f} {r['avg_return']:>9.3f} {r['max_drawdown']:>9.3f}")


if __name__ == "__main__":
    import sys
    res = run_backtest(progress=lambda i, n, m: print(f"[{i + 1}/{n}] {m}", file=sys.stderr))
    run_at = store_results(res)
    print(f"run_at={run_at}\n")
    print_table(res)
