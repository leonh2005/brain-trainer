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
TWSE_HOLIDAY = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
HOLIDAY_CACHE = os.path.join(BASE, "data", "tw_market_holidays.json")
HOLIDAY_TTL_DAYS = 30

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
            msg = f"{y}-{m:02d} 取得失敗：{str(e)[:80]}"
            errors.append(msg)
            print(msg)                          # 視窗外的失敗也要留下軌跡
            continue
        month_rows = d.get("data") or []
        if not month_rows:
            # 200 但沒有 data（當月未發布/整月失敗）不能當成「這個月沒交易日」
            msg = f"{y}-{m:02d} 無資料（來源未發布）"
            errors.append(msg)
            print(msg)
            continue
        bad = 0
        for row in month_rows:
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
    整個端點失敗時直接拋錯，由 main 記成「抓取失敗」。
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
            return ""                          # 無法解析 → 丟掉並計數

    valid, bad_pos = [], []
    for i, r in enumerate(data):
        rate = r.get("USD/NTD")
        d = iso(r.get("Date", ""))
        if not rate or not d:
            # 有列卻沒有匯率＝那天從序列消失，會讓不相鄰的兩天被當連續
            bad_pos.append(i)
            continue
        valid.append({"date": d, "rate": float(rate)})
    # 先依日期排序再取尾段：來源萬一改成新→舊，也不會取到最舊的幾筆
    valid.sort(key=lambda x: x["date"])
    if days:
        valid = valid[-days:]
    # 只回報尾段的壞列；視窗外的舊問題不該讓警告永遠掛著
    cutoff = len(data) - (days or len(data))
    bad = sum(1 for i in bad_pos if i >= cutoff)
    return valid, ([f"{bad} 筆資料無法解析或缺值"] if bad else [])


def fetch_holidays(year: int) -> set[str]:
    """該年度『不交易』日期（證交所開休市日曆，結果快取在 data/，30 天後重抓）。

    日曆同時列出「國曆新年開始交易日」這類交易日備註，名稱含「交易日」的要排除，
    否則會把交易日當成休市日。
    """
    try:
        cached = json.load(open(HOLIDAY_CACHE, encoding="utf-8"))
    except Exception:
        cached = {}
    entry = cached.get(str(year))
    if isinstance(entry, dict) and entry.get("days"):
        try:
            age = (date.today() - date.fromisoformat(entry["at"])).days
        except Exception:
            age = HOLIDAY_TTL_DAYS        # 壞掉就當過期，重抓
        if age < HOLIDAY_TTL_DAYS:
            return set(entry["days"])
    body = _get_json(f"{TWSE_HOLIDAY}?response=json&date={year}0101")
    days = {row[0] for row in body.get("data") or [] if "交易日" not in (row[1] or "")}
    if not days:
        return days                            # 空結果不寫快取，下次重抓
    cached[str(year)] = {"days": sorted(days), "at": date.today().isoformat()}
    try:
        os.makedirs(os.path.dirname(HOLIDAY_CACHE), exist_ok=True)
        with open(HOLIDAY_CACHE, "w", encoding="utf-8") as f:
            json.dump(cached, f, ensure_ascii=False)
    except Exception:
        pass                                    # 快取失敗不影響本次判定
    return days


def latest_trading_day(today: date, holidays: set[str]) -> str:
    """today（含）往前找最近一個交易日，回 ISO 日期字串。"""
    d = today
    for _ in range(30):                         # 農曆年最長連休也在這之內
        if d.weekday() < 5 and d.isoformat() not in holidays:
            return d.isoformat()
        d -= timedelta(days=1)
    return today.isoformat()


def _insufficient(have: int, need: int, errors: list[str] | None = None,
                  unit: str = "筆") -> dict:
    """三個訊號共用的「資料不足」結果，順帶把抓取失敗原因帶給卡片。"""
    detail = f"資料不足（{have} 筆，需 {need} 筆）"
    if errors:
        shown = "；".join(errors[:2])          # 錯誤字串已在來源截斷
        more = f" 等 {len(errors)} {unit}" if len(errors) > 2 else ""
        detail += f"；抓取失敗 {len(errors)} {unit}：{shown}{more}"
    return {"ok": False, "detail": detail}


