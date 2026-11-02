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
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta

import requests

BASE = os.path.expanduser("~/CCProject")
STATUS_FILE = os.path.join(BASE, "data", "foreign_return_status.json")
TOKEN_FILE = os.path.join(BASE, ".secrets", "telegram_token.txt")
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
    # 金鑰延遲載入：模組 import（含測試）不該要求本機存在 .secrets/
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
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


def fetch_foreign_net_oi(days: int = 8) -> tuple[list[dict], list[str]]:
    """外資大台淨未沖銷口數，回 ([{date, net}] 由舊到新, 失敗訊息清單)。

    期交所 opendata 只有「當日」，故改逐日打網站的三大法人歷史查詢頁並解析表格
    （網站表格用「外資」，與 opendata 的「外資及陸資」不同；欄位順序見 _NUM 過濾後的
    第 11 個數字＝未沖銷淨口數）。
    """
    from bs4 import BeautifulSoup
    out = []
    errors: list[str] = []
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
            r.raise_for_status()      # 403/5xx 若不檢查會被解析成 0 列而看似「沒資料」
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
                        out.append({"date": d.strftime("%Y-%m-%d"),
                                    "net": _to_int(nums[10])})
                    break
        except Exception as e:
            errors.append(f"{d}：{str(e)[:80]}")
        d -= timedelta(days=1)
    return sorted(out, key=lambda x: x["date"]), errors


def fetch_volume(months: int = 3) -> tuple[list[dict], list[str]]:
    """大盤每日成交金額，回 ([{date, amount}] 由舊到新, 失敗訊息清單)。

    FMTQIK 不帶 date 只回當月，故往前逐月抓以湊足 20 日均量所需的歷史。
    months 必須 ≥3：月初當月只有 0~1 個交易日，2 個月只湊得到約 20 筆，
    會讓 check_volume 永遠回「資料不足」（2026-11-02 實測 19 筆）。
    單月失敗不再靜默跳過，錯誤摺進回傳值讓卡片顯示得出來。
    """
    out: list[dict] = []
    errors: list[str] = []
    today = date.today()
    for i in range(months):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        try:
            d = _get_json(f"{TWSE_VOL}&date={y}{m:02d}01")
        except Exception as e:
            errors.append(f"{y}-{m:02d} 取得失敗：{str(e)[:80]}")
            continue
        bad = 0
        for row in d.get("data", []):
            try:
                yy, mm, dd = row[0].split("/")      # 民國 115/10/16
                out.append({"date": f"{int(yy) + 1911}-{int(mm):02d}-{int(dd):02d}",
                            "amount": float(row[2].replace(",", ""))})
            except Exception:
                bad += 1                            # 壞列跳過，不連累同月其他列
        if bad:
            errors.append(f"{y}-{m:02d} 有 {bad} 列無法解析")
    uniq = {r["date"]: r for r in out}
    return [uniq[k] for k in sorted(uniq)], errors


def fetch_fx(days: int = 10) -> tuple[list[dict], list[str]]:
    """USD/NTD 收盤匯率，回 ([{date, rate}] 由舊到新, 失敗訊息清單)。

    期交所回 YYYYMMDD，統一轉成 YYYY-MM-DD 與其他兩項一致。
    整個端點失敗時直接拋錯，由 main 記成「抓取失敗」，故 errors 恆為空。
    """
    data = _get_json(TAIFEX_FX)

    def iso(s: str) -> str:
        t = str(s).strip()
        if len(t) == 8 and t.isdigit():
            return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
        if len(t) == 10 and t[4] == "-" and t[7] == "-":
            return t                           # 來源已是 ISO
        try:                                   # 容錯 2026/10/30 之類的變體
            return datetime.strptime(t, "%Y/%m/%d").strftime("%Y-%m-%d")
        except ValueError:
            return ""                          # 無法解析 → 丟掉並記進 errors

    out, bad = [], 0
    for r in data:
        rate = r.get("USD/NTD")
        if not rate:
            continue
        d = iso(r.get("Date", ""))
        if not d:
            bad += 1
            continue
        out.append({"date": d, "rate": float(rate)})
    out.sort(key=lambda x: x["date"])
    return out[-days:], ([f"{bad} 筆日期無法解析"] if bad else [])


def _weekdays_between(d1: date, d2: date) -> int:
    """兩個日期之間（不含端點）的工作日數。"""
    n, cur = 0, d1 + timedelta(days=1)
    while cur < d2:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def _gap_detail(rows: list[dict]) -> str:
    """回第一個「中間夾了工作日卻沒資料」的缺口說明；沒有缺口回空字串。

    週五→週一之間沒有工作日，屬正常（連假同理會誤擋，方向保守）；
    週三→週五中間夾了週四，代表有一天交易日被漏抓 —— 此時相鄰兩筆
    相減會把多日變化算成單日，必須擋掉。
    """
    for a, b in zip(rows, rows[1:]):
        try:
            d1, d2 = date.fromisoformat(a["date"]), date.fromisoformat(b["date"])
        except (ValueError, TypeError):
            return f"{a.get('date')}~{b.get('date')} 日期格式異常"
        missing = _weekdays_between(d1, d2)
        if missing:
            return f"{a['date']} 與 {b['date']} 之間漏了 {missing} 個工作日"
    return ""


def _insufficient(have: int, need: int, errors: list[str] | None = None,
                  unit: str = "筆") -> dict:
    """三個訊號共用的「資料不足」結果，順帶把抓取失敗原因帶給卡片。"""
    detail = f"資料不足（{have} 筆，需 {need} 筆）"
    if errors:
        shown = "；".join(errors[:2])          # 錯誤字串已在來源截斷
        more = f" 等 {len(errors)} {unit}" if len(errors) > 2 else ""
        detail += f"；抓取失敗 {len(errors)} {unit}：{shown}{more}"
    return {"ok": False, "detail": detail}


