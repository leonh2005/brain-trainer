"""近月經濟總覽資料聚合。

來源(皆特定可靠來源,非 LLM):
  市場數值(油/金/美元/殖利率/VIX) -> yfinance
  CPI / Fed 利率                    -> FRED 官方 CSV(免 key)
  台美龍頭法說會                     -> Finnhub 財報行事曆
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


def _earnings():
    """台美龍頭法說會(未來 ~45 天),資料來自 Finnhub 財報行事曆。"""
    watch = {
        "TSM": "台積電 ADR", "NVDA": "輝達", "AAPL": "蘋果", "MSFT": "微軟",
        "GOOGL": "Google", "AMZN": "亞馬遜", "META": "Meta", "AVGO": "博通",
    }
    today = date.today()
    frm, to = today.isoformat(), (today + timedelta(days=45)).isoformat()
    url = (f"https://finnhub.io/api/v1/calendar/earnings?from={frm}&to={to}"
           f"&token={_finnhub_key()}")
    cal = json.loads(_get(url)).get("earningsCalendar", [])
    ev = [{"symbol": e["symbol"], "name": watch[e["symbol"]], "date": e.get("date")}
          for e in cal if e.get("symbol") in watch and e.get("date")]
    ev.sort(key=lambda x: x["date"])
    return ev


def _war_news():
    q = urllib.parse.quote("戰爭 OR 以色列 OR 烏克蘭 OR 中東 OR 地緣衝突")
    xml = _get(f"https://news.google.com/rss/search?q={q}+when:14d"
               f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    news = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:6]:
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        l = re.search(r"<link>(.*?)</link>", it, re.S)
        if t:
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).strip()
            news.append({"title": title, "link": l.group(1).strip() if l else ""})
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
        ("earnings", _earnings),
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
