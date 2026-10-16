#!/usr/bin/env python3
"""外資回頭三訊號監控（盤後執行）。

三個「外資資金回頭」的確認訊號，三項都成立時才發重點通知；平時只更新狀態檔，
狀態有變化（成立項數改變）才通知一次，避免每天洗版。

1. 外資台指期(大台)淨空單「連續 3 日各減碼 ≥10,000 口」  ← 最關鍵
2. 大盤成交量「站上 20 日均量」（脫離窒息量）
3. 新台幣「連續 3 日升值、累計 ≥0.3 元」（外資停止匯出）

資料源（全免 token）：
- 外資期貨：期交所 opendata MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate
- 匯率：期交所 opendata DailyForeignExchangeRates（USD/NTD）
- 成交量：證交所 FMTQIK（每日成交金額）

輸出：~/CCProject/data/foreign_return_status.json（給 command-center 卡片讀）
"""
import json
import os
import re
from datetime import date, datetime, timedelta

import requests

BASE = os.path.expanduser("~/CCProject")
STATUS_FILE = os.path.join(BASE, "data", "foreign_return_status.json")
TELEGRAM_TOKEN = open(os.path.join(BASE, ".secrets", "telegram_token.txt")).read().strip()
TELEGRAM_CHAT_ID = "7556217543"

TAIFEX_FX = "https://openapi.taifex.com.tw/v1/DailyForeignExchangeRates"
TAIFEX_WEB = "https://www.taifex.com.tw/cht/3/futContractsDate"   # 三大法人歷史查詢(逐日)
TWSE_VOL = "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?response=json"

BIG_CONTRACT = "臺股期貨"          # 大台
IDENTS = ("自營商", "投信", "外資")  # 網站表格用的法人名
FOREIGN_SITE = "外資"
_NUM = re.compile(r"^-?[\d,]+$")
REDUCE_LOTS = 10_000               # 單日減碼門檻(口)
REDUCE_DAYS = 3                    # 連續天數
VOL_MA_DAYS = 20                   # 成交量均量天數
FX_UP_DAYS = 3                     # 台幣連續升值天數
FX_UP_TOTAL = 0.3                  # 台幣累計升值門檻(元)


def send_telegram(msg: str) -> None:
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15,
        )
    except Exception as e:
        print(f"Telegram 通知失敗：{e}")


def _get_json(url: str):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()


def _to_int(s) -> int:
    return int(str(s).replace(",", "").strip())


def fetch_foreign_net_oi(days: int = 8) -> list[dict]:
    """外資大台淨未沖銷口數，回 [{date, net}] 由舊到新（負值=淨空單）。

    期交所 opendata 只有「當日」，故改逐日打網站的三大法人歷史查詢頁並解析表格
    （網站表格用「外資」，與 opendata 的「外資及陸資」不同；欄位順序見 _NUM 過濾後的
    第 11 個數字＝未沖銷淨口數）。
    """
    from bs4 import BeautifulSoup
    out = []
    d = date.today()
    for _ in range(days * 2 + 6):        # 多掃幾天以跳過週末/假日
        if len(out) >= days:
            break
        try:
            r = requests.post(
                TAIFEX_WEB,
                data={"queryType": "1", "goDay": "", "doQuery": "1",
                      "dateaddcnt": "", "queryDate": d.strftime("%Y/%m/%d")},
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
                timeout=30,
            )
            soup = BeautifulSoup(r.text, "html.parser")
            cur = None
            for tr in soup.find_all("tr", class_="12bk"):
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if not tds:
                    continue
                if len(tds) >= 3 and tds[1] and tds[2] in IDENTS:
                    cur = tds[1]
                ident = tds[2] if len(tds) >= 3 and tds[2] in IDENTS else (
                    tds[0] if tds[0] in IDENTS else None)
                if cur == BIG_CONTRACT and ident == FOREIGN_SITE:
                    nums = [x for x in tds if _NUM.fullmatch(x)]
                    if len(nums) > 10:
                        out.append({"date": d.strftime("%Y%m%d"),
                                    "net": _to_int(nums[10])})
                    break
        except Exception:
            pass
        d -= timedelta(days=1)
    return sorted(out, key=lambda x: x["date"])


def fetch_volume(months: int = 2) -> list[dict]:
    """大盤每日成交金額，回 [{date, amount}] 由舊到新（單位：元）。

    FMTQIK 不帶 date 只回當月，故往前逐月抓以湊足 20 日均量所需的歷史。
    """
    out = []
    today = date.today()
    for i in range(months):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        try:
            d = _get_json(f"{TWSE_VOL}&date={y}{m:02d}01")
        except Exception:
            continue
        for row in d.get("data", []):
            yy, mm, dd = row[0].split("/")          # 民國 115/10/16
            out.append({"date": f"{int(yy) + 1911}-{int(mm):02d}-{int(dd):02d}",
                        "amount": float(row[2].replace(",", ""))})
    uniq = {r["date"]: r for r in out}
    return [uniq[k] for k in sorted(uniq)]


