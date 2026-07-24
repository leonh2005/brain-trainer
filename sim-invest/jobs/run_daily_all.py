"""LaunchAgent 進入點：對所有帳戶跑 run_day（今日）。"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
import store, quotes
from jobs import daily
from plans import PLANS

conn = store.connect("sim.db")
today = date.today().isoformat()
for aid in PLANS:
    daily.run_day(conn, aid, today, quotes.get_quote, quotes.get_fx)
    print(aid, "ok", today)
