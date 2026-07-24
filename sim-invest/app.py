"""模擬投資 Flask 服務（port 5250，唯讀報價，絕不真實下單）。"""
import json
import os
from flask import Flask, g, jsonify, render_template

import store
import engine
from plans import PLANS

app = Flask(__name__)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("SIM_DB", os.path.join(_BASE_DIR, "sim.db"))


def _conn():
    if "db" not in g:
        g.db = store.connect(DB)
    return g.db


@app.teardown_appcontext
def _close_conn(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@app.get("/api/health")
def health():
    return jsonify(status="ok", service="sim-invest")


@app.get("/api/accounts")
def accounts():
    return jsonify([{"id": p.plan_id, "name": p.name, "capital_twd": p.capital_twd}
                    for p in PLANS.values()])


@app.get("/api/account/<aid>")
def account(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    plan = PLANS[aid]
    conn = _conn()
    hs = engine.holdings(conn, aid)
    latest = store.latest_snapshot(conn, aid)
    by_ticker = {}
    if latest and latest["by_ticker_json"]:
        by_ticker = json.loads(latest["by_ticker_json"])
    holdings = []
    for k, v in hs.items():
        market_value = by_ticker.get(k, {}).get("market_value", v["cost_twd"])
        holdings.append({"ticker": k, **v, "market_value": market_value})
    return jsonify(
        account={"id": aid, "name": plan.name, "capital_twd": plan.capital_twd},
        holdings=holdings,
        targets=[{"ticker": t.ticker, "category": t.category,
                  "target_twd": t.target_twd, "build_method": t.build_method,
                  "target_pct": round(100 * t.target_twd / plan.capital_twd, 2)}
                 for t in plan.targets],
        latest=dict(latest) if latest else None,
    )


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
    sigs = engine.check_rebalance(conn, aid, PLANS[aid])
    return jsonify(sigs)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5250)
