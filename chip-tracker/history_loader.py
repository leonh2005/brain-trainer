# -*- coding: utf-8 -*-
"""FinMind 歷史資料回補：日K + 已發行股數 + 三大法人。

補進既有 daily 表（不新增欄位），另建 daily_ext 存 turnover / amplitude / amount。
寫入前會把 2026-06 以後的 FinMind 值與 chip.db 現有值逐筆比對，不一致就中止且不寫入。
"""

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = "/Users/steven/CCProject/.secrets/finmind_token.txt"
FM_URL = "https://api.finmindtrade.com/api/v4/data"
USAGE_PATH = os.path.join(BASE_DIR, "data", "finmind_usage.json")
REPORT_PATH = os.path.join(BASE_DIR, "data", "history_verify_mismatch.json")

HISTORY_START = "2022-10-01"   # 比回測起點多一年 warmup（MA250 用）
VERIFY_FROM = "2026-06-01"     # 與 chip.db 現有值比對的起點
HOURLY_LIMIT = 590             # FinMind 免費額度 600/小時，留 10 次餘裕
CALL_INTERVAL = 0.35           # 秒，避免瞬時爆量
PRICE_LOOKBACK_DAYS = 15       # 抓價時多抓前面幾天，用來算第一筆振幅

_FOREIGN = ("Foreign_Investor",)
_TRUST = ("Investment_Trust",)
_DEALER = ("Dealer_self", "Dealer_Hedging")


# ── FinMind 呼叫 ──────────────────────────────────────────────


def _read_token() -> str:
    with open(TOKEN_PATH) as f:
        return f.read().strip()


def _load_usage() -> list:
    try:
        with open(USAGE_PATH) as f:
            return [t for t in json.load(f) if time.time() - t < 3600]
    except (OSError, ValueError):
        return []


def _save_usage(stamps: list) -> None:
    os.makedirs(os.path.dirname(USAGE_PATH), exist_ok=True)
    with open(USAGE_PATH, "w") as f:
        json.dump(stamps, f)


