"""每日排程：到期 DCA（補投模型）+ 淨值快照。"""
import calendar
from datetime import date as _date

import store
import engine
from plans import PLANS


def _parse(d: str) -> _date:
    y, m, dd = map(int, d.split("-"))
    return _date(y, m, dd)


def _add_months_clamped(d: _date, months: int) -> _date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return _date(year, month, day)


def tranches_due(start_date, today, dca_months) -> int:
    """回傳到 today 為止應該已投入的 DCA 期數(1..dca_months)，以月週年 + 月底 clamp 判斷。"""
    s, t = _parse(start_date), _parse(today)
    due = 1
    for k in range(1, dca_months):
        anniv = _add_months_clamped(s, k)
        if anniv <= t:
            due = k + 1
        else:
            break
    return min(due, dca_months)


def run_day(conn, account_id, today, quote_fn, fx_fn) -> dict:
    plan = PLANS[account_id]
    acct = store.get_account(conn, account_id)
    start = acct["start_date"]
    existing = store.get_trades(conn, account_id)
    if not existing:                       # 建帳日：先一次建倉
        engine.build_lump(conn, account_id, plan, today, quote_fn, fx_fn)
    due = tranches_due(start, today, plan.dca_months)
    invested = {tr["tranche_no"] for tr in store.get_trades(conn, account_id)
                if tr["tranche_no"] >= 1}
    for n in range(1, due + 1):
        if n not in invested:
            engine.run_dca_tranche(conn, account_id, plan, today, n, quote_fn, fx_fn)
    return engine.daily_snapshot(conn, account_id, plan, today, quote_fn, fx_fn)
