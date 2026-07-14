# -*- coding: utf-8 -*-
"""chip-tracker 資料更新器：FinMind（法人/融資/價格）+ TDCC 股權分散（週）。

排程由 LaunchAgent 觸發（08:30/12:00/16:30/21:00），跑一次即退出。
fail-open：單一資料集失敗只記 log，不中斷其他股票/指標。
"""

import fcntl
import io
import json
import logging
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("updater")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = "/Users/steven/CCProject/.secrets/finmind_token.txt"
FM_URL = "https://api.finmindtrade.com/api/v4/data"
TDCC_URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
TDCC_QRY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
TWSE_QFII_URL = "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
TPEX_QFII_URL = "https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&o=json"
LOCK_PATH = os.path.join(BASE_DIR, ".updater.lock")

_FOREIGN_NAMES = ("Foreign_Investor", "Foreign_Dealer_Self")
_TRUST_NAMES = ("Investment_Trust",)
_DEALER_NAMES = ("Dealer_self", "Dealer_Hedging")

_stock_info_cache = None


def _read_token() -> str:
    with open(TOKEN_PATH) as f:
        return f.read().strip()


def fm_get(dataset: str, data_id: str, start: str, end: str) -> list:
    params = urllib.parse.urlencode({
        "dataset": dataset,
        "data_id": data_id,
        "start_date": start,
        "end_date": end,
        "token": _read_token(),
    })
    body = json.loads(urllib.request.urlopen(f"{FM_URL}?{params}", timeout=20).read())
    if body.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset} 錯誤: {body.get('msg')}")
    return body.get("data", [])


def _stock_info() -> dict:
    """TaiwanStockInfo 全表快取 {code: name}。"""
    global _stock_info_cache
    if _stock_info_cache is None:
        params = urllib.parse.urlencode({"dataset": "TaiwanStockInfo", "token": _read_token()})
        body = json.loads(urllib.request.urlopen(f"{FM_URL}?{params}", timeout=20).read())
        if body.get("status") != 200:
            raise RuntimeError(f"FinMind TaiwanStockInfo 錯誤: {body.get('msg')}")
        _stock_info_cache = {r["stock_id"]: r["stock_name"] for r in body.get("data", [])}
    return _stock_info_cache


def resolve_name(code: str):
    """由 TaiwanStockInfo 查股名；查無回 None（兼作代碼驗證）。"""
    return _stock_info().get(code)


def resolve_query(query: str):
    """代碼或中文名稱 → (code, name)。查無回 (None, None)。

    名稱先精確比對，再子字串比對；多筆符合 raise ValueError（訊息列候選）。
    """
    q = (query or "").strip()
    info = _stock_info()
    code_q = q.upper()
    if code_q in info:
        return code_q, info[code_q]
    matches = {c: n for c, n in info.items() if n == q}
    if not matches and q:
        matches = {c: n for c, n in info.items() if q in n}
    if len(matches) == 1:
        return next(iter(matches.items()))
    if len(matches) > 1:
        cands = "、".join(f"{c} {n}" for c, n in sorted(matches.items())[:5])
        more = f"…共{len(matches)}筆" if len(matches) > 5 else ""
        raise ValueError(f"多筆符合：{cands}{more}")
    return None, None


# ── 純函式（可測） ──────────────────────────────────────────────


def aggregate_institutional(rows: list) -> dict:
    """FinMind 法人列 → {date: {foreign_net, trust_net, dealer_net, total_net}}（張）。"""
    by_date = {}
    for r in rows:
        d = by_date.setdefault(r["date"], {"foreign": 0, "trust": 0, "dealer": 0})
        net = int(r.get("buy", 0)) - int(r.get("sell", 0))
        name = r.get("name", "")
        if name in _FOREIGN_NAMES:
            d["foreign"] += net
        elif name in _TRUST_NAMES:
            d["trust"] += net
        elif name in _DEALER_NAMES:
            d["dealer"] += net
    result = {}
    for date, d in by_date.items():
        # int() 向零截斷：-1500 股 → -1 張（// 會變 -2）
        f, t, dl = int(d["foreign"] / 1000), int(d["trust"] / 1000), int(d["dealer"] / 1000)
        result[date] = {
            "foreign_net": f, "trust_net": t, "dealer_net": dl,
            "total_net": f + t + dl,
        }
    return result


def parse_tdcc_csv(text: str, codes: list) -> dict:
    """TDCC 股權分散 CSV → {code: (date, big400_pct, whale_holders, retail_pct)}。

    級距 1-11 = 散戶（<400張）、12-15 = 大戶（≥400張）、15 = 千張；16/17 為差異數與合計，排除。
    """
    import csv as _csv

    wanted = set(codes)
    acc = {}
    reader = _csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header:
        return {}
    # 欄位：資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例百分比
    for row in reader:
        if len(row) < 6:
            continue
        date_raw, code, level = row[0].strip(), row[1].strip(), row[2].strip()
        if code not in wanted:
            continue
        try:
            level = int(level)
            holders = int(row[3])
            pct = float(row[5])
        except ValueError:
            continue
        if not 1 <= level <= 15:
            continue
        a = acc.setdefault(code, {"date": date_raw, "big": 0.0, "retail": 0.0, "whale": 0})
        if level >= 12:
            a["big"] += pct
        else:
            a["retail"] += pct
        if level == 15:
            a["whale"] = holders
    result = {}
    for code, a in acc.items():
        d = a["date"]
        date = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d
        result[code] = (date, round(a["big"], 2), a["whale"], round(a["retail"], 2))
    return result