def _trading_days_since(latest_date: str, latest_td: str, holidays: set[str] | None) -> int:
    """latest_date 之後到 latest_td（含）之間的交易日數。"""
    d1, d2 = date.fromisoformat(latest_date), date.fromisoformat(latest_td)
    n, cur = 0, d1 + timedelta(days=1)
    while cur <= d2:
        if cur.weekday() < 5 and (holidays is None or cur.isoformat() not in holidays):
            n += 1
        cur += timedelta(days=1)
    return n


def _lag_warn(latest_date: str, latest_td: str | None, holidays: set[str] | None = None,
              max_lag: int = 0) -> str:
    """資料日落後最近交易日超過 max_lag 時回警告字串。

    max_lag 依來源發布時間而不同：期交所匯率固定 T-1 發布，所以 fx 允許落後 1 個交易日。
    """
    if not latest_td or latest_date >= latest_td:
        return ""
    lag = _trading_days_since(latest_date, latest_td, holidays)
    if lag <= max_lag:
        return ""
    return f"資料落後 {lag} 個交易日（最新 {latest_date}，最近交易日 {latest_td}）"


def _apply_warns(result: dict, warns: list[str]) -> dict:
    if warns:
        result["warn"] = "；".join(warns)
        result["detail"] += f"　⚠️ {result['warn']}"
    return result


