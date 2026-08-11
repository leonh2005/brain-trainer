"""近月經濟總覽資料聚合。

來源(皆特定可靠來源,非 LLM):
  市場數值(油/金/美元/殖利率/VIX) -> yfinance
  CPI / Fed 利率                    -> FRED 官方 CSV(免 key)
  重大事件日曆(結算日/三巫日/Fed會議/選舉/法說會) -> 規則計算 + Fed 官方公告 + Finnhub 財報行事曆
  戰爭 / 地緣消息                    -> Google News RSS 標題

每個來源各自 try/except,單一來源失敗不影響其他。15 分鐘快取。
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta


def _finnhub_key():
    if os.environ.get("FINNHUB_KEY"):
        return os.environ["FINNHUB_KEY"]
    with open("/Users/steven/CCProject/.secrets/finnhub_key.txt", encoding="utf-8") as f:
        return f.read().strip()


_cache = {"data": None, "ts": 0}
TTL = 900  # 15 分鐘


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _market():
    """油/金/美元/殖利率/VIX：現價 + 當日% + 近一月%。"""
    import yfinance as yf
    defs = [
        ("WTI原油", "CL=F", "$"),
        ("布蘭特原油", "BZ=F", "$"),
        ("黃金", "GC=F", "$"),
        ("美元指數", "DX-Y.NYB", ""),
        ("美10年債殖利率", "^TNX", "%"),
        ("VIX恐慌指數", "^VIX", ""),
    ]
    out = []
    for name, tk, unit in defs:
        try:
            h = yf.Ticker(tk).history(period="1mo")["Close"].dropna()
            if len(h) < 2:
                continue
            last, prev, mago = float(h.iloc[-1]), float(h.iloc[-2]), float(h.iloc[0])
            out.append({
                "name": name, "unit": unit, "price": round(last, 2),
                "chg_pct": round((last - prev) / prev * 100, 2),
                "mo_pct": round((last - mago) / mago * 100, 2),
            })
        except Exception:
            continue
    return out


def _fred_rows(series):
    csv = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")
    rows = []
    for line in csv.strip().splitlines()[1:]:
        parts = line.split(",")
        if len(parts) == 2 and parts[1] not in (".", ""):
            try:
                rows.append((parts[0], float(parts[1])))
            except ValueError:
                pass
    return rows


def _cpi_yoy(series):
    rows = _fred_rows(series)
    if len(rows) < 13:
        return None
    latest_d, latest_v = rows[-1]
    base_v = rows[-13][1]
    return {"date": latest_d, "yoy": round((latest_v / base_v - 1) * 100, 2)}


def _fed_rate():
    up, lo = _fred_rows("DFEDTARU"), _fred_rows("DFEDTARL")
    if not up or not lo:
        return None
    latest = up[-1][1]
    ago = up[-252][1] if len(up) > 252 else up[0][1]   # 約一年前
    trend = "降" if latest < ago else ("升" if latest > ago else "平")
    return {"upper": up[-1][1], "lower": lo[-1][1], "date": up[-1][0], "trend": trend}


def _earnings(frm, to):
    """台美龍頭法說會,資料來自 Finnhub 財報行事曆。"""
    watch = {
        "TSM": "台積電 ADR", "NVDA": "輝達", "AAPL": "蘋果", "MSFT": "微軟",
        "GOOGL": "Google", "AMZN": "亞馬遜", "META": "Meta", "AVGO": "博通",
    }
    url = (f"https://finnhub.io/api/v1/calendar/earnings?from={frm}&to={to}"
           f"&token={_finnhub_key()}")
    cal = json.loads(_get(url)).get("earningsCalendar", [])
    return [{"date": e["date"], "category": "法說會",
              "title": f'{watch[e["symbol"]]}（{e["symbol"]}）法說會'}
             for e in cal if e.get("symbol") in watch and e.get("date")]


_TW_LEADERS = {"2330": "台積電", "2454": "聯發科", "2308": "台達電",
               "2317": "鴻海", "3711": "日月光", "2382": "廣達"}


def _tw_earnings():
    """台股龍頭法說會,資料來自公開資訊觀測站(MOPS)法人說明會一覽表。
    公司通常僅提前 1–2 週公告,故月份越遠越可能查無資料(屬正常現象,非錯誤)。"""
    today = date.today()
    months = []
    y, m = today.year, today.month
    while (y, m) <= (today.year, 12):
        months.append((y, m))
        m += 1
    out = []
    for y, m in months:
        body = urllib.parse.urlencode({
            "encodeURIComponent": "1", "step": "1", "firstin": "1", "off": "1",
            "TYPEK": "sii", "year": str(y - 1911), "month": f"{m:02d}",
        }).encode()
        req = urllib.request.Request(
            "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1", data=body,
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8", "replace")
        rows = re.findall(
            r"text-align:left !important;'>(\d{3,6})</td><td>([^<]*)</td>\s*"
            r"<td align='center'>([\d/]*)</td>\s*<td align='center'>([\d:]*)</td>", html)
        for code, _name, roc_date, _time in rows:
            if code in _TW_LEADERS and roc_date:
                ry, rm, rd = roc_date.split("/")
                out.append({"date": f"{int(ry) + 1911}-{rm}-{rd}", "category": "法說會",
                            "title": f"{_TW_LEADERS[code]}（{code}）法說會"})
    return out


def _nth_weekday(year, month, weekday, n):
    """月份中第 n 個指定星期幾(weekday: Monday=0...Sunday=6)。"""
    import calendar
    days = [d for d in calendar.Calendar().itermonthdates(year, month)
            if d.month == month and d.weekday() == weekday]
    return days[n - 1]


# Fed 官方公告之 2026 FOMC 會議日期(政策聲明日=第二日)
_FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
              "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]

# BLS 官方公告之 2026 CPI 公布日期(美東時間 8:30 AM)
_CPI_RELEASE_2026 = ["2026-02-13", "2026-03-11", "2026-04-10", "2026-05-12",
                      "2026-06-10", "2026-07-14", "2026-08-12", "2026-09-11",
                      "2026-10-14", "2026-11-10", "2026-12-10"]

# BLS 官方公告之 2026 非農就業報告(Employment Situation)公布日期(美東時間 8:30 AM)
_NFP_RELEASE_2026 = ["2026-09-04", "2026-10-02", "2026-11-06", "2026-12-04"]

# BLS 官方公告之 2026 PPI 公布日期(美東時間 8:30 AM)
_PPI_RELEASE_2026 = ["2026-01-14", "2026-01-30", "2026-02-27", "2026-03-18",
                      "2026-04-14", "2026-06-11", "2026-07-15", "2026-08-13",
                      "2026-09-10", "2026-10-15", "2026-11-13", "2026-12-15"]

# BEA 官方公告之 2026 核心PCE物價指數(Personal Income and Outlays)公布日期(美東時間 8:30 AM)
_PCE_RELEASE_2026 = ["2026-08-26", "2026-09-30", "2026-11-25", "2026-12-23"]


def _events():
    """重大市場事件日曆(即日起至今年年底):結算日/三巫日/Fed會議/美國選舉/法說會。"""
    today = date.today()
    year_end = date(today.year, 12, 31)
    events = []

    # 台指期結算日:每月第三個星期三
    for m in range(1, 13):
        d = _nth_weekday(today.year, m, 2, 3)
        if today <= d <= year_end:
            events.append({"date": d.isoformat(), "category": "結算日", "title": "台指期貨結算日"})

    # 三巫日(美股):3/6/9/12 月第三個星期五
    for m in (3, 6, 9, 12):
        d = _nth_weekday(today.year, m, 4, 3)
        if today <= d <= year_end:
            events.append({"date": d.isoformat(), "category": "三巫日", "title": "美股三巫日(股指期貨/選擇權到期)"})

    # Fed FOMC 會議(政策聲明日)
    for d in _FOMC_2026:
        if today.isoformat() <= d <= year_end.isoformat():
            events.append({"date": d, "category": "Fed會議", "title": "FOMC 利率決策公布"})

    # 美國 CPI 公布日
    for d in _CPI_RELEASE_2026:
        if today.isoformat() <= d <= year_end.isoformat():
            events.append({"date": d, "category": "CPI", "title": "美國 CPI 公布"})

    # 美國非農就業報告公布日
    for d in _NFP_RELEASE_2026:
        if today.isoformat() <= d <= year_end.isoformat():
            events.append({"date": d, "category": "非農", "title": "美國非農就業報告公布"})

    # 美國 PPI 公布日
    for d in _PPI_RELEASE_2026:
        if today.isoformat() <= d <= year_end.isoformat():
            events.append({"date": d, "category": "PPI", "title": "美國 PPI 公布"})

    # 美國核心 PCE 物價指數公布日
    for d in _PCE_RELEASE_2026:
        if today.isoformat() <= d <= year_end.isoformat():
            events.append({"date": d, "category": "PCE", "title": "美國核心 PCE 物價指數公布"})

    # 美國期中選舉日:11月第一個星期一後的第一個星期二
    first_monday = _nth_weekday(today.year, 11, 0, 1)
    election = first_monday + timedelta(days=1)
    if today <= election <= year_end:
        events.append({"date": election.isoformat(), "category": "美國選舉", "title": "美國期中選舉日"})

    # 台美龍頭法說會
    events += _earnings(today.isoformat(), year_end.isoformat())
    try:
        events += [e for e in _tw_earnings() if e["date"] >= today.isoformat()]
    except Exception:
        pass  # MOPS 偶爾不穩,不影響其他事件顯示

    events.sort(key=lambda x: x["date"])
    return events


def _war_news():
    q = urllib.parse.quote("戰爭 OR 以色列 OR 烏克蘭 OR 中東 OR 地緣衝突")
    xml = _get(f"https://news.google.com/rss/search?q={q}+when:14d"
               f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    news = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:6]:
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        l = re.search(r"<link>(.*?)</link>", it, re.S)
        p = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        if t:
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).strip()
            date = ""
            if p:
                try:
                    date = time.strftime("%m/%d", time.strptime(p.group(1).strip(), "%a, %d %b %Y %H:%M:%S %Z"))
                except ValueError:
                    date = ""
            news.append({"title": title, "link": l.group(1).strip() if l else "", "date": date})
    return news


def _tw_econ():
    """台灣官方數據（手動維護檔，無乾淨免費 API）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tw_econ.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build():
    data = {}
    for key, fn in [
        ("market", _market),
        ("cpi_us", lambda: _cpi_yoy("CPIAUCSL")),
        ("cpi_core_us", lambda: _cpi_yoy("CPILFESL")),
        ("fed_rate", _fed_rate),
        ("events", _events),
        ("war", _war_news),
        ("tw", _tw_econ),
    ]:
        try:
            data[key] = fn()
        except Exception as e:
            data[key] = None
            data.setdefault("errors", {})[key] = str(e)
    return data


def get_econ(force=False):
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < TTL:
        return _cache["data"]
    d = _build()
    d["updated"] = time.strftime("%Y-%m-%d %H:%M")
    _cache["data"] = d
    _cache["ts"] = now
    return d
