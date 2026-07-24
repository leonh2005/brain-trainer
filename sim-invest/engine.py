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