def fetch_fx(days: int = 10) -> list[dict]:
    """USD/NTD 收盤匯率，回 [{date, rate}] 由舊到新（數字下降=台幣升值）。"""
    data = _get_json(TAIFEX_FX)
    out = [{"date": r["Date"], "rate": float(r["USD/NTD"])}
           for r in data if r.get("USD/NTD")]
    out.sort(key=lambda x: x["date"])
    return out[-days:]


def check_futures(rows: list[dict]) -> dict:
    """外資淨空單連續 REDUCE_DAYS 日各減碼 ≥REDUCE_LOTS 口。"""
    if len(rows) < REDUCE_DAYS + 1:
        return {"ok": False, "detail": "資料不足"}
    changes = [rows[i]["net"] - rows[i - 1]["net"] for i in range(1, len(rows))]
    recent = changes[-REDUCE_DAYS:]
    ok = all(c >= REDUCE_LOTS for c in recent)
    return {
        "ok": ok,
        "latest_net": rows[-1]["net"],
        "latest_date": rows[-1]["date"],
        "recent_changes": recent,
        "detail": f"近 {REDUCE_DAYS} 日淨空單變化 {recent}（減碼為正）",
    }


def check_volume(rows: list[dict]) -> dict:
    """最新一日成交金額站上 20 日均量。"""
    if len(rows) < VOL_MA_DAYS + 1:
        return {"ok": False, "detail": "資料不足"}
    latest = rows[-1]
    avg = sum(r["amount"] for r in rows[-(VOL_MA_DAYS + 1):-1]) / VOL_MA_DAYS
    return {
        "ok": latest["amount"] >= avg,
        "latest_date": latest["date"],
        "latest_amount": latest["amount"],
        "ma20": avg,
        "detail": f"{latest['date']} 成交 {latest['amount'] / 1e12:.2f} 兆 vs 20日均量 {avg / 1e12:.2f} 兆",
    }


def check_fx(rows: list[dict]) -> dict:
    """台幣連續 FX_UP_DAYS 日升值、累計 ≥FX_UP_TOTAL 元。"""
    if len(rows) < FX_UP_DAYS + 1:
        return {"ok": False, "detail": "資料不足"}
    seg = rows[-(FX_UP_DAYS + 1):]
    daily_up = all(seg[i]["rate"] < seg[i - 1]["rate"] for i in range(1, len(seg)))
    total = seg[0]["rate"] - seg[-1]["rate"]     # 正=升值
    return {
        "ok": daily_up and total >= FX_UP_TOTAL,
        "latest_date": seg[-1]["date"],
        "latest_rate": seg[-1]["rate"],
        "total_appreciation": round(total, 3),
        "detail": f"{seg[0]['date']}~{seg[-1]['date']} USD/NTD {seg[0]['rate']}→{seg[-1]['rate']}"
                  f"（台幣{'升值' if total > 0 else '貶值'} {abs(total):.3f} 元）",
    }


def main() -> None:
    results = {}
    for key, fn, fetcher in [
        ("futures", check_futures, fetch_foreign_net_oi),
        ("volume", check_volume, fetch_volume),
        ("fx", check_fx, fetch_fx),
    ]:
        try:
            results[key] = fn(fetcher())
        except Exception as e:
            results[key] = {"ok": False, "error": str(e)[:200]}

    hits = [k for k, v in results.items() if v.get("ok")]
    status = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "hits": hits,
        "hit_count": len(hits),
        "results": results,
    }
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)

    labels = {"futures": "① 外資淨空單連續減碼", "volume": "② 成交量站上20日均量", "fx": "③ 台幣連續升值"}
    prev_path = STATUS_FILE + ".prev"
    is_first = not os.path.exists(prev_path)
    prev = {}
    if not is_first:
        try:
            prev = json.load(open(prev_path, encoding="utf-8"))
        except Exception:
            prev = {}

    if len(hits) == 3:
        msg = "🟢 外資回頭三訊號「全部成立」\n" + "\n".join(
            f"{labels[k]}：{results[k]['detail']}" for k in ["futures", "volume", "fx"])
    elif not is_first and len(hits) > prev.get("hit_count", 0):
        msg = f"🟡 外資回頭訊號 {len(hits)}/3 成立\n" + "\n".join(
            f"{labels[k]} ✅" for k in hits) + "\n（詳細看 command-center）"
    else:
        msg = None

    if msg:
        send_telegram(msg)
    # 記住本輪，供下次比對（首次執行只建立基準，不誤發「0/3」通知）
    json.dump(status, open(prev_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"hits": hits, "notified": bool(msg)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
