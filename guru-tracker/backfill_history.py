# -*- coding: utf-8 -*-
"""一次性補跑：把每位13F持有人近兩年（最多8季）的歷史申報都存進DB，
讓每檔標的能畫出近兩年的持倉曲線。

木頭姐(ARK)是每日資料，SEC沒有申報可回溯，此腳本不處理她——
她的歷史會隨每天排程執行自然累積。Aschenbrenner的基金2026年才成立，
能補到的季數自然有限，不是錯誤。

不是常態排程的一部分，手動執行一次即可。
"""

import logging
import sys

import config
import db
import updater

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                     handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("backfill_history")

QUARTERS_BACK = 8  # 近兩年


def list_13f_filings(cik: str, limit: int) -> list:
    r = updater._sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = r.json()["filings"]["recent"]
    out = []
    for i, form in enumerate(recent["form"]):
        if form == "13F-HR":
            out.append({
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
            })
            if len(out) >= limit:
                break
    return out


def backfill_one(conn, holder, filing) -> None:
    period = updater._report_period(filing["report_date"])
    existing = db.latest_periods(conn, holder["id"], limit=20)
    if period in existing:
        log.info(f"{holder['name']}: {period} 已存在，跳過")
        return

    holdings = updater.fetch_13f_holdings(holder["cik"], filing["accession"])
    if not holdings:
        log.warning(f"{holder['name']}: {period} 解析出0筆持股，跳過")
        return
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
    log.info(f"{holder['name']}: 補入 {period}，寫入 {written}/{len(holdings)} 檔")


def main():
    conn = db.get_conn()
    try:
        for holder in config.HOLDERS:
            if holder["type"] != "13f":
                continue
            filings = list_13f_filings(holder["cik"], QUARTERS_BACK)
            log.info(f"{holder['name']}: 找到 {len(filings)} 季申報（近兩年上限{QUARTERS_BACK}季）")
            for filing in filings:
                backfill_one(conn, holder, filing)
        log.info("歷史補跑完成")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
