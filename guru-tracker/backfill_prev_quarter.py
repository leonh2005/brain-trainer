# -*- coding: utf-8 -*-
"""一次性補跑：把每位13F持有人的「上一季」申報也存進DB，讓最新一期能立刻算出增減。

不是常態排程的一部分，手動執行一次即可（之後 updater.py 每次抓新一季時，
資料庫自然就會累積出至少兩期可比較）。
"""

import logging
import sys
from datetime import datetime

import config
import db
import updater

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                     handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("backfill")


def second_latest_13f_filing(cik: str) -> dict | None:
    r = updater._sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = r.json()["filings"]["recent"]
    found = 0
    for i, form in enumerate(recent["form"]):
        if form == "13F-HR":
            found += 1
            if found == 2:
                return {
                    "accession": recent["accessionNumber"][i],
                    "filing_date": recent["filingDate"][i],
                    "report_date": recent["reportDate"][i],
                }
    return None


def main():
    conn = db.get_conn()
    try:
        for holder in config.HOLDERS:
            if holder["type"] != "13f":
                continue
            filing = second_latest_13f_filing(holder["cik"])
            if not filing:
                log.warning(f"{holder['name']}: 找不到上一季 13F-HR，跳過")
                continue
            period = updater._report_period(filing["report_date"])
            if db.latest_periods(conn, holder["id"], limit=1) and period in db.latest_periods(conn, holder["id"], limit=5):
                log.info(f"{holder['name']}: {period} 已存在，跳過")
                continue

            holdings = updater.fetch_13f_holdings(holder["cik"], filing["accession"])
            if not holdings:
                log.warning(f"{holder['name']}: {period} 解析出0筆持股，跳過")
                continue
            cusips = [h["cusip"] for h in holdings]
            ticker_map = updater.resolve_cusips(conn, cusips)
            total_value = sum(h["value_usd"] for h in holdings) or 1.0
            db.clear_snapshot_period(conn, holder["id"], period)
            written = 0
            for h in holdings:
                ticker = ticker_map.get(h["cusip"])
                if not ticker:
                    continue
                db.upsert_ticker_meta(conn, ticker, h["issuer"])
                weight_pct = updater.compute_weight_pct(h["value_usd"], total_value)
                db.upsert_snapshot(conn, holder["id"], ticker, period, h["shares"], h["value_usd"],
                                    weight_pct, filing["filing_date"])
                written += 1
            conn.commit()
            log.info(f"{holder['name']}: 補入上一季 {period}，寫入 {written}/{len(holdings)} 檔")
        log.info("補跑完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
