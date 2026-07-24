"""模擬引擎：建倉、DCA、快照、再平衡。純計算，IO 經 store/quote_fn/fx_fn。"""
import json


def price_twd(target, quote_fn, fx_fn) -> float:
    p = quote_fn(target.ticker, target.market)
    return p * fx_fn() if target.market == "US" else p


def target_shares(target_twd: float, price_twd: float) -> float:
    return target_twd / price_twd


def _buy(conn, account_id, date, target, amount_twd, quote_fn, fx_fn, tranche_no):
    native = quote_fn(target.ticker, target.market)
    fx = fx_fn() if target.market == "US" else 1.0
    ptwd = native * fx
    shares = amount_twd / ptwd
    import store
    store.add_trade(conn, account_id, date, target.ticker, target.market,
                    shares, native, fx, amount_twd, tranche_no)


def build_lump(conn, account_id, plan, date, quote_fn, fx_fn) -> None:
    for t in plan.targets:
        if t.build_method == "lump":
            _buy(conn, account_id, date, t, t.target_twd, quote_fn, fx_fn, 0)


def run_dca_tranche(conn, account_id, plan, date, tranche_no, quote_fn, fx_fn) -> None:
    for t in plan.targets:
        if t.build_method == "dca":
            amount = t.target_twd / plan.dca_months
            _buy(conn, account_id, date, t, amount, quote_fn, fx_fn, tranche_no)


def holdings(conn, account_id) -> dict:
    import store
    agg: dict = {}
    for tr in store.get_trades(conn, account_id):
        h = agg.setdefault(tr["ticker"], {
            "shares": 0.0, "market": tr["market"], "cost_twd": 0.0,
        })
        h["shares"] += tr["shares"]
        h["cost_twd"] += tr["cost_twd"]
    return agg


def _category_of(plan, ticker):
    for t in plan.targets:
        if t.ticker == ticker:
            return t.category
    return "其他"


def daily_snapshot(conn, account_id, plan, date, quote_fn, fx_fn) -> dict:
    import store
    hs = holdings(conn, account_id)
    fx = fx_fn()
    market_value = 0.0
    invested = 0.0
    by_cat: dict = {}
    for ticker, h in hs.items():
        native = quote_fn(ticker, h["market"])
        ptwd = native * fx if h["market"] == "US" else native
        mv = h["shares"] * ptwd
        market_value += mv
        invested += h["cost_twd"]
        cat = _category_of(plan, ticker)
        by_cat[cat] = by_cat.get(cat, 0.0) + mv
    cash = plan.capital_twd - invested
    total = cash + market_value
    pnl = market_value - invested
    store.add_snapshot(conn, account_id, date, total, cash, pnl, json.dumps(by_cat))
    return {"total_value_twd": total, "cash_twd": cash,
            "unrealized_pnl_twd": pnl, "by_category": by_cat}


DRIFT_PP = 5.0          # 百分點
SATELLITE_MULT = 1.5
SATELLITE_CAT = "AI基建衛星"


def check_rebalance(conn, account_id, plan, date, quote_fn, fx_fn) -> list:
    snap = daily_snapshot(conn, account_id, plan, date, quote_fn, fx_fn)
    total = snap["total_value_twd"]
    by_cat = snap["by_category"]
    signals = []
    # 目標比例（依 capital）
    target_by_cat: dict = {}
    for t in plan.targets:
        target_by_cat[t.category] = target_by_cat.get(t.category, 0.0) + t.target_twd
    for cat, tgt_twd in target_by_cat.items():
        actual_pct = 100.0 * by_cat.get(cat, 0.0) / total if total else 0.0
        target_pct = 100.0 * tgt_twd / plan.capital_twd
        if abs(actual_pct - target_pct) > DRIFT_PP:
            signals.append({"type": "drift", "category": cat,
                            "actual_pct": round(actual_pct, 2),
                            "target_pct": round(target_pct, 2)})
    # 衛星獲利了結
    sat_mv = by_cat.get(SATELLITE_CAT, 0.0)
    sat_tgt = target_by_cat.get(SATELLITE_CAT, 0.0)
    if sat_tgt and sat_mv > sat_tgt * SATELLITE_MULT:
        signals.append({"type": "satellite_take_profit",
                        "market_value": round(sat_mv, 0),
                        "target": round(sat_tgt, 0)})
    return signals