def _error_days(errors: list[str] | None, holidays: set[str] | None = None) -> list[str]:
    """從抓取失敗訊息取出「真的該有資料」的日期（排除週末與休市日）。"""
    out = []
    for e in errors or []:
        d = (e or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            continue
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        if dd.weekday() >= 5 or (holidays and d in holidays):
            continue
        out.append(d)
    return sorted(set(out))


def _weekday_series(d1: date, d2: date) -> list[str]:
    """兩個日期之間（不含端點）的所有平日。"""
    out, cur = [], d1 + timedelta(days=1)
    while cur < d2:
        if cur.weekday() < 5:
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def check_futures(rows: list[dict], errors: list[str] | None = None,
                  holidays: set[str] | None = None, latest_td: str | None = None,
                  max_lag: int = 0, market_days: set[str] | None = None) -> dict:
    """外資淨空單連續 REDUCE_DAYS 日各減碼 ≥REDUCE_LOTS 口。

    缺口判斷的三種證據：
    - 抓取失敗的日期（errors）→ 一定是缺口
    - market_days（當日成交量有資料）→ 市場有交易，期貨卻缺 → 缺口
    - 其餘平日 → 當成休市（國定假日、颱風假都是期交所 200 但沒有表格）
    沒有 market_days 時退回「平日扣掉日曆假日」的保守判斷。
    """
    if len(rows) < REDUCE_DAYS + 1:
        return _insufficient(len(rows), REDUCE_DAYS + 1, errors, "天")
    seg = rows[-(REDUCE_DAYS + 1):]
    gaps = []
    for a, b in zip(seg, seg[1:]):
        try:
            d1, d2 = date.fromisoformat(a["date"]), date.fromisoformat(b["date"])
        except (ValueError, TypeError):
            continue
        for d in _weekday_series(d1, d2):
            if holidays and d in holidays:
                continue
            if market_days is None or d in market_days:
                gaps.append(d)
    bad_days = _error_days(errors, holidays)
    inside = sorted(set(gaps) | {d for d in bad_days
                                 if seg[0]["date"] < d < seg[-1]["date"]})
    if inside:
        # 缺一天會讓「兩日合計」被當成單日減碼 → 寧可不判定，也不要誤報成立
        return {"ok": False, "warn": "資料有缺口",
                "detail": f"資料有缺口（{', '.join(inside[:3])}），暫不判定連續減碼"}
    changes = [rows[i]["net"] - rows[i - 1]["net"] for i in range(1, len(rows))]
    recent = changes[-REDUCE_DAYS:]
    result = {
        "ok": all(c >= REDUCE_LOTS for c in recent),
        "latest_net": rows[-1]["net"],
        "latest_date": rows[-1]["date"],
        "recent_changes": recent,
        "detail": f"近 {REDUCE_DAYS} 日淨空單變化 {recent}（減碼為正）",
    }
    warns = []
    # 只有落在判定窗口之後的失敗才影響結論；更早的失敗不該永久否決訊號
    relevant = [d for d in bad_days if d >= seg[0]["date"]]
    if relevant:
        warns.append(f"最新資料日 {rows[-1]['date']}，期間有 {len(relevant)} 天抓取失敗")
    lag = _lag_warn(rows[-1]["date"], latest_td, holidays, max_lag)
    if lag:
        warns.append(lag)
    return _apply_warns(result, warns)


def check_volume(rows: list[dict], errors: list[str] | None = None,
                 holidays: set[str] | None = None, latest_td: str | None = None,
                 max_lag: int = 0) -> dict:
    """最新一日成交金額站上 20 日均量。errors 來自 fetch_volume 的抓取/解析失敗。"""
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
    # 月份層級（整月沒抓到/沒發布）與資料列層級（少數壞列）分開講，免得文案自相矛盾。
    cur_ym = date.today().strftime("%Y-%m")
    window_start = rows[-(VOL_MA_DAYS + 1)]["date"][:7]
    month_fails = [e for e in (errors or []) if "取得失敗" in e or "無資料" in e]
    parse_fails = [e for e in (errors or []) if "無法解析" in e]
    warns = []
    lag = _lag_warn(latest["date"], latest_td, holidays, max_lag)
    # 當月沒資料只有在「判定基準已經落後」時才值得一提；
    # 月初/假日當天本來就還沒有當月資料，那不算問題（否則會永久否決成交量訊號）
    if lag and any(e.startswith(cur_ym) for e in month_fails):
        warns.append(f"當月資料未取得，以 {latest['date']} 判定")
    in_win_month = [e for e in month_fails if window_start <= e[:7] < cur_ym]
    if in_win_month:
        warns.append(f"均量窗口內有月份缺失：{'；'.join(in_win_month[:2])}")
    in_win_parse = [e for e in parse_fails if window_start <= e[:7] <= cur_ym]
    if in_win_parse:
        warns.append(f"均量窗口內有無法解析的資料列：{'；'.join(in_win_parse[:2])}")
    if lag:
        warns.append(lag)
    return _apply_warns(result, warns)


def check_fx(rows: list[dict], errors: list[str] | None = None,
             holidays: set[str] | None = None, latest_td: str | None = None,
             max_lag: int = 1) -> dict:
    """台幣連續 FX_UP_DAYS 日升值、累計 ≥FX_UP_TOTAL 元。

    期交所匯率是一次回傳完整序列，序列裡沒有的日期就是休市日（含颱風假），
    不再另外做缺口判斷；落後判定允許 1 個交易日（來源固定 T-1 發布）。
    """
    if len(rows) < FX_UP_DAYS + 1:
        return _insufficient(len(rows), FX_UP_DAYS + 1, errors, "筆")
    seg = rows[-(FX_UP_DAYS + 1):]
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
    warns = []
    if errors:
        warns.append(f"最新資料日 {seg[-1]['date']}，期間有 {len(errors)} 筆抓取失敗")
    lag = _lag_warn(seg[-1]["date"], latest_td, holidays, max_lag)
    if lag:
        warns.append(lag)
    return _apply_warns(result, warns)


LABELS = {"futures": "① 外資淨空單連續減碼", "volume": "② 成交量站上20日均量",
          "fx": "③ 台幣連續升值"}


def _notification(results: dict, prev: dict, is_first: bool) -> str | None:
    """只在「有訊號從不成立變成成立」時通知。

    警告消失（ok 一直是 True，只是 warn 不見）不算新成立，
    否則同一筆資料會因為警告解除而再發一次通知。
    """
    hits = _count_hits(results)
    prev_results = (prev or {}).get("results", {})
    newly = [k for k in hits if not (prev_results.get(k) or {}).get("ok")]
    if len(hits) == 3 and (newly or is_first):
        return "🟢 外資回頭三訊號「全部成立」\n" + "\n".join(
            f"{LABELS[k]}：{results[k]['detail']}" for k in ("futures", "volume", "fx"))
    if not is_first and newly:
        return (f"🟡 外資回頭訊號 {len(hits)}/3 成立\n"
                + "\n".join(f"{LABELS[k]} ✅" for k in hits) + "\n（詳細看 command-center）")
    return None


def _count_hits(results: dict) -> list[str]:
    """有警告的訊號不算成立：資料有缺口/落後時不能拿來發「全部成立」通知。"""
    return [k for k, v in results.items() if v.get("ok") and not v.get("warn")]


def main() -> None:
    today = date.today()
    # 開休市日曆：只用來判斷「最近交易日」與資料是否落後；取不到就按工作日算
    holidays: set[str] = set()
    for year in (today.year, today.year - 1):
        try:
            holidays |= fetch_holidays(year)
        except Exception as e:
            # 單一年度失敗不影響另一年（1 月初的窗口會跨到前一年）
            print(f"{year} 開休市日曆取得失敗，該年度按工作日判斷：{str(e)[:80]}")
    holidays_opt: set[str] | None = holidays or None
    latest_td = latest_trading_day(today, holidays) if holidays else None

    fetched, fetch_errors = {}, {}
    for key, fetcher in [("futures", fetch_foreign_net_oi), ("volume", fetch_volume),
                         ("fx", fetch_fx)]:
        try:
            fetched[key] = fetcher()
        except Exception as e:
            # 卡片只讀 detail，所以失敗訊息寫這裡（不再另存 error 欄位）
            fetched[key] = None
            fetch_errors[key] = str(e)[:150]

    # 當日有成交量＝市場有交易：期貨缺那天才是真的漏抓（颱風假兩邊都不會有資料）。
    # 但成交量本身若有一個月沒抓到，market_days 就不完整 → 不能用，退回保守判斷。
    vol_rows = fetched["volume"][0] if fetched["volume"] else []
    vol_errors = fetched["volume"][1] if fetched["volume"] else ["volume 抓取失敗"]
    vol_complete = not any("取得失敗" in e or "無資料" in e for e in vol_errors)
    market_days = {r["date"] for r in vol_rows} if (vol_rows and vol_complete) else None

    # 颱風假等臨時休市不會出現在官方日曆：若成交量完整且只落後一個交易日，
    # 代表「今天市場根本沒開」，用成交量的最後一天當最近交易日（避免假落後警告）
    if latest_td and vol_rows and vol_complete and vol_rows[-1]["date"] < latest_td:
        if _trading_days_since(vol_rows[-1]["date"], latest_td, holidays_opt) <= 1:
            latest_td = vol_rows[-1]["date"]

    results = {}
    for key, fn in [("futures", check_futures), ("volume", check_volume), ("fx", check_fx)]:
        if fetched[key] is None:
            results[key] = {"ok": False, "detail": f"抓取失敗：{fetch_errors[key]}"}
            continue
        rows, errs = fetched[key]
        extra = {"market_days": market_days} if key == "futures" else {}
        try:
            results[key] = fn(rows, errs, holidays=holidays_opt, latest_td=latest_td, **extra)
        except Exception as e:
            # 單一訊號判定出錯不該讓整輪崩掉（狀態檔就完全不會更新）
            results[key] = {"ok": False, "detail": f"判定失敗：{str(e)[:150]}"}

    hits = _count_hits(results)
    status = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "hits": hits,
        "hit_count": len(hits),
        "results": results,
    }
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=1)

    prev_path = STATUS_FILE + ".prev"
    is_first = not os.path.exists(prev_path)
    prev = {}
    if not is_first:
        try:
            with open(prev_path, encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    msg = _notification(results, prev, is_first)

    if msg:
        send_telegram(msg)
    # 記住本輪，供下次比對（首次執行只建立基準，不誤發「0/3」通知）
    json.dump(status, open(prev_path, "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps({"hits": hits, "notified": bool(msg)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