def check_futures(rows: list[dict], errors: list[str] | None = None) -> dict:
    """外資淨空單連續 REDUCE_DAYS 日各減碼 ≥REDUCE_LOTS 口。"""
    if len(rows) < REDUCE_DAYS + 1:
        return _insufficient(len(rows), REDUCE_DAYS + 1, errors, "天")
    gap = _gap_detail(rows[-(REDUCE_DAYS + 1):])
    if gap:
        # 缺一天會讓「兩日合計」被當成單日減碼 → 寧可不判定，也不要誤報成立
        return {"ok": False, "warn": "資料有缺口",
                "detail": f"資料有缺口（{gap}），暫不判定連續減碼"}
    changes = [rows[i]["net"] - rows[i - 1]["net"] for i in range(1, len(rows))]
    recent = changes[-REDUCE_DAYS:]
    result = {
        "ok": all(c >= REDUCE_LOTS for c in recent),
        "latest_net": rows[-1]["net"],
        "latest_date": rows[-1]["date"],
        "recent_changes": recent,
        "detail": f"近 {REDUCE_DAYS} 日淨空單變化 {recent}（減碼為正）",
    }
    if errors:
        # 湊足筆數但有天抓不到 → 最新資料日可能不是最近交易日，必須講出來
        result["warn"] = f"最新資料日 {rows[-1]['date']}，期間有 {len(errors)} 天抓取失敗"
        result["detail"] += f"　⚠️ {result['warn']}"
    return result


def check_volume(rows: list[dict], errors: list[str] | None = None) -> dict:
    """最新一日成交金額站上 20 日均量。errors 來自 fetch_volume 的抓取失敗。"""
    if len(rows) < VOL_MA_DAYS + 1:
        return _insufficient(len(rows), VOL_MA_DAYS + 1, errors, "個月")
    latest = rows[-1]
    avg = sum(r["amount"] for r in rows[-(VOL_MA_DAYS + 1):-1]) / VOL_MA_DAYS
    detail = (f"{latest['date']} 成交 {latest['amount'] / 1e12:.2f} 兆 "
              f"vs 20日均量 {avg / 1e12:.2f} 兆")
    result = {
        "ok": latest["amount"] >= avg,
        "latest_date": latest["date"],
        "latest_amount": latest["amount"],
        "ma20": avg,
        "detail": detail,
    }
    # 只有真的影響判定的失敗才警告，否則會掛一個永遠不會消的假警告。
    # 只認「取得失敗」（整月沒抓到）；「無法解析」是少數壞列，由筆數與缺口把關。
    warns = []
    fails = [e for e in (errors or []) if "取得失敗" in e]
    if fails:
        cur_ym = date.today().strftime("%Y-%m")
        if any(e.startswith(cur_ym) for e in fails):
            warns.append(f"當月資料未取得，以 {latest['date']} 判定")
        window_start = rows[-(VOL_MA_DAYS + 1)]["date"][:7]
        if any(window_start <= e[:7] < cur_ym for e in fails):
            warns.append("均量窗口內有月份缺失")
    if warns:
        result["warn"] = "；".join(warns)
        result["detail"] = detail + f"　⚠️ {result['warn']}"
    return result


def check_fx(rows: list[dict], errors: list[str] | None = None) -> dict:
    """台幣連續 FX_UP_DAYS 日升值、累計 ≥FX_UP_TOTAL 元。"""
    if len(rows) < FX_UP_DAYS + 1:
        return _insufficient(len(rows), FX_UP_DAYS + 1, errors, "筆")
    seg = rows[-(FX_UP_DAYS + 1):]
    gap = _gap_detail(seg)
    if gap:
        # 中間缺一天時「連續升值」會把不相鄰的兩日當相鄰 → 不判定
        return {"ok": False, "warn": "資料有缺口",
                "detail": f"資料有缺口（{gap}），暫不判定連續升值"}
    daily_up = all(seg[i]["rate"] < seg[i - 1]["rate"] for i in range(1, len(seg)))
    total = seg[0]["rate"] - seg[-1]["rate"]     # 正=升值
    result = {
        # 用與顯示相同的 3 位小數比較，否則會出現「升值 0.300 元」卻不算成立
        "ok": daily_up and round(total, 3) >= FX_UP_TOTAL,
        "latest_date": seg[-1]["date"],
        "latest_rate": seg[-1]["rate"],
        "total_appreciation": round(total, 3),
        "detail": f"{seg[0]['date']}~{seg[-1]['date']} USD/NTD {seg[0]['rate']}→{seg[-1]['rate']}"
                  f"（台幣{'升值' if total > 0 else '貶值'} {abs(total):.3f} 元）",
    }
    if errors:
        result["warn"] = f"最新資料日 {seg[-1]['date']}，期間有 {len(errors)} 筆抓取失敗"
        result["detail"] += f"　⚠️ {result['warn']}"
    return result


def main() -> None:
    results = {}
    for key, fn, fetcher in [
        ("futures", check_futures, fetch_foreign_net_oi),
        ("volume", check_volume, fetch_volume),
        ("fx", check_fx, fetch_fx),
    ]:
        try:
            results[key] = fn(*fetcher())      # fetcher 都回 (rows, errors)
        except Exception as e:
            # 卡片只讀 detail，所以失敗訊息寫這裡（不再另存 error 欄位）
            results[key] = {"ok": False, "detail": f"抓取失敗：{str(e)[:150]}"}

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