def fm_get(dataset: str, data_id: str, start: str, end: str) -> list:
    """單次 FinMind 查詢，含每小時 600 次配額保護。"""
    stamps = _load_usage()
    if len(stamps) >= HOURLY_LIMIT:
        wait = int(3600 - (time.time() - min(stamps))) // 60 + 1
        raise RuntimeError(f"FinMind 每小時額度已滿（{len(stamps)} 次），請約 {wait} 分鐘後再試")
    params = urllib.parse.urlencode({
        "dataset": dataset, "data_id": data_id,
        "start_date": start, "end_date": end, "token": _read_token(),
    })
    req = urllib.request.Request(f"{FM_URL}?{params}", headers={"User-Agent": "chip-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    stamps.append(time.time())
    _save_usage(stamps)
    if body.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset} {data_id} 錯誤: {body.get('msg')}")
    time.sleep(CALL_INTERVAL)
    return body.get("data", [])


# ── 資料組裝 ──────────────────────────────────────────────────


def aggregate_institutional(rows: list) -> dict:
    """法人列 → {date: (foreign_net, trust_net, dealer_net)}，單位張（int 向零截斷）。"""
    acc = {}
    for r in rows:
        d = acc.setdefault(r["date"], {"foreign": 0, "trust": 0, "dealer": 0})
        net = int(r.get("buy", 0) or 0) - int(r.get("sell", 0) or 0)
        name = r.get("name", "")
        if name in _FOREIGN:
            d["foreign"] += net
        elif name in _TRUST:
            d["trust"] += net
        elif name in _DEALER:
            d["dealer"] += net
    return {
        d: (int(v["foreign"] / 1000), int(v["trust"] / 1000), int(v["dealer"] / 1000))
        for d, v in acc.items()
    }


def shares_by_date(rows: list) -> dict:
    """TaiwanStockShareholding → {date: NumberOfSharesIssued}。"""
    out = {}
    for r in rows:
        shares = r.get("NumberOfSharesIssued")
        if shares:
            out[r["date"]] = int(shares)
    return out


def build_rows(price_rows: list, shares: dict, inst: dict, prev_close: float = None) -> list:
    """合併三份來源成每日紀錄（純函式，供測試）。

    amplitude 用「前一日收盤」；turnover = 成交量(股) / 已發行股數 × 100。
    FinMind 偶有 OHLC 全 0 的壞列（例如 4542 2022-10），直接丟棄。
    """
    rows = []
    last_close = prev_close
    shares_iter = iter(sorted(shares.items()))
    shares_date, shares_value = next(shares_iter, (None, None))
    for r in sorted((x for x in price_rows if (x.get("close") or 0) > 0), key=lambda x: x["date"]):
        d = r["date"]
        while shares_date is not None and shares_date <= d:
            shares_value = shares[shares_date]
            shares_date, _ = next(shares_iter, (None, None))
        close = r.get("close")
        high, low = r.get("max"), r.get("min")
        volume = r.get("Trading_Volume")
        amplitude = round((high - low) / last_close * 100, 4) if last_close and high is not None and low is not None else None
        turnover = round(volume / shares_value * 100, 4) if volume and shares_value else None
        f, t, dl = inst.get(d, (None, None, None))
        rows.append({
            "date": d, "open": r.get("open"), "high": high, "low": low, "close": close,
            "volume": volume, "amount": r.get("Trading_money"),
            "turnover": turnover, "amplitude": amplitude,
            "foreign_net": f, "trust_net": t, "dealer_net": dl,
            "total_net": (f + t + dl) if f is not None else None,
        })
        if close is not None:
            last_close = close
    return rows


# ── 驗證 ──────────────────────────────────────────────────────


def _db_prev_close(conn, code: str, before: str):
    row = conn.execute(
        "SELECT close FROM daily WHERE code = ? AND date < ? AND close > 0 "
        "ORDER BY date DESC LIMIT 1", (code, before)
    ).fetchone()
    return row["close"] if row else None


def verify_against_db(conn, code: str, rows: list, since: str = VERIFY_FROM) -> tuple:
    """2026-06 起逐筆比對 OHLCV + 三大法人。

    回傳 (blocking, rounding)：
    - DB 沒值 = 當時還沒補這欄，不算不一致（blocking 不列）。
    - 法人張數差 1 且同號 = 舊資料用 floor、現行用 int() 向零截斷的捨入差異（rounding 另列）。
    """
    fields = ("open", "high", "low", "close", "volume", "foreign_net", "trust_net", "dealer_net")
    net_fields = ("foreign_net", "trust_net", "dealer_net")
    blocking, rounding = [], []
    for r in rows:
        if r["date"] < since:
            continue
        old = conn.execute(
            "SELECT open, high, low, close, volume, foreign_net, trust_net, dealer_net "
            "FROM daily WHERE code = ? AND date = ?", (code, r["date"])
        ).fetchone()
        if not old:
            continue
        for f in fields:
            a, b = r[f], old[f]
            if a is None and b is None:
                continue
            if b is None:
                continue  # DB 缺值不算「不一致」
            if isinstance(a, float) or isinstance(b, float):
                same = a is not None and abs(float(a) - float(b)) < 1e-6
            else:
                same = a == b
            if same:
                continue
            item = {"code": code, "date": r["date"], "field": f, "finmind": a, "db": b}
            if (f in net_fields and a is not None
                    and abs(a - b) == 1 and (a > 0) == (b > 0)):
                rounding.append(item)
            else:
                blocking.append(item)
    return blocking, rounding


# ── 主流程 ────────────────────────────────────────────────────


def _write_rows(conn, code: str, rows: list) -> tuple:
    for r in rows:
        db.upsert_daily(
            conn, code, r["date"],
            close=r["close"], open=r["open"], high=r["high"], low=r["low"], volume=r["volume"],
            foreign_net=r["foreign_net"], trust_net=r["trust_net"], dealer_net=r["dealer_net"],
            total_net=r["total_net"],
        )
        conn.execute(
            "INSERT INTO daily_ext (code, date, turnover, amplitude, amount) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(code, date) DO UPDATE SET turnover=excluded.turnover, "
            "amplitude=excluded.amplitude, amount=excluded.amount",
            (code, r["date"], r["turnover"], r["amplitude"], r["amount"]),
        )
    conn.commit()
    return len(rows)


def load_history(codes: list = None, start: str = HISTORY_START, end: str = None,
                 progress=None, verify_only: bool = False) -> dict:
    """補齊所有股票歷史。回傳摘要；驗證不一致時 aborted=True 且完全不寫入。"""
    end = end or date.today().isoformat()
    conn = db.get_conn()
    try:
        if codes is None:
            codes = [r["code"] for r in conn.execute("SELECT code FROM stocks ORDER BY code")]
        fetched = {}
        errors = []
        for i, code in enumerate(codes):
            if progress:
                progress(i, len(codes), f"抓取 {code} ({i + 1}/{len(codes)})")
            try:
                price_start = (datetime.strptime(start, "%Y-%m-%d")
                               - timedelta(days=PRICE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
                prices = fm_get("TaiwanStockPrice", code, price_start, end)
                shares = shares_by_date(fm_get("TaiwanStockShareholding", code, price_start, end))
                inst = aggregate_institutional(
                    fm_get("TaiwanStockInstitutionalInvestorsBuySell", code, price_start, end))
                prev_close = _db_prev_close(conn, code, price_start)
                rows = [r for r in build_rows(prices, shares, inst, prev_close) if r["date"] >= start]
                fetched[code] = rows
            except Exception as e:  # 單檔失敗不中斷其他股票
                errors.append(f"{code}: {e}")

        blocking, rounding = [], []
        for code, rows in fetched.items():
            b, r = verify_against_db(conn, code, rows)
            blocking.extend(b)
            rounding.extend(r)
        if blocking:
            os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
            with open(REPORT_PATH, "w") as f:
                json.dump(blocking, f, ensure_ascii=False, indent=2)
            return {"aborted": True, "mismatches": blocking, "errors": errors,
                    "report": REPORT_PATH, "codes": len(fetched)}

        written = 0
        if os.path.exists(REPORT_PATH):
            os.remove(REPORT_PATH)  # 上次的不一致報告已過期
        if not verify_only:
            for i, (code, rows) in enumerate(fetched.items()):
                if progress:
                    progress(i, len(fetched), f"寫入 {code} ({i + 1}/{len(fetched)})")
                written += _write_rows(conn, code, rows)
        return {"aborted": False, "codes": len(fetched), "daily_rows": written,
                "mismatches": [], "rounding_diffs": rounding, "errors": errors,
                "start": start, "end": end}
    finally:
        conn.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)
    result = load_history(progress=lambda i, n, m: print(f"[{i + 1}/{n}] {m}"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
