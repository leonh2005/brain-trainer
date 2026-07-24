"""每日排程：到期 DCA + 淨值快照。"""
from datetime import date as _date

import store
import engine
from plans import PLANS


def _parse(d: str) -> _date:
    y, m, dd = map(int, d.split("-"))
    return _date(y, m, dd)


def due_tranche(start_date, today, dca_months):
    s, t = _parse(start_date), _parse(today)
    if t.day != s.day:
        return None
    months = (t.year - s.year) * 12 + (t.month - s.month)
    if 0 <= months < dca_months:
        return months + 1
    return None


def run_day(conn, account_id, today, quote_fn, fx_fn) -> dict:
    plan = PLANS[account_id]
    acct = store.get_account(conn, account_id)
    start = acct["start_date"]
    existing = store.get_trades(conn, account_id)
    if not existing:                       # 建帳日：先一次建倉
        engine.build_lump(conn, account_id, plan, today, quote_fn, fx_fn)
    n = due_tranche(start, today, plan.dca_months)
    if n is not None:
        already = {tr["tranche_no"] for tr in store.get_trades(conn, account_id)}
        if n not in already:
            engine.run_dca_tranche(conn, account_id, plan, today, n, quote_fn, fx_fn)
    return engine.daily_snapshot(conn, account_id, plan, today, quote_fn, fx_fn)
