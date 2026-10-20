# -*- coding: utf-8 -*-
"""CYQ 與策略選股的服務層：算好寫進 DB，Flask 只讀。"""

import json
from datetime import datetime

import cyq
import db
from strategies import STRATEGIES, prepare_frame


def load_history_rows(conn, code: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT d.date, d.open, d.high, d.low, d.close, d.volume, "
        "e.amount, e.amplitude, e.turnover "
        "FROM daily d JOIN daily_ext e ON e.code = d.code AND e.date = d.date "
        "WHERE d.code = ? AND d.close > 0 AND d.volume > 0 AND d.open IS NOT NULL "
        "AND d.high IS NOT NULL AND d.low IS NOT NULL "
        "ORDER BY d.date", (code,))]


def compute_cyq_all(progress=None) -> dict:
    conn = db.get_conn()
    try:
        codes = [r["code"] for r in conn.execute("SELECT code FROM stocks ORDER BY code")]
        ok, failed = 0, []
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, code in enumerate(codes):
            if progress:
                progress(i, len(codes), f"計算 {code} ({i + 1}/{len(codes)})")
            try:
                result = cyq.compute_from_rows(load_history_rows(conn, code))
                conn.execute(
                    "INSERT INTO cyq_cache (code, date, payload, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(code) DO UPDATE SET date=excluded.date, "
                    "payload=excluded.payload, updated_at=excluded.updated_at",
                    (code, result["date"], json.dumps(result, separators=(",", ":")), now),
                )
                ok += 1
            except Exception as e:  # 資料不足的個股跳過，其餘照算
                failed.append(f"{code}: {e}")
        conn.commit()
        return {"ok": ok, "failed": failed, "updated_at": now}
    finally:
        conn.close()


def get_cyq(conn, code: str):
    row = conn.execute("SELECT payload FROM cyq_cache WHERE code = ?", (code,)).fetchone()
    if row:
        return json.loads(row["payload"])
    result = cyq.compute_from_rows(load_history_rows(conn, code))
    conn.execute(
        "INSERT INTO cyq_cache (code, date, payload, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(code) DO UPDATE SET date=excluded.date, payload=excluded.payload, "
        "updated_at=excluded.updated_at",
        (code, result["date"], json.dumps(result, separators=(",", ":")),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    conn.commit()
    return result


def run_strategies(progress=None) -> dict:
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute("SELECT code, name FROM stocks ORDER BY code")]
        run_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        signals = []
        for i, s in enumerate(rows):
            if progress:
                progress(i, len(rows), f"掃描 {s['code']} ({i + 1}/{len(rows)})")
            data = [dict(r) for r in conn.execute(
                "SELECT date, open, high, low, close, volume, foreign_net, trust_net, dealer_net "
                "FROM daily WHERE code = ? AND close IS NOT NULL ORDER BY date", (s["code"],))]
            df = prepare_frame(data)
            if len(df) < 30:
                continue
            last_date = str(df.iloc[-1]["date"])
            for name, fn in STRATEGIES:
                if fn((s["code"], s["name"]), df, date=last_date):
                    signals.append((run_at, name, s["code"], s["name"], last_date,
                                    float(df.iloc[-1]["close"])))
        conn.execute("DELETE FROM strategy_signals")
        conn.executemany(
            "INSERT INTO strategy_signals (run_at, strategy, code, name, date, close) "
            "VALUES (?, ?, ?, ?, ?, ?)", signals)
        conn.commit()
        return {"run_at": run_at, "count": len(signals)}
    finally:
        conn.close()


def get_signals(conn) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT run_at, strategy, code, name, date, close FROM strategy_signals "
        "ORDER BY strategy, code")]
    run_at = rows[0]["run_at"] if rows else None
    return {"run_at": run_at, "signals": rows}


def get_backtest(conn) -> dict:
    rows = [dict(r) for r in conn.execute(
        "SELECT run_at, strategy, hold_days, year, samples, win_rate, avg_return, max_drawdown "
        "FROM backtest_stats ORDER BY strategy, hold_days, "
        "CASE year WHEN 'ALL' THEN 1 ELSE 0 END, year")]
    run_at = rows[0]["run_at"] if rows else None
    return {"run_at": run_at, "stats": rows}
