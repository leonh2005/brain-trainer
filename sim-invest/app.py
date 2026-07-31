"""模擬投資 Flask 服務（port 5250，唯讀報價，絕不真實下單）。"""
import json
import os
from datetime import date
from flask import Flask, g, jsonify, render_template, request

import store
import engine
import plans as plans_mod
import quotes
from plans import PLANS

app = Flask(__name__)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("SIM_DB", os.path.join(_BASE_DIR, "sim.db"))

_startup_conn = store.connect(DB)
plans_mod.ensure_accounts(_startup_conn)
plans_mod.seed_defaults(_startup_conn)
_startup_conn.close()


def _conn():
    if "db" not in g:
        g.db = store.connect(DB)
    return g.db


@app.teardown_appcontext
def _close_conn(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _account_payload(aid, conn):
    plan = plans_mod.load_plan(conn, aid)
    hs = engine.holdings(conn, aid)
    latest = store.latest_snapshot(conn, aid)
    by_ticker = {}
    if latest and latest["by_ticker_json"]:
        by_ticker = json.loads(latest["by_ticker_json"])
    holdings = []
    for k, v in hs.items():
        market_value = by_ticker.get(k, {}).get("market_value", v["cost_twd"])
        holdings.append({"ticker": k, **v, "market_value": market_value})
    realized_pnl_twd = sum(v["realized_pnl_twd"] for v in hs.values())
    return dict(
        account={"id": aid, "name": plan.name, "capital_twd": plan.capital_twd,
                 "realized_pnl_twd": realized_pnl_twd},
        holdings=holdings,
        targets=[{"id": t.id, "ticker": t.ticker, "market": t.market,
                  "category": t.category, "target_twd": t.target_twd,
                  "build_method": t.build_method,
                  "target_pct": round(100 * t.target_twd / plan.capital_twd, 2)}
                 for t in plan.targets],
        latest=dict(latest) if latest else None,
    )


def _validate_target(data):
    if not str(data.get("ticker", "")).strip():
        return "ticker 不可為空"
    if not str(data.get("category", "")).strip():
        return "category 不可為空"
    if data.get("market") not in ("TW", "US"):
        return "market 必須為 TW 或 US"
    if data.get("build_method") not in ("lump", "dca"):
        return "build_method 必須為 lump 或 dca"
    try:
        if float(data.get("target_twd")) <= 0:
            return "target_twd 必須大於 0"
    except (TypeError, ValueError):
        return "target_twd 必須為數字"
    return None


@app.get("/api/health")
def health():
    return jsonify(status="ok", service="sim-invest")


@app.get("/api/accounts")
def accounts():
    conn = _conn()
    return jsonify([{"id": p.plan_id, "name": p.name,
                     "capital_twd": plans_mod.load_plan(conn, p.plan_id).capital_twd}
                    for p in PLANS.values()])


@app.get("/api/account/<aid>")
def account(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    return jsonify(_account_payload(aid, _conn()))


@app.get("/api/account/<aid>/nav")
def nav(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    conn = _conn()
    return jsonify([{"date": s["date"], "total_value_twd": s["total_value_twd"]}
                    for s in store.get_snapshots(conn, aid)])


@app.get("/api/account/<aid>/rebalance")
def rebalance(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    conn = _conn()
    plan = plans_mod.load_plan(conn, aid)
    sigs = engine.check_rebalance(conn, aid, plan)
    return jsonify(sigs)


@app.get("/api/account/<aid>/live")
def live(aid):
    """即時報價估值（唯讀，手動刷新用）：逐檔現價、當日漲跌%、即時市值與未實現損益。"""
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    conn = _conn()
    plan = plans_mod.load_plan(conn, aid)
    hs = engine.holdings(conn, aid)
    try:
        fx = quotes.get_fx()
    except Exception as e:
        return jsonify(error=f"匯率取得失敗: {e}"), 502
    rows = []
    total_mv = 0.0
    total_cost = 0.0
    tickers = {t.ticker: t.market for t in plan.targets}
    tickers.update({k: h["market"] for k, h in hs.items()})
    for ticker, market in tickers.items():
        h = hs.get(ticker, {"shares": 0.0, "cost_twd": 0.0})
        cost = h["cost_twd"]
        total_cost += cost
        try:
            d = quotes.get_quote_detail(ticker, market)
            ptwd = d["last"] * fx if market == "US" else d["last"]
            mv = h["shares"] * ptwd
            total_mv += mv
            rows.append({"ticker": ticker, "market": market, "shares": h["shares"],
                         "cost_twd": cost, "last_native": d["last"],
                         "change_pct": round(d["change_pct"], 2),
                         "market_value": mv, "pnl": mv - cost})
        except Exception as e:
            total_mv += cost  # 報價失敗以成本占位，維持 total_value 基準與逐檔一致
            rows.append({"ticker": ticker, "market": market, "shares": h["shares"],
                         "cost_twd": cost, "last_native": None, "change_pct": None,
                         "market_value": cost, "pnl": 0.0, "error": str(e)})
    cash = plan.capital_twd - total_cost
    return jsonify(fx=round(fx, 3), holdings=rows, cash_twd=cash,
                   total_value_twd=cash + total_mv)


@app.put("/api/account/<aid>/capital")
def update_capital(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    data = request.get_json(force=True) or {}
    try:
        capital_twd = float(data["capital_twd"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="capital_twd 必須為數字"), 400
    if capital_twd <= 0:
        return jsonify(error="capital_twd 必須大於 0"), 400
    conn = _conn()
    store.update_account_capital(conn, aid, capital_twd)
    return jsonify(_account_payload(aid, conn))


@app.post("/api/account/<aid>/target")
def add_target(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    data = request.get_json(force=True) or {}
    err = _validate_target(data)
    if err:
        return jsonify(error=err), 400
    conn = _conn()
    store.add_target(conn, aid, data["ticker"].strip().upper(), data["market"],
                     data["category"].strip(), float(data["target_twd"]),
                     data["build_method"])
    return jsonify(_account_payload(aid, conn)), 201


@app.put("/api/account/<aid>/target/<int:tid>")
def edit_target(aid, tid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    data = request.get_json(force=True) or {}
    err = _validate_target(data)
    if err:
        return jsonify(error=err), 400
    conn = _conn()
    store.update_target(conn, tid, aid, data["ticker"].strip().upper(), data["market"],
                        data["category"].strip(), float(data["target_twd"]),
                        data["build_method"])
    return jsonify(_account_payload(aid, conn))


@app.delete("/api/account/<aid>/target/<int:tid>")
def remove_target(aid, tid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    conn = _conn()
    store.delete_target(conn, tid, aid)
    return jsonify(_account_payload(aid, conn))


@app.post("/api/account/<aid>/buy")
def buy(aid):
    """加倉：用 NT$ 金額依現價買入。"""
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    data = request.get_json(force=True) or {}
    ticker = str(data.get("ticker", "")).strip().upper()
    market = data.get("market")
    if not ticker:
        return jsonify(error="ticker 不可為空"), 400
    if market not in ("TW", "US"):
        return jsonify(error="market 必須為 TW 或 US"), 400
    try:
        amount_twd = float(data.get("amount_twd"))
    except (TypeError, ValueError):
        return jsonify(error="amount_twd 必須為數字"), 400
    if amount_twd <= 0:
        return jsonify(error="amount_twd 必須大於 0"), 400
    conn = _conn()
    plan = plans_mod.load_plan(conn, aid)
    hs = engine.holdings(conn, aid)
    total_cost = sum(h["cost_twd"] for h in hs.values())
    if amount_twd > plan.capital_twd - total_cost:
        return jsonify(error="超過可用現金"), 400
    try:
        engine.add_position(conn, aid, date.today().isoformat(), ticker, market,
                            amount_twd, quotes.get_quote, quotes.get_fx)
    except Exception as e:
        return jsonify(error=f"報價取得失敗: {e}"), 502
    return jsonify(_account_payload(aid, conn))


@app.post("/api/account/<aid>/sell")
def sell(aid):
    """平倉：依股數或金額（擇一）賣出。"""
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    data = request.get_json(force=True) or {}
    ticker = str(data.get("ticker", "")).strip().upper()
    market = data.get("market")
    if not ticker:
        return jsonify(error="ticker 不可為空"), 400
    if market not in ("TW", "US"):
        return jsonify(error="market 必須為 TW 或 US"), 400
    shares = data.get("shares")
    amount_twd = data.get("amount_twd")
    if shares is None and amount_twd is None:
        return jsonify(error="shares 或 amount_twd 需擇一提供"), 400
    try:
        shares = float(shares) if shares is not None else None
        amount_twd = float(amount_twd) if amount_twd is not None else None
    except (TypeError, ValueError):
        return jsonify(error="shares/amount_twd 必須為數字"), 400
    if (shares is not None and shares <= 0) or (amount_twd is not None and amount_twd <= 0):
        return jsonify(error="shares/amount_twd 必須大於 0"), 400
    conn = _conn()
    try:
        engine.close_position(conn, aid, date.today().isoformat(), ticker, market,
                              shares=shares, amount_twd=amount_twd,
                              quote_fn=quotes.get_quote, fx_fn=quotes.get_fx)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        return jsonify(error=f"報價取得失敗: {e}"), 502
    return jsonify(_account_payload(aid, conn))


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5250)
