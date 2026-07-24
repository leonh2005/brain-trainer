"""一次性初始化：建立帳戶 A/B，執行建帳日建倉 + 首批 DCA + 快照。"""
from datetime import date
import store, quotes
from jobs import daily
from plans import PLANS

conn = store.connect("sim.db")
today = date.today().isoformat()
for aid, plan in PLANS.items():
    if store.get_account(conn, aid) is None:
        store.create_account(conn, aid, plan.name, aid, today, plan.capital_twd)
    daily.run_day(conn, aid, today, quotes.get_quote, quotes.get_fx)
    print(aid, store.latest_snapshot(conn, aid)["total_value_twd"])