def parse_tdcc_html(html: str):
    """TDCC 查詢結果 HTML → (big400_pct, whale_holders, retail_pct)；查無資料回 None。

    表格列：[級距, 持股範圍, 人數, 股數, 比例%]，級距 1-11=散戶、12-15=大戶、15=千張。
    """
    big = retail = 0.0
    whale = None
    found = False
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>|\s|,", "", c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        level = int(cells[0])
        if not 1 <= level <= 15:
            continue
        try:
            pct = float(cells[4])
        except ValueError:
            continue
        found = True
        if level >= 12:
            big += pct
        else:
            retail += pct
        if level == 15:
            try:
                whale = int(cells[2])
            except ValueError:
                whale = None
    if not found:
        return None
    return round(big, 2), whale, round(retail, 2)


def fetch_tdcc_history(code: str, weeks: int = 12) -> dict:
    """TDCC 表單逐週查詢近 N 週股權分散 → {date: (big400, whale, retail)}。

    SYNCHRONIZER_TOKEN 為一次性 token，每次 POST 後從回應頁取新 token；
    失敗時重新 GET 首頁換新 token，避免壞 token 連鎖導致後續週全失敗。
    """
    import requests
    import urllib3

    urllib3.disable_warnings()  # TDCC 憑證問題，同 portfolio-analyzer 既有做法
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    html = s.get(TDCC_QRY_URL, verify=False, timeout=30).text
    dates = re.findall(r'<option value="(20\d{6})"', html)[:weeks]
    result = {}
    for d in dates:
        try:
            token = re.search(r'SYNCHRONIZER_TOKEN.*?value="([^"]+)"', html).group(1)
            res = s.post(TDCC_QRY_URL, data={
                "SYNCHRONIZER_TOKEN": token,
                "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                "method": "submit", "firDate": d, "scaDate": d,
                "sqlMethod": "StockNo", "stockNo": code, "stockName": "", "REQ_TYPE": "",
            }, headers={"Referer": TDCC_QRY_URL}, verify=False, timeout=30).text
            parsed = parse_tdcc_html(res)
            if parsed:
                result[f"{d[:4]}-{d[4:6]}-{d[6:8]}"] = parsed
            html = res  # 回應頁含下一次可用的新 token
            time.sleep(0.4)
        except Exception as e:
            logger.warning("TDCC 歷史 %s %s 失敗: %s", code, d, e)
            try:
                html = s.get(TDCC_QRY_URL, verify=False, timeout=30).text  # 換新 token
            except Exception:
                pass
    return result


def backfill_tdcc(conn, code: str, weeks: int = 12) -> None:
    for date, (big, whale, retail) in fetch_tdcc_history(code, weeks).items():
        db.upsert_weekly(conn, code, date, big, whale, retail)
    conn.commit()


def _http_json(url: str) -> dict:
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=30, context=ctx).read())


def fetch_foreign_ratios(codes: list, date_str: str = None) -> dict:
    """外資持股比率（%）→ {code: (date, ratio)}。

    上市：TWSE MI_QFIIS（bulk，指定日期，非交易日回空）。
    上櫃：TPEx 僑外資排行表（僅最新交易日，date_str 非今日時跳過）。
    """
    wanted = set(codes)
    result = {}

    # 指定日期只查該日；未指定則往回找最近有資料的交易日
    # （MI_QFIIS 收盤後才發布，早上查當日必為空）
    if date_str:
        dates = [date_str]
    else:
        dates = [(datetime.now() - timedelta(days=i)).strftime("%Y%m%d") for i in range(5)]
    for d in dates:
        try:
            body = _http_json(f"{TWSE_QFII_URL}?response=json&date={d}&selectType=ALLBUT0999")
            if body.get("stat") != "OK" or not body.get("data"):
                continue
            iso = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            for row in body.get("data", []):
                code = str(row[0]).strip()
                if code in wanted:
                    try:
                        result[code] = (iso, float(row[7]))
                    except (TypeError, ValueError, IndexError):
                        pass
            break
        except Exception as e:
            logger.warning("TWSE 外資持股 %s 失敗: %s", d, e)

    missing = wanted - set(result)
    if missing and date_str is None:  # TPEx 端點只有最新日，回補模式跳過
        try:
            body = _http_json(TPEX_QFII_URL)
            table = body["tables"][0]
            roc = table.get("date", "")  # '115/07/13'
            parts = roc.split("/")
            iso = f"{int(parts[0]) + 1911}-{parts[1]}-{parts[2]}" if len(parts) == 3 else None
            for row in table.get("data", []):
                code = str(row[1]).strip()
                if code in missing and iso:
                    try:
                        result[code] = (iso, float(str(row[7]).rstrip("%")))
                    except (TypeError, ValueError, IndexError):
                        pass
        except Exception as e:
            logger.warning("TPEx 外資持股失敗: %s", e)

    return result


