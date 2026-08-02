# -*- coding: utf-8 -*-
"""guru-tracker 資料抓取：13F 申報 + ARK 每日持股 + sector 分類 + Steven周交集。

由 LaunchAgent 排程觸發，跑一次就結束。用 fcntl 檔案鎖防止重疊執行。
"""

import csv
import fcntl
import io
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime

import requests

import config
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_PATH = os.path.join(BASE_DIR, ".updater.lock")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("guru-tracker-updater")

HEADERS = {"User-Agent": config.SEC_USER_AGENT}
INFOTABLE_NS = {"n": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}


def _sec_get(url: str):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r


def _report_period(report_date: str) -> str:
    """'2026-03-31' -> '2026-Q1'"""
    y, m, _ = report_date.split("-")
    q = (int(m) - 1) // 3 + 1
    return f"{y}-Q{q}"


def compute_weight_pct(value_usd: float, total_value_usd: float) -> float:
    """單一持股占該 holder 總市值的百分比；total 為 0/None 時回傳 0 避免除零。"""
    if not total_value_usd:
        return 0.0
    return value_usd / total_value_usd * 100


def latest_13f_filing(cik: str) -> dict | None:
    r = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = r.json()["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form == "13F-HR":
            return {
                "accession": recent["accessionNumber"][i],
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
            }
    return None


def parse_infotable_xml(xml_text: str) -> list:
    root = ET.fromstring(xml_text)
    out = []
    for info in root.findall("n:infoTable", INFOTABLE_NS):
        issuer = info.findtext("n:nameOfIssuer", default="", namespaces=INFOTABLE_NS)
        cusip = (info.findtext("n:cusip", default="", namespaces=INFOTABLE_NS) or "").strip()
        value_raw = info.findtext("n:value", default="0", namespaces=INFOTABLE_NS) or "0"
        shares_raw = info.findtext(
            "n:shrsOrPrnAmt/n:sshPrnamt", default="0", namespaces=INFOTABLE_NS
        ) or "0"
        if not cusip:
            continue
        out.append({
            "issuer": issuer,
            "cusip": cusip,
            "value_usd": float(value_raw),
            "shares": float(shares_raw),
        })
    return out


def fetch_13f_holdings(cik: str, accession: str) -> list:
    """回傳該次申報彙整後的持股（同一 CUSIP 跨多個 infoTable 條目已加總）"""
    accession_nodash = accession.replace("-", "")
    cik_int = str(int(cik))
    idx = _sec_get(
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/index.json"
    ).json()
    xml_files = [
        f["name"] for f in idx["directory"]["item"]
        if f["name"].endswith(".xml") and f["name"] != "primary_doc.xml"
    ]

    raw = []
    for fname in xml_files:
        xml_text = _sec_get(
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{fname}"
        ).text
        if "informationTable" not in xml_text:
            continue
        raw.extend(parse_infotable_xml(xml_text))

    # 同一 CUSIP 可能因 otherManager 拆成多筆條目，加總後才是該 filer 的真實部位
    merged: dict = {}
    for row in raw:
        m = merged.setdefault(row["cusip"], {"issuer": row["issuer"], "cusip": row["cusip"],
                                              "value_usd": 0.0, "shares": 0.0})
        m["value_usd"] += row["value_usd"]
        m["shares"] += row["shares"]
    return list(merged.values())


def resolve_cusips(conn, cusips: list) -> dict:
    """CUSIP -> ticker，優先讀快取，缺的才批次打 OpenFIGI（免金鑰限制保守設計：每批10筆）"""
    unresolved = [c for c in dict.fromkeys(cusips) if not db.cusip_already_resolved(conn, c)]
    now = datetime.now().isoformat()
    batch_size = 10
    for i in range(0, len(unresolved), batch_size):
        batch = unresolved[i:i + batch_size]
        payload = [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        try:
            r = requests.post(config.OPENFIGI_URL, json=payload, timeout=30)
            r.raise_for_status()
            results = r.json()
        except Exception as e:
            log.warning(f"OpenFIGI 批次失敗，略過本批：{e}")
            for c in batch:
                db.upsert_cusip_ticker(conn, c, None, now)
            conn.commit()
            time.sleep(3)
            continue
        for c, res in zip(batch, results):
            ticker = None
            if isinstance(res, dict) and res.get("data"):
                ticker = res["data"][0].get("ticker")
            db.upsert_cusip_ticker(conn, c, ticker, now)
        conn.commit()
        time.sleep(3)  # 免金鑰限速保守設計
    return {c: db.get_cusip_ticker(conn, c) for c in cusips}


def update_13f_holder(conn, holder: dict) -> None:
    filing = latest_13f_filing(holder["cik"])
    if not filing:
        log.warning(f"{holder['name']}: 找不到 13F-HR 申報，跳過")
        return
    period = _report_period(filing["report_date"])

    existing_periods = db.latest_periods(conn, holder["id"], limit=1)
    if existing_periods and existing_periods[0] == period:
        log.info(f"{holder['name']}: {period} 已是最新資料，跳過")
        db.touch_holder(conn, holder["id"], datetime.now().isoformat())
        conn.commit()
        return

    holdings = fetch_13f_holdings(holder["cik"], filing["accession"])
    if not holdings:
        log.warning(f"{holder['name']}: {period} 申報解析出0筆持股，跳過寫入")
        return

    cusips = [h["cusip"] for h in holdings]
    ticker_map = resolve_cusips(conn, cusips)

    total_value = sum(h["value_usd"] for h in holdings) or 1.0
    db.clear_snapshot_period(conn, holder["id"], period)
    written = 0
    for h in holdings:
        ticker = ticker_map.get(h["cusip"])
        if not ticker:
            continue  # 無法識別的 CUSIP（非美股/衍生性商品等），不猜測代碼
        db.upsert_ticker_meta(conn, ticker, h["issuer"])
        weight_pct = compute_weight_pct(h["value_usd"], total_value)
        db.upsert_snapshot(conn, holder["id"], ticker, period,
                            h["shares"], h["value_usd"], weight_pct, filing["filing_date"])
        written += 1
    db.touch_holder(conn, holder["id"], datetime.now().isoformat())
    conn.commit()
    log.info(f"{holder['name']}: {period} 寫入 {written}/{len(holdings)} 檔持股（其餘 CUSIP 無法識別）")


def update_ark_holder(conn, holder: dict) -> None:
    today = date.today().isoformat()
    existing_periods = db.latest_periods(conn, holder["id"], limit=1)
    if existing_periods and existing_periods[0] == today:
        log.info(f"{holder['name']}: {today} 已是最新資料，跳過")
        db.touch_holder(conn, holder["id"], datetime.now().isoformat())
        conn.commit()
        return

    agg: dict = {}
    got_any = False
    for fund, url in config.ARK_HOLDINGS_URLS.items():
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            log.warning(f"ARK {fund} CSV 下載失敗：{e}")
            continue
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue
            value_str = (row.get("market value ($)") or "0").replace("$", "").replace(",", "")
            shares_str = (row.get("shares") or "0").replace(",", "")
            try:
                value = float(value_str)
                shares = float(shares_str)
            except ValueError:
                continue
            a = agg.setdefault(ticker, {"name": row.get("company", ""), "value_usd": 0.0, "shares": 0.0})
            a["value_usd"] += value
            a["shares"] += shares
            got_any = True

    if not got_any:
        log.warning(f"{holder['name']}: ARK 全部 CSV 下載/解析失敗，跳過寫入")
        return

    total_value = sum(v["value_usd"] for v in agg.values()) or 1.0
    db.clear_snapshot_period(conn, holder["id"], today)
    for ticker, v in agg.items():
        db.upsert_ticker_meta(conn, ticker, v["name"])
        weight_pct = compute_weight_pct(v["value_usd"], total_value)
        db.upsert_snapshot(conn, holder["id"], ticker, today, v["shares"], v["value_usd"], weight_pct, today)
    db.touch_holder(conn, holder["id"], datetime.now().isoformat())
    conn.commit()
    log.info(f"{holder['name']}: {today} 寫入 {len(agg)} 檔持股")


def update_sectors(conn) -> None:
    import yfinance as yf

    missing = db.tickers_missing_sector(conn)
    if not missing:
        log.info("sector 分類：無新 ticker 需要補齊")
        return
    log.info(f"sector 分類：{len(missing)} 檔待查")
    now = datetime.now().isoformat()
    for ticker in missing:
        try:
            info = yf.Ticker(ticker).info
            sector = info.get("sector") or "未分類"
            industry = info.get("industry") or "未分類"
        except Exception as e:
            log.warning(f"{ticker} sector 查詢失敗：{e}")
            sector, industry = "未分類", "未分類"
        db.set_ticker_sector(conn, ticker, sector, industry, now)
        conn.commit()


def compute_steven_zhou(conn) -> None:
    real_holder_ids = [h["id"] for h in config.HOLDERS]
    threshold = int(db.get_config(conn, "steven_zhou_threshold", str(config.STEVEN_ZHOU_THRESHOLD_DEFAULT)))

    snapshot_by_holder = db.latest_snapshot_all_holders(conn, real_holder_ids)
    ticker_holders: dict = {}
    for hid, rows in snapshot_by_holder.items():
        for row in rows:
            ticker_holders.setdefault(row["ticker"], set()).add(hid)

    qualified = {t: holders for t, holders in ticker_holders.items() if len(holders) >= threshold}
    if not qualified:
        log.warning(f"Steven周：門檻 >={threshold} 人同時持有下無任何交集股票，維持空持股")

    period = date.today().isoformat()
    db.clear_snapshot_period(conn, config.STEVEN_ZHOU_ID, period)
    if qualified:
        weight = 100.0 / len(qualified)
        for ticker in qualified:
            db.upsert_snapshot(conn, config.STEVEN_ZHOU_ID, ticker, period, None, None, weight, period)
    db.touch_holder(conn, config.STEVEN_ZHOU_ID, datetime.now().isoformat())
    conn.commit()
    log.info(f"Steven周：門檻>={threshold}人，交集 {len(qualified)} 檔，均分權重 {100.0/len(qualified) if qualified else 0:.2f}%")


def main():
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("已有 updater 在跑，本次跳過")
        lock_file.close()
        return

    conn = db.get_conn()
    try:
        for holder in config.HOLDERS:
            db.upsert_holder(conn, holder["id"], holder["name"], holder["name_zh"],
                              holder["type"], holder["cik"], holder["source"])
        db.upsert_holder(conn, config.STEVEN_ZHOU_ID, "Steven Zhou", config.STEVEN_ZHOU_NAME_ZH,
                          "virtual", None, "12位大老持股交集（均分權重）")
        if db.get_config(conn, "steven_zhou_threshold") is None:
            db.set_config(conn, "steven_zhou_threshold", str(config.STEVEN_ZHOU_THRESHOLD_DEFAULT))

        for holder in config.HOLDERS:
            try:
                if holder["type"] == "13f":
                    update_13f_holder(conn, holder)
                elif holder["type"] == "ark":
                    update_ark_holder(conn, holder)
            except Exception as e:
                log.error(f"{holder['name']} 更新失敗：{e}", exc_info=True)
                continue  # fail-open：單一來源失敗不影響其他人

        update_sectors(conn)
        compute_steven_zhou(conn)
        log.info("本次 updater 執行完成")
    finally:
        conn.close()
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
