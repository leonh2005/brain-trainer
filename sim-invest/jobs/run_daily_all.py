"""LaunchAgent 進入點：對所有帳戶跑 run_day（今日）。"""
import os
import sys
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT_DIR)

from datetime import date
import store, quotes
from jobs import daily
from plans import PLANS

DB = os.environ.get("SIM_DB", os.path.join(_ROOT_DIR, "sim.db"))
conn = store.connect(DB)
today = date.today().isoformat()
for aid in PLANS:
    daily.run_day(conn, aid, today, quotes.get_quote, quotes.get_fx)
    print(aid, "ok", today)