def update_foreign_ratios(conn, codes: list) -> None:
    for code, (date, ratio) in fetch_foreign_ratios(codes).items():
        db.upsert_daily(conn, code, date, foreign_ratio=ratio)
    conn.commit()


def backfill_foreign(conn, codes: list, days: int = 10) -> None:
    """回補近 N 天外資持股比率。

    先跑一次最新模式（含 TPEx 上櫃備援），再逐日回補 TWSE 歷史，
    確保上櫃股票加入當下就有最新值。
    """
    for code, (date, ratio) in fetch_foreign_ratios(codes).items():
        db.upsert_daily(conn, code, date, foreign_ratio=ratio)
    for i in range(days):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        for code, (date, ratio) in fetch_foreign_ratios(codes, date_str=d).items():
            db.upsert_daily(conn, code, date, foreign_ratio=ratio)
        time.sleep(0.5)
    conn.commit()


# ── 抓取與入庫 ─────────────────────────────────────────────────


def update_daily(conn, code: str, start: str, end: str) -> None:
    try:
        inst = aggregate_institutional(
            fm_get("TaiwanStockInstitutionalInvestorsBuySell", code, start, end)
        )
        for date, cols in inst.items():
            db.upsert_daily(conn, code, date, **cols)
    except Exception as e:
        logger.warning("%s 法人資料失敗: %s", code, e)

    try:
        for r in fm_get("TaiwanStockMarginPurchaseShortSale", code, start, end):
            db.upsert_daily(
                conn, code, r["date"],
                margin_balance=r.get("MarginPurchaseTodayBalance"),
                short_balance=r.get("ShortSaleTodayBalance"),
            )
    except Exception as e:
        logger.info("%s 融資融券無資料或失敗（ETF 屬正常）: %s", code, e)

    try:
        for r in fm_get("TaiwanStockPrice", code, start, end):
            close = r.get("close")
            spread = r.get("spread", 0) or 0
            prev = (close - spread) if close is not None else None
            change_pct = round(spread / prev * 100, 2) if prev else None
            db.upsert_daily(conn, code, r["date"], close=close, change_pct=change_pct)
    except Exception as e:
        logger.warning("%s 價格資料失敗: %s", code, e)

    try:
        conn.commit()
    except Exception as e:
        logger.warning("%s commit 失敗: %s", code, e)


def tdcc_needed(conn, codes: list) -> bool:
    """DB 最新週資料距今 >6 天，或有股票缺週資料 → 需要下載。"""
    latest = db.latest_weekly_date(conn)
    if latest is None:
        return True
    if (datetime.now() - datetime.strptime(latest, "%Y-%m-%d")).days > 6:
        return True
    for code in codes:
        if not db.get_weekly_history(conn, code, weeks=1):
            return True
    return False


def update_tdcc(conn, codes: list) -> None:
    req = urllib.request.Request(TDCC_URL, headers={
        "Referer": "https://www.tdcc.com.tw/",
        "User-Agent": "Mozilla/5.0",
    })
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    text = urllib.request.urlopen(req, timeout=90, context=ctx).read().decode("utf-8", "replace")
    parsed = parse_tdcc_csv(text, codes)
    for code, (date, big, whale, retail) in parsed.items():
        db.upsert_weekly(conn, code, date, big, whale, retail)
    conn.commit()
    logger.info("TDCC 週資料更新：%d 檔", len(parsed))


def backfill(conn, code: str, days: int = 45) -> None:
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    update_daily(conn, code, start, end)


def main() -> int:
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("另一個 updater 執行中，跳過")
        return 0

    conn = db.get_conn()
    stocks = db.list_stocks(conn)
    if not stocks:
        logger.info("清單為空，無事可做")
        return 0

    today = datetime.now().strftime("%Y-%m-%d")
    for s in stocks:
        code = s["code"]
        last = db.latest_daily_date(conn, code)
        if last:
            start = (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
        else:
            start = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d")
        update_daily(conn, code, start, today)
        time.sleep(0.3)

    codes = [s["code"] for s in stocks]
    try:
        update_foreign_ratios(conn, codes)
    except Exception as e:
        logger.warning("外資持股更新失敗: %s", e)

    if tdcc_needed(conn, codes):
        try:
            update_tdcc(conn, codes)
        except Exception as e:
            logger.warning("TDCC 更新失敗: %s", e)

    db.set_meta(conn, "last_update", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("更新完成：%d 檔", len(stocks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
