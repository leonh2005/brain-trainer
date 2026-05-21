#!/usr/bin/env python3
"""
市場恐慌儀表板生成器
每日抓取 VIX、S&P 500 200MA、CNN Fear & Greed，生成自含 HTML
"""

import json
import math
import requests
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path


def trunc2(x):
    """截斷至小數點後兩位，不四捨五入"""
    return math.trunc(x * 100) / 100

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("請安裝：pip3 install yfinance pandas")
    sys.exit(1)

OUTPUT     = Path(__file__).parent / "index.html"
FG_CACHE   = Path(__file__).parent / "fg_history.json"
SP_STATE   = Path(__file__).parent / "sp_state.json"
CAPE_CACHE = Path(__file__).parent / "cape_cache.json"
BOT_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID = "7556217543"


def load_fg_cache() -> dict:
    """讀取本地累積的 F&G 歷史（{date: {y, rating}}）"""
    if FG_CACHE.exists():
        return json.loads(FG_CACHE.read_text())
    return {}


def save_fg_cache(cache: dict):
    FG_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))


def merge_fg_history(cnn_hist: list, cache: dict) -> list:
    """合併 CNN 近期資料與本地快取，去重後按日期排序"""
    merged = {item["x"]: item for item in cnn_hist}
    for date, val in cache.items():
        if date not in merged:
            merged[date] = {"x": date, "y": val["y"], "rating": val["rating"]}
    return sorted(merged.values(), key=lambda d: d["x"])


def send_telegram(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def fetch_yfinance(ticker, period="20y"):
    df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
    close = df[("Close", ticker)].dropna()
    result = []
    for date, val in close.items():
        result.append({"x": date.strftime("%Y-%m-%d"), "y": trunc2(float(val))})
    return result


def fetch_sp500_with_ma(period="20y"):
    df = yf.download("^GSPC", period=period, progress=False, auto_adjust=True)
    close = df[("Close", "^GSPC")].dropna()
    ma200 = close.rolling(200).mean()
    hist_high = trunc2(float(close.max()))
    prices, mas = [], []
    for date, val in close.items():
        prices.append({"x": date.strftime("%Y-%m-%d"), "y": trunc2(float(val))})
    for date, val in ma200.items():
        if pd.notna(val):
            mas.append({"x": date.strftime("%Y-%m-%d"), "y": trunc2(float(val))})
    return prices, mas, hist_high


def fetch_twse_recent(months=3) -> dict:
    """用 TWSE 官方 API 抓最近 N 個月的加權指數日收盤（台灣時間）"""
    result = {}
    today = datetime.now()
    for i in range(months):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        date_str = f"{year}{month:02d}01"
        try:
            r = requests.get(
                "https://www.twse.com.tw/exchangeReport/FMTQIK",
                params={"response": "json", "date": date_str, "type": "MS"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
                verify=False,
            )
            j = r.json()
            if j.get("stat") != "OK":
                continue
            for row in j.get("data", []):
                # row[0] = ROC date e.g. "115/04/14", row[4] = 加權指數
                roc_date, close_str = row[0], row[4]
                parts = roc_date.split("/")
                iso_date = f"{int(parts[0]) + 1911}-{parts[1]}-{parts[2]}"
                result[iso_date] = trunc2(float(close_str.replace(",", "")))
        except Exception as e:
            print(f"  TWSE {date_str} 抓取失敗: {e}")
    return result


def fetch_taiex_with_ma(period="20y"):
    # yfinance 抓長期歷史
    df = yf.download("^TWII", period=period, progress=False, auto_adjust=True)
    close = df[("Close", "^TWII")].dropna()
    hist = {date.strftime("%Y-%m-%d"): trunc2(float(val)) for date, val in close.items()}

    # TWSE 官方 API 補最近 3 個月（確保今日資料）
    print("  補充 TWSE 官方資料（近 3 個月）...")
    twse = fetch_twse_recent(months=3)
    hist.update(twse)  # TWSE 資料覆蓋 yfinance（更準確）

    # 轉回 pandas Series 計算 MA200
    series = pd.Series(hist).sort_index()
    ma200 = series.rolling(200).mean()
    hist_high = trunc2(float(series.max()))

    prices, mas = [], []
    for date, val in series.items():
        prices.append({"x": date, "y": val})
    for date, val in ma200.items():
        if pd.notna(val):
            mas.append({"x": date, "y": trunc2(float(val))})
    return prices, mas, hist_high


def fetch_cape():
    """從 multpl.com 抓取席勒本益比（CAPE Ratio）月度歷史資料"""
    try:
        from bs4 import BeautifulSoup
        r = requests.get(
            "https://www.multpl.com/shiller-pe/table/by-month",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=15,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", {"id": "datatable"}) or soup.find("table")
        if table is None:
            raise ValueError("找不到 CAPE 表格")
        result = []
        for row in table.find_all("tr")[1:]:
            cols = row.find_all("td")
            if len(cols) < 2:
                continue
            try:
                date_str = pd.to_datetime(cols[0].text.strip()).strftime("%Y-%m-%d")
                val = trunc2(float(cols[1].text.strip().replace(",", "")))
                result.append({"x": date_str, "y": val})
            except Exception:
                continue
        result = sorted(result, key=lambda d: d["x"])
        CAPE_CACHE.write_text(json.dumps(result, ensure_ascii=False))
        print(f"  CAPE 抓取成功，{len(result)} 筆，最新：{result[-1]['y'] if result else 'N/A'}")
        return result
    except Exception as e:
        print(f"CAPE 抓取失敗: {e}")
        if CAPE_CACHE.exists():
            print("  使用本地快取")
            return json.loads(CAPE_CACHE.read_text())
        return []


def fetch_bls(series_id: str, start_year: int, end_year: int) -> list:
    """BLS 公共 API（無需 API key，月度資料）"""
    try:
        r = requests.post(
            "https://api.bls.gov/publicAPI/v1/timeseries/data/",
            json={"seriesid": [series_id], "startyear": str(start_year), "endyear": str(end_year)},
            headers={"Content-Type": "application/json"},
            timeout=20,
        )
        data = r.json()
        if data.get("status") != "REQUEST_SUCCEEDED":
            raise ValueError(f"BLS {data.get('status')}: {data.get('message', '')}")
        result = []
        for item in data["Results"]["series"][0]["data"]:
            period = item["period"]
            if not period.startswith("M") or period == "M13":
                continue
            date_str = f"{item['year']}-{period[1:]}-01"
            try:
                result.append({"x": date_str, "y": trunc2(float(item["value"]))})
            except ValueError:
                continue
        return sorted(result, key=lambda d: d["x"])
    except Exception as e:
        print(f"  BLS {series_id} 失敗: {e}")
        return []


def fetch_yield_spread_yf() -> list:
    """yfinance ^TNX - ^IRX → 10Y−3M 公債利差"""
    try:
        df10 = yf.download("^TNX", period="20y", progress=False, auto_adjust=True)
        df3m = yf.download("^IRX", period="20y", progress=False, auto_adjust=True)
        c10 = df10[("Close", "^TNX")].dropna()
        c3m = df3m[("Close", "^IRX")].dropna()
        common = c10.index.intersection(c3m.index)
        spread = c10.loc[common] - c3m.loc[common]
        return [{"x": d.strftime("%Y-%m-%d"), "y": trunc2(float(v))} for d, v in spread.items()]
    except Exception as e:
        print(f"  10Y-3M spread 失敗: {e}")
        return []


def fetch_recession_signals():
    """抓取四個衰退預警指標"""
    from datetime import date as _date
    _today = _date.today()

    print("  抓取失業率 (UNRATE via BLS)...")
    unrate = fetch_bls("LNS14000000", _today.year - 2, _today.year)[-24:]

    print("  抓取核心 CPI (CPILFESL via BLS)...")
    core_cpi_raw = fetch_bls("CUSR0000SA0L1E", _today.year - 3, _today.year)[-36:]

    print("  ISM 新訂單（暫無免費 API，跳過）")
    ism = []

    print("  抓取 10Y-3M 利差 (yfinance)...")
    t10y2y = fetch_yield_spread_yf()

    # ── 失業率：是否連升 3 個月以上 ────────────────────────────
    unemp_signal = False
    unemp_consec = 0
    if len(unrate) >= 4:
        vals = [d["y"] for d in unrate[-6:]]
        consec = 0
        for i in range(len(vals) - 1, 0, -1):
            if vals[i] > vals[i - 1]:
                consec += 1
            else:
                break
        unemp_consec = consec
        unemp_signal = consec >= 3

    # ── 核心 CPI：是否重新加速（近 3 個月 YoY 趨勢回升）─────────
    cpi_signal = False
    cpi_yoy_recent = []
    if len(core_cpi_raw) >= 14:
        for i in range(-3, 0):
            cur = core_cpi_raw[i]["y"]
            prev = core_cpi_raw[i - 12]["y"]
            yoy = trunc2((cur / prev - 1) * 100)
            cpi_yoy_recent.append(yoy)
        # 如果近 3 個月 YoY 連升 → 重新加速
        cpi_signal = len(cpi_yoy_recent) == 3 and cpi_yoy_recent[2] > cpi_yoy_recent[1] > cpi_yoy_recent[0]

    # 轉成 YoY 序列用於圖表
    cpi_yoy = []
    for i in range(12, len(core_cpi_raw)):
        cur = core_cpi_raw[i]["y"]
        prev = core_cpi_raw[i - 12]["y"]
        cpi_yoy.append({"x": core_cpi_raw[i]["x"], "y": trunc2((cur / prev - 1) * 100)})

    # ── ISM 新訂單：低於 50 幾個月 ───────────────────────────────
    ism_signal = False
    ism_below50_months = 0
    if ism:
        for d in reversed(ism):
            if d["y"] < 50:
                ism_below50_months += 1
            else:
                break
        ism_signal = ism_below50_months >= 2

    # ── 10Y-2Y：倒掛或近零 ──────────────────────────────────────
    yc_signal = False
    yc_current = t10y2y[-1]["y"] if t10y2y else None
    if yc_current is not None:
        yc_signal = yc_current <= 0.2

    return {
        "unrate": {"data": unrate, "signal": unemp_signal, "consec_months": unemp_consec,
                   "current": unrate[-1]["y"] if unrate else None},
        "core_cpi": {"data": cpi_yoy[-24:], "signal": cpi_signal,
                     "current": cpi_yoy[-1]["y"] if cpi_yoy else None,
                     "recent": cpi_yoy_recent},
        "ism": {"data": ism, "signal": ism_signal, "below50_months": ism_below50_months,
                "current": ism[-1]["y"] if ism else None},
        "yield_curve": {"data": t10y2y[-500:], "signal": yc_signal, "current": yc_current},
    }


def fetch_fear_greed():
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Referer": "https://www.cnn.com/",
            },
            timeout=15,
        )
        d = r.json()
        current = d["fear_and_greed"]
        hist_raw = d.get("fear_and_greed_historical", {}).get("data", [])
        hist = [
            {"x": datetime.fromtimestamp(p["x"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
             "y": trunc2(p["y"]),
             "rating": p.get("rating", "")}
            for p in hist_raw
        ]
        score = trunc2(current["score"])
        rating = current["rating"]

        # 更新本地快取（今天的值）
        cache = load_fg_cache()
        today = datetime.now().strftime("%Y-%m-%d")
        cache[today] = {"y": score, "rating": rating}
        save_fg_cache(cache)

        # 合併 CNN + 快取
        full_hist = merge_fg_history(hist, cache)

        return {
            "score": score,
            "rating": rating,
            "history": full_hist,
        }
    except Exception as e:
        print(f"Fear & Greed 抓取失敗: {e}")
        # 即使 CNN 失敗，仍可從快取讀取歷史
        cache = load_fg_cache()
        hist = [{"x": d, "y": v["y"], "rating": v["rating"]} for d, v in sorted(cache.items())]
        return {"score": None, "rating": "N/A", "history": hist}


def generate_html(vix_data, sp_data, ma_data, sp_hist_high, fg_data, tw_data, tw_ma_data, tw_hist_high, cape_data, recession, generated_at):
    vix_current = vix_data[-1]["y"] if vix_data else 0
    vix_date    = vix_data[-1]["x"] if vix_data else "N/A"
    sp_current  = sp_data[-1]["y"] if sp_data else 0
    sp_date     = sp_data[-1]["x"] if sp_data else "N/A"
    ma_current  = ma_data[-1]["y"] if ma_data else 0
    sp_vs_ma    = trunc2((sp_current / ma_current - 1) * 100) if ma_current else 0
    sp_vs_high  = trunc2((sp_current / sp_hist_high - 1) * 100) if sp_hist_high else 0
    fg_score    = fg_data["score"]
    fg_rating   = fg_data["rating"]
    fg_date     = fg_data["history"][-1]["x"] if fg_data.get("history") else "N/A"
    tw_current  = tw_data[-1]["y"] if tw_data else 0
    tw_date     = tw_data[-1]["x"] if tw_data else "N/A"
    tw_ma_current = tw_ma_data[-1]["y"] if tw_ma_data else 0
    tw_vs_ma    = trunc2((tw_current / tw_ma_current - 1) * 100) if tw_ma_current else 0
    tw_vs_high  = trunc2((tw_current / tw_hist_high - 1) * 100) if tw_hist_high else 0

    cape_current = cape_data[-1]["y"] if cape_data else 0
    cape_date    = cape_data[-1]["x"] if cape_data else "N/A"

    # 衰退指標
    r_unemp = recession["unrate"]
    r_cpi   = recession["core_cpi"]
    r_ism   = recession["ism"]
    r_yc    = recession["yield_curve"]

    def sig_icon(triggered): return "🚨" if triggered else "✅"
    def sig_color(triggered): return "#ff4757" if triggered else "#00d68f"

    unemp_icon  = sig_icon(r_unemp["signal"])
    cpi_icon    = sig_icon(r_cpi["signal"])
    ism_icon    = sig_icon(r_ism["signal"])
    yc_icon     = sig_icon(r_yc["signal"])
    unemp_color = sig_color(r_unemp["signal"])
    cpi_color   = sig_color(r_cpi["signal"])
    ism_color   = sig_color(r_ism["signal"])
    yc_color    = sig_color(r_yc["signal"])

    unemp_val  = f"{r_unemp['current']:.1f}%" if r_unemp["current"] else "N/A"
    cpi_val    = f"{r_cpi['current']:.2f}%" if r_cpi["current"] else "N/A"
    ism_val    = f"{r_ism['current']:.1f}" if r_ism["current"] else "N/A"
    yc_val     = f"{r_yc['current']:+.2f}%" if r_yc["current"] is not None else "N/A"
    unemp_date = r_unemp["data"][-1]["x"] if r_unemp.get("data") else "N/A"
    cpi_date   = r_cpi["data"][-1]["x"] if r_cpi.get("data") else "N/A"
    ism_date   = r_ism["data"][-1]["x"] if r_ism.get("data") else "N/A"
    yc_date    = r_yc["data"][-1]["x"] if r_yc.get("data") else "N/A"
    cape_hist_avg = 17.0  # 長期歷史均值
    cape_vs_avg = trunc2((cape_current / cape_hist_avg - 1) * 100) if cape_hist_avg else 0

    # 顏色判斷
    vix_color = "#ff4757" if vix_current > 30 else ("#ffaa00" if vix_current > 20 else "#00d68f")
    fg_color = "#ff4757" if (fg_score or 50) < 25 else ("#ffaa00" if (fg_score or 50) < 45 else ("#00d68f" if (fg_score or 50) > 55 else "#e0e0e0"))
    sp_color = "#00d68f" if sp_vs_ma > 0 else "#ff4757"
    tw_color = "#ff4757" if tw_vs_ma > 0 else "#00d68f"  # 台股：紅漲綠跌
    cape_color = "#ff4757" if cape_current > 35 else ("#ffaa00" if cape_current > 25 else "#00d68f")

    fg_score_display = f"{fg_score:.2f}" if fg_score is not None else "N/A"
    fg_label_map = {
        "extreme fear": "極度恐慌", "fear": "恐慌",
        "neutral": "中立", "greed": "貪婪",
        "extreme greed": "極度貪婪",
    }
    fg_label = fg_label_map.get(fg_rating.lower(), fg_rating)

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>市場恐慌儀表板</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  :root {{
    --bg: #06080f;
    --surface: #0d1221;
    --surface2: #141928;
    --border: #1e2a3a;
    --text: #c8d6e8;
    --text-dim: #5a6a7e;
    --green: #00d68f;
    --amber: #ffaa00;
    --red: #ff4757;
    --cyan: #00b8ff;
    --font-mono: 'Share Tech Mono', monospace;
    --font-sans: 'Syne', sans-serif;
  }}

  * {{ margin: 0; padding: 0; box-sizing: border-box; }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: var(--font-sans);
    min-height: 100vh;
    background-image:
      radial-gradient(ellipse at 20% 0%, rgba(0,184,255,0.04) 0%, transparent 60%),
      radial-gradient(ellipse at 80% 100%, rgba(0,214,143,0.03) 0%, transparent 60%);
  }}

  header {{
    padding: 2rem 2.5rem 1.5rem;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    gap: 1.5rem;
    flex-wrap: wrap;
  }}

  header h1 {{
    font-size: 1.5rem;
    font-weight: 800;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: #fff;
  }}

  header h1 span {{
    color: var(--cyan);
  }}

  .updated {{
    font-family: var(--font-mono);
    font-size: 0.75rem;
    color: var(--text-dim);
    margin-left: auto;
  }}

  .cards {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1px;
    border: 1px solid var(--border);
    background: var(--border);
    margin: 2rem 2.5rem;
    border-radius: 12px;
    overflow: hidden;
  }}

  .card {{
    background: var(--surface);
    padding: 1.75rem 2rem;
    position: relative;
    overflow: hidden;
  }}

  .card::before {{
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 2px;
    background: var(--accent);
  }}

  .card-label {{
    font-size: 0.7rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-bottom: 1rem;
  }}

  .card-value {{
    font-family: var(--font-mono);
    font-size: 3rem;
    font-weight: 400;
    line-height: 1;
    color: var(--accent);
    margin-bottom: 0.5rem;
  }}

  .card-sub {{
    font-family: var(--font-mono);
    font-size: 0.8rem;
    color: var(--text-dim);
  }}

  .card-sub b {{
    color: var(--accent);
    font-style: normal;
  }}

  .pulse {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    margin-right: 6px;
    animation: pulse 2s ease-in-out infinite;
  }}

  @keyframes pulse {{
    0%, 100% {{ opacity: 1; transform: scale(1); }}
    50% {{ opacity: 0.4; transform: scale(0.7); }}
  }}

  .controls {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0 2.5rem 1.5rem;
  }}

  .controls span {{
    font-size: 0.7rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--text-dim);
    margin-right: 0.5rem;
  }}

  .btn {{
    font-family: var(--font-mono);
    font-size: 0.75rem;
    padding: 0.35rem 0.85rem;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    border-radius: 4px;
    transition: all 0.15s;
    letter-spacing: 0.05em;
  }}

  .btn:hover, .btn.active {{
    border-color: var(--cyan);
    color: var(--cyan);
    background: rgba(0, 184, 255, 0.08);
  }}

  .charts {{
    display: flex;
    flex-direction: column;
    gap: 1px;
    background: var(--border);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
  }}

  .chart-section {{
    background: var(--surface);
    padding: 1.5rem 2.5rem 2rem;
  }}

  .chart-header {{
    display: flex;
    align-items: baseline;
    gap: 1rem;
    margin-bottom: 1.25rem;
    flex-wrap: wrap;
  }}

  .chart-title {{
    font-size: 0.72rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--text-dim);
  }}

  .chart-title b {{
    color: var(--text);
    font-weight: 600;
  }}

  .chart-legend {{
    display: flex;
    gap: 1.25rem;
    margin-left: auto;
  }}

  .legend-item {{
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    color: var(--text-dim);
  }}

  .legend-dot {{
    width: 8px;
    height: 2px;
    border-radius: 1px;
  }}

  .chart-wrap {{
    height: 200px;
    position: relative;
  }}

  .zone-label {{
    position: absolute;
    right: 8px;
    font-family: var(--font-mono);
    font-size: 0.6rem;
    letter-spacing: 0.1em;
    opacity: 0.4;
    text-transform: uppercase;
  }}

  footer {{
    padding: 1.5rem 2.5rem;
    font-family: var(--font-mono);
    font-size: 0.7rem;
    color: var(--text-dim);
    display: flex;
    gap: 2rem;
  }}

  footer a {{ color: var(--cyan); text-decoration: none; }}

  @media (max-width: 1100px) {{
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
    .card-span2 {{ grid-column: span 1 !important; }}
  }}
  @media (max-width: 768px) {{
    .cards {{ grid-template-columns: 1fr; margin: 1rem; }}
    .cards .card {{ grid-column: 1 !important; }}
    header, .controls, .chart-section, footer {{ padding-left: 1rem; padding-right: 1rem; }}
    .card-value {{ font-size: 2.2rem; }}
  }}
</style>
</head>
<body>

<header>
  <h1>市場<span>恐慌</span>儀表板</h1>
  <div class="updated">更新時間：{generated_at}</div>
</header>

<div class="cards">
  <div class="card" style="--accent: {vix_color}">
    <div class="card-label"><span class="pulse"></span>VIX 恐慌指數</div>
    <div class="card-value">{vix_current:.2f}</div>
    <div class="card-sub">
      {'<b>極度恐慌 &gt;30</b>' if vix_current > 30 else ('<b>警戒 20–30</b>' if vix_current > 20 else '<b>低波動 &lt;20</b>')}
      &nbsp;·&nbsp; 標普 500 30日隱含波動率
      <br><span style="opacity:0.5">{vix_date}</span>
    </div>
  </div>

  <div class="card" style="--accent: {fg_color}">
    <div class="card-label"><span class="pulse"></span>CNN 恐懼貪婪指數</div>
    <div class="card-value">{fg_score_display}</div>
    <div class="card-sub">
      <b>{fg_label}</b>
      &nbsp;·&nbsp; 0=極恐 / 100=極貪
      <br><span style="opacity:0.5">{fg_date}</span>
    </div>
  </div>

  <div class="card" style="--accent: {cape_color}">
    <div class="card-label"><span class="pulse"></span>席勒本益比 CAPE Ratio</div>
    <div class="card-value">{cape_current:.1f}</div>
    <div class="card-sub">
      歷史均值：{cape_hist_avg}
      &nbsp;·&nbsp; <b>{'▲' if cape_vs_avg > 0 else '▼'} {abs(cape_vs_avg):.0f}%</b>
      &nbsp;·&nbsp; {'<b>高估警戒 &gt;35</b>' if cape_current > 35 else ('<b>偏高 25–35</b>' if cape_current > 25 else '<b>合理 &lt;25</b>')}
      <br><span style="opacity:0.5">{cape_date}</span>
    </div>
  </div>

  <div class="card card-span2" style="--accent: {sp_color}; grid-column: span 2">
    <div class="card-label"><span class="pulse"></span>S&P 500 vs 200MA</div>
    <div class="card-value">{sp_current:,.2f}</div>
    <div class="card-sub">
      200MA：{ma_current:,.2f}
      &nbsp;·&nbsp; <b>{'▲' if sp_vs_ma > 0 else '▼'} {abs(sp_vs_ma)}%</b>
      <br><span style="font-size:0.7rem;opacity:0.6">歷史高點：{sp_hist_high:,.2f}&nbsp;&nbsp;<b style="opacity:1">{sp_vs_high:.1f}%</b>&nbsp;&nbsp;{sp_date}</span>
    </div>
  </div>

  <div class="card card-span2" style="--accent: {tw_color}; grid-column: span 2">
    <div class="card-label"><span class="pulse"></span>台股加權指數 vs 200MA</div>
    <div class="card-value">{tw_current:,.0f}</div>
    <div class="card-sub">
      200MA：{tw_ma_current:,.0f}
      &nbsp;·&nbsp; <b style="color:{'#ff4757' if tw_vs_ma > 0 else '#00d68f'}">{'▲' if tw_vs_ma > 0 else '▼'} {abs(tw_vs_ma)}%</b>
      <br><span style="font-size:0.7rem;opacity:0.6">歷史高點：{tw_hist_high:,.0f}&nbsp;&nbsp;<b style="opacity:1">{tw_vs_high:.1f}%</b>&nbsp;&nbsp;{tw_date}</span>
    </div>
  </div>

</div>

<div class="section-label" style="padding: 0 2.5rem 0.75rem; font-size:0.7rem; letter-spacing:0.15em; text-transform:uppercase; color:var(--text-dim);">
  ⚡ 衰退預警指標
</div>
<div class="cards" style="margin-top:0; grid-template-columns: repeat(4, 1fr)">
  <div class="card" style="--accent: {unemp_color}">
    <div class="card-label">{unemp_icon} 失業率</div>
    <div class="card-value" style="font-size:2rem">{unemp_val}</div>
    <div class="card-sub">
      連升：<b>{r_unemp['consec_months']} 個月</b>
      &nbsp;·&nbsp; {'<b>警示 ≥3個月</b>' if r_unemp["signal"] else '正常'}
      <br><span style="opacity:0.5">{unemp_date}</span>
    </div>
  </div>
  <div class="card" style="--accent: {cpi_color}">
    <div class="card-label">{cpi_icon} 核心 CPI（YoY）</div>
    <div class="card-value" style="font-size:2rem">{cpi_val}</div>
    <div class="card-sub">
      {'<b>🔺 重新加速</b>' if r_cpi["signal"] else '趨勢未加速'}
      &nbsp;·&nbsp; CPILFESL
      <br><span style="opacity:0.5">{cpi_date}</span>
    </div>
  </div>
  <div class="card" style="--accent: {ism_color}">
    <div class="card-label">{ism_icon} ISM 新訂單</div>
    <div class="card-value" style="font-size:2rem">{ism_val}</div>
    <div class="card-sub">
      低於50：<b>{r_ism['below50_months']} 個月</b>
      &nbsp;·&nbsp; {'<b>收縮警示</b>' if r_ism["signal"] else '擴張'}
      <br><span style="opacity:0.5">{ism_date if ism_date != "N/A" else "暫無資料"}</span>
    </div>
  </div>
  <div class="card" style="--accent: {yc_color}">
    <div class="card-label">{yc_icon} 10Y−3M 利差</div>
    <div class="card-value" style="font-size:2rem">{yc_val}</div>
    <div class="card-sub">
      {'<b>倒掛／接近零</b>' if r_yc["signal"] else '正斜率'}
      &nbsp;·&nbsp; ^TNX − ^IRX
      <br><span style="opacity:0.5">{yc_date}</span>
    </div>
  </div>
</div>

<div class="controls">
  <span>區間</span>
  <button class="btn" onclick="setRange(365)">1Y</button>
  <button class="btn" onclick="setRange(365*3)">3Y</button>
  <button class="btn" onclick="setRange(365*5)">5Y</button>
  <button class="btn" onclick="setRange(365*10)">10Y</button>
  <button class="btn active" onclick="setRange(0)">全部</button>
</div>

<div class="charts">
  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>VIX</b> 波動率指數</div>
      <div class="chart-legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00b8ff"></div>VIX</div>
        <div class="legend-item" style="color:#ff4757"><div class="legend-dot" style="background:#ff4757"></div>&gt;30 恐慌區</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="vixChart"></canvas></div>
  </div>

  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>Fear &amp; Greed</b> 恐懼貪婪指數 <span style="font-size:0.65rem;opacity:0.5">（近一年）</span></div>
      <div class="chart-legend">
        <div class="legend-item"><div class="legend-dot" style="background:#ff4757"></div>&lt;25 極恐</div>
        <div class="legend-item"><div class="legend-dot" style="background:#00d68f"></div>&gt;75 極貪</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="fgChart"></canvas></div>
  </div>

  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>S&P 500</b> 收盤價 vs 200日均線 vs 歷史高點</div>
      <div class="chart-legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00d68f"></div>S&P 500</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ffaa00"></div>200MA</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff4757"></div>歷史高點 {sp_hist_high:,.0f}</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="spChart"></canvas></div>
  </div>

  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>席勒本益比（CAPE）</b> 月度歷史 <span style="font-size:0.65rem;opacity:0.5">（來源：multpl.com，自 1881 年）</span></div>
      <div class="chart-legend">
        <div class="legend-item"><div class="legend-dot" style="background:#c8a0ff"></div>CAPE</div>
        <div class="legend-item" style="color:#ffaa00"><div class="legend-dot" style="background:#ffaa00"></div>均值 {cape_hist_avg}</div>
        <div class="legend-item" style="color:#ff4757"><div class="legend-dot" style="background:#ff4757"></div>&gt;35 高估警戒</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="capeChart"></canvas></div>
  </div>

  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>台股加權指數</b> 收盤價 vs 200日均線 vs 歷史高點</div>
      <div class="chart-legend">
        <div class="legend-item"><div class="legend-dot" style="background:#ff4757"></div>加權指數</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ffaa00"></div>200MA</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ff4757"></div>歷史高點 {tw_hist_high:,.0f}</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="twChart"></canvas></div>
  </div>
</div>

<div class="charts" style="margin-top:1px">
  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>失業率</b> UNRATE <span style="font-size:0.65rem;opacity:0.5">（月度，%）</span></div>
      <div class="chart-legend">
        <div class="legend-item" style="color:{unemp_color}"><div class="legend-dot" style="background:{unemp_color}"></div>{'⚠ 連升3+月' if r_unemp["signal"] else '正常'}</div>
      </div>
    </div>
    <div class="chart-wrap" style="height:150px"><canvas id="unrateChart"></canvas></div>
  </div>
  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>核心 CPI YoY</b> CPILFESL <span style="font-size:0.65rem;opacity:0.5">（年增率，%）</span></div>
      <div class="chart-legend">
        <div class="legend-item" style="color:{cpi_color}"><div class="legend-dot" style="background:{cpi_color}"></div>{'⚠ 重新加速' if r_cpi["signal"] else '持平或下行'}</div>
      </div>
    </div>
    <div class="chart-wrap" style="height:150px"><canvas id="cpiChart"></canvas></div>
  </div>
  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>ISM 製造業新訂單</b> <span style="font-size:0.65rem;opacity:0.5">（50 = 榮枯線，暫無資料）</span></div>
      <div class="chart-legend">
        <div class="legend-item" style="color:#00d68f"><div class="legend-dot" style="background:#00d68f"></div>≥50 擴張</div>
        <div class="legend-item" style="color:#ff4757"><div class="legend-dot" style="background:#ff4757"></div>&lt;50 收縮</div>
      </div>
    </div>
    <div class="chart-wrap" style="height:150px"><canvas id="ismChart"></canvas></div>
  </div>
  <div class="chart-section">
    <div class="chart-header">
      <div class="chart-title"><b>10Y−3M 公債利差</b> ^TNX−^IRX <span style="font-size:0.65rem;opacity:0.5">（%，&lt;0 = 倒掛）</span></div>
      <div class="chart-legend">
        <div class="legend-item" style="color:#00d68f"><div class="legend-dot" style="background:#00d68f"></div>≥0.5 正常</div>
        <div class="legend-item" style="color:#ffaa00"><div class="legend-dot" style="background:#ffaa00"></div>0–0.5 警戒</div>
        <div class="legend-item" style="color:#ff4757"><div class="legend-dot" style="background:#ff4757"></div>&lt;0 倒掛</div>
      </div>
    </div>
    <div class="chart-wrap" style="height:150px"><canvas id="yieldCurveChart"></canvas></div>
  </div>
</div>

<footer>
  <span>資料來源：Yahoo Finance · CNN Fear &amp; Greed · multpl.com · BLS</span>
  <span>每日 07:00 自動更新</span>
</footer>

<script>
const VIX_DATA = {json.dumps(vix_data)};
const SP_DATA  = {json.dumps(sp_data)};
const MA_DATA  = {json.dumps(ma_data)};
const SP_HIST_HIGH = {sp_hist_high};
const FG_DATA  = {json.dumps(fg_data["history"])};
const TW_DATA  = {json.dumps(tw_data)};
const TW_MA_DATA = {json.dumps(tw_ma_data)};
const TW_HIST_HIGH = {tw_hist_high};
const CAPE_DATA    = {json.dumps(cape_data)};
const CAPE_AVG     = {cape_hist_avg};
const UNRATE_DATA  = {json.dumps(r_unemp["data"])};
const CPI_YOY_DATA = {json.dumps(r_cpi["data"])};
const ISM_DATA     = {json.dumps(r_ism["data"])};
const T10Y2Y_DATA  = {json.dumps(r_yc["data"])};

const CHART_DEFAULTS = {{
  responsive: true,
  maintainAspectRatio: false,
  interaction: {{ mode: 'index', intersect: false }},
  plugins: {{
    legend: {{ display: false }},
    tooltip: {{
      backgroundColor: '#141928',
      borderColor: '#1e2a3a',
      borderWidth: 1,
      titleColor: '#5a6a7e',
      bodyColor: '#c8d6e8',
      titleFont: {{ family: "'Share Tech Mono'" }},
      bodyFont: {{ family: "'Share Tech Mono'" }},
      padding: 10,
    }},
  }},
  scales: {{
    x: {{
      type: 'time',
      time: {{ tooltipFormat: 'yyyy-MM-dd' }},
      grid: {{ color: '#1e2a3a' }},
      ticks: {{ color: '#5a6a7e', font: {{ family: "'Share Tech Mono'", size: 10 }} }},
    }},
    y: {{
      grid: {{ color: '#1e2a3a' }},
      ticks: {{ color: '#5a6a7e', font: {{ family: "'Share Tech Mono'", size: 10 }} }},
    }},
  }},
}};

function makeChart(id, datasets, yMin, yMax, extras={{}}) {{
  const ctx = document.getElementById(id).getContext('2d');
  return new Chart(ctx, {{
    type: 'line',
    data: {{ datasets }},
    options: {{ ...CHART_DEFAULTS, ...extras,
      plugins: {{ ...CHART_DEFAULTS.plugins, ...extras.plugins }},
      scales: {{
        ...CHART_DEFAULTS.scales,
        y: {{ ...CHART_DEFAULTS.scales.y,
          ...(yMin !== null ? {{ min: yMin }} : {{}}),
          ...(yMax !== null ? {{ max: yMax }} : {{}}),
          ...((extras.scales||{{}}).y || {{}})
        }},
        x: {{ ...CHART_DEFAULTS.scales.x, ...((extras.scales||{{}}).x || {{}}) }}
      }},
    }},
  }});
}}

// VIX chart with green/yellow/red bands
const vixPlugin = {{
  id: 'vixBand',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    const y30 = scales.y.getPixelForValue(30);
    const y20 = scales.y.getPixelForValue(20);
    const y0  = scales.y.getPixelForValue(0);
    ctx.save();
    // 綠：<20（低波動）
    ctx.fillStyle = 'rgba(0,214,143,0.06)';
    ctx.fillRect(chartArea.left, y20, chartArea.right - chartArea.left, Math.min(y0, chartArea.bottom) - y20);
    // 黃：20-30（警戒）
    ctx.fillStyle = 'rgba(255,170,0,0.06)';
    ctx.fillRect(chartArea.left, y30, chartArea.right - chartArea.left, y20 - y30);
    // 紅：>30（恐慌）
    ctx.fillStyle = 'rgba(255,71,87,0.07)';
    ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, y30 - chartArea.top);
    // 分隔線
    ctx.setLineDash([4,4]);
    ctx.strokeStyle = 'rgba(255,71,87,0.35)';
    ctx.beginPath(); ctx.moveTo(chartArea.left, y30); ctx.lineTo(chartArea.right, y30); ctx.stroke();
    ctx.strokeStyle = 'rgba(255,170,0,0.35)';
    ctx.beginPath(); ctx.moveTo(chartArea.left, y20); ctx.lineTo(chartArea.right, y20); ctx.stroke();
    ctx.restore();
  }}
}};

const vixChart = makeChart('vixChart', [{{
  data: VIX_DATA,
  borderColor: '#00b8ff',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.3,
}}], 0, null);
Chart.register(vixPlugin);
vixChart.config.plugins = [vixPlugin];
vixChart.update();

// Fear & Greed chart
const fgPlugin = {{
  id: 'fgBands',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    ctx.save();
    const bands = [
      [0,   25, 'rgba(255,71,87,0.08)'],
      [25,  45, 'rgba(255,170,0,0.05)'],
      [55,  75, 'rgba(0,214,143,0.05)'],
      [75, 100, 'rgba(0,214,143,0.08)'],
    ];
    bands.forEach(([lo, hi, color]) => {{
      const y1 = scales.y.getPixelForValue(hi);
      const y2 = scales.y.getPixelForValue(lo);
      ctx.fillStyle = color;
      ctx.fillRect(chartArea.left, y1, chartArea.right - chartArea.left, y2 - y1);
    }});
    ctx.restore();
  }}
}};

const fgChart = makeChart('fgChart', [{{
  data: FG_DATA,
  borderColor: '#c8d6e8',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.4,
  segment: {{
    borderColor: ctx => ctx.p1.parsed.y < 25 ? '#ff4757' :
                        ctx.p1.parsed.y < 45 ? '#ffaa00' :
                        ctx.p1.parsed.y > 75 ? '#00d68f' : '#c8d6e8',
  }},
}}], 0, 100);
Chart.register(fgPlugin);
fgChart.config.plugins = [fgPlugin];
fgChart.update();

// S&P 500 chart with ATH line
const spHighPlugin = {{
  id: 'spHighLine',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    const yHigh = scales.y.getPixelForValue(SP_HIST_HIGH);
    if (yHigh < chartArea.top || yHigh > chartArea.bottom) return;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,71,87,0.5)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6,4]);
    ctx.beginPath();
    ctx.moveTo(chartArea.left, yHigh);
    ctx.lineTo(chartArea.right, yHigh);
    ctx.stroke();
    ctx.fillStyle = 'rgba(255,71,87,0.7)';
    ctx.font = "10px 'Share Tech Mono'";
    ctx.fillText('ATH ' + SP_HIST_HIGH.toLocaleString(), chartArea.left + 6, yHigh - 4);
    ctx.restore();
  }}
}};

const spChart = makeChart('spChart', [
  {{
    label: 'S&P 500',
    data: SP_DATA,
    borderColor: '#00d68f',
    borderWidth: 1.5,
    pointRadius: 0,
    fill: false,
    parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
    tension: 0.2,
  }},
  {{
    label: '200MA',
    data: MA_DATA,
    borderColor: '#ffaa00',
    borderWidth: 1.5,
    pointRadius: 0,
    fill: false,
    parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
    tension: 0.2,
    borderDash: [5,3],
  }},
], null, null);
Chart.register(spHighPlugin);
spChart.config.plugins = [spHighPlugin];
spChart.update();

// TAIEX chart with historical high line
const twHighPlugin = {{
  id: 'twHighLine',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    const yHigh = scales.y.getPixelForValue(TW_HIST_HIGH);
    if (yHigh < chartArea.top || yHigh > chartArea.bottom) return;
    ctx.save();
    ctx.strokeStyle = 'rgba(255,71,87,0.5)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6,4]);
    ctx.beginPath();
    ctx.moveTo(chartArea.left, yHigh);
    ctx.lineTo(chartArea.right, yHigh);
    ctx.stroke();
    ctx.fillStyle = 'rgba(255,71,87,0.7)';
    ctx.font = "10px 'Share Tech Mono'";
    ctx.fillText('ATH ' + TW_HIST_HIGH.toLocaleString(), chartArea.left + 6, yHigh - 4);
    ctx.restore();
  }}
}};

const twChart = makeChart('twChart', [
  {{
    label: '加權指數',
    data: TW_DATA,
    borderColor: '#ff4757',
    borderWidth: 1.5,
    pointRadius: 0,
    fill: false,
    parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
    tension: 0.2,
  }},
  {{
    label: '200MA',
    data: TW_MA_DATA,
    borderColor: '#ffaa00',
    borderWidth: 1.5,
    pointRadius: 0,
    fill: false,
    parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
    tension: 0.2,
    borderDash: [5,3],
  }},
], null, null);
Chart.register(twHighPlugin);
twChart.config.plugins = [twHighPlugin];
twChart.update();

// CAPE chart with historical avg + danger band
const capePlugin = {{
  id: 'capeBands',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    ctx.save();
    const y35 = scales.y.getPixelForValue(35);
    const y25 = scales.y.getPixelForValue(25);
    const yAvg = scales.y.getPixelForValue(CAPE_AVG);
    // 高估警戒區 >35（紅）
    ctx.fillStyle = 'rgba(255,71,87,0.07)';
    ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, y35 - chartArea.top);
    // 偏高區 25-35（黃）
    ctx.fillStyle = 'rgba(255,170,0,0.05)';
    ctx.fillRect(chartArea.left, y35, chartArea.right - chartArea.left, y25 - y35);
    // 合理區 <25（綠）
    ctx.fillStyle = 'rgba(0,214,143,0.05)';
    ctx.fillRect(chartArea.left, y25, chartArea.right - chartArea.left, chartArea.bottom - y25);
    // 歷史均值線
    ctx.strokeStyle = 'rgba(255,170,0,0.6)';
    ctx.lineWidth = 1;
    ctx.setLineDash([6,4]);
    ctx.beginPath(); ctx.moveTo(chartArea.left, yAvg); ctx.lineTo(chartArea.right, yAvg); ctx.stroke();
    ctx.fillStyle = 'rgba(255,170,0,0.8)';
    ctx.font = "10px 'Share Tech Mono'";
    ctx.fillText('均值 ' + CAPE_AVG, chartArea.left + 6, yAvg - 4);
    ctx.restore();
  }}
}};

const capeChart = makeChart('capeChart', [{{
  label: 'CAPE',
  data: CAPE_DATA,
  borderColor: '#c8a0ff',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.2,
}}], null, null);
Chart.register(capePlugin);
capeChart.config.plugins = [capePlugin];
capeChart.update();

// 失業率
const unrateChart = makeChart('unrateChart', [{{
  data: UNRATE_DATA,
  borderColor: '{unemp_color}',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.3,
}}], null, null);

// 核心 CPI YoY（綠<2.5 / 黃2.5-4 / 紅>4）
const cpiPlugin = {{
  id: 'cpiBands',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea || !scales.y) return;
    ctx.save();
    const y4  = scales.y.getPixelForValue(4);
    const y25 = scales.y.getPixelForValue(2.5);
    const y0  = scales.y.getPixelForValue(0);
    // 紅：>4%
    if (y4 >= chartArea.top) {{
      ctx.fillStyle = 'rgba(255,71,87,0.07)';
      ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, y4 - chartArea.top);
    }}
    // 黃：2.5-4%
    const y4c = Math.max(y4, chartArea.top);
    const y25c = Math.min(y25, chartArea.bottom);
    if (y25c > y4c) {{
      ctx.fillStyle = 'rgba(255,170,0,0.06)';
      ctx.fillRect(chartArea.left, y4c, chartArea.right - chartArea.left, y25c - y4c);
    }}
    // 綠：<2.5%
    if (y25 <= chartArea.bottom) {{
      ctx.fillStyle = 'rgba(0,214,143,0.06)';
      ctx.fillRect(chartArea.left, Math.max(y25, chartArea.top), chartArea.right - chartArea.left, chartArea.bottom - Math.max(y25, chartArea.top));
    }}
    // 參考線
    ctx.setLineDash([4,4]);
    [{{v:4,c:'rgba(255,71,87,0.4)'}},{{v:2.5,c:'rgba(255,170,0,0.4)'}}].forEach(l => {{
      const yp = scales.y.getPixelForValue(l.v);
      if (yp >= chartArea.top && yp <= chartArea.bottom) {{
        ctx.strokeStyle = l.c;
        ctx.beginPath(); ctx.moveTo(chartArea.left, yp); ctx.lineTo(chartArea.right, yp); ctx.stroke();
      }}
    }});
    ctx.restore();
  }}
}};
const cpiChart = makeChart('cpiChart', [{{
  data: CPI_YOY_DATA,
  borderColor: '{cpi_color}',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.3,
  segment: {{
    borderColor: ctx => ctx.p1.parsed.y > (CPI_YOY_DATA[Math.max(0, ctx.p1DataIndex-1)]?.y ?? 0)
      ? '#ff4757' : '#00d68f',
  }},
}}], null, null);
Chart.register(cpiPlugin);
cpiChart.config.plugins = [cpiPlugin];
cpiChart.update();

// ISM 新訂單
const ismPlugin = {{
  id: 'ism50',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    const y50 = scales.y.getPixelForValue(50);
    ctx.save();
    // 綠：≥50 擴張
    ctx.fillStyle = 'rgba(0,214,143,0.06)';
    ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, y50 - chartArea.top);
    // 紅：<50 收縮
    ctx.fillStyle = 'rgba(255,71,87,0.07)';
    ctx.fillRect(chartArea.left, y50, chartArea.right - chartArea.left, chartArea.bottom - y50);
    ctx.strokeStyle = 'rgba(255,71,87,0.45)';
    ctx.lineWidth = 1;
    ctx.setLineDash([5,4]);
    ctx.beginPath(); ctx.moveTo(chartArea.left, y50); ctx.lineTo(chartArea.right, y50); ctx.stroke();
    ctx.fillStyle = 'rgba(255,71,87,0.7)';
    ctx.font = "10px 'Share Tech Mono'";
    ctx.fillText('50 榮枯線', chartArea.left + 6, y50 - 4);
    ctx.restore();
  }}
}};
const ismChart = makeChart('ismChart', [{{
  data: ISM_DATA,
  borderColor: '#00b8ff',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.3,
  segment: {{
    borderColor: ctx => ctx.p1.parsed.y < 50 ? '#ff4757' : '#00b8ff',
  }},
}}], null, null);
Chart.register(ismPlugin);
ismChart.config.plugins = [ismPlugin];
ismChart.update();

// 10Y-2Y 利差
const ycPlugin = {{
  id: 'ycBands',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    const y0   = scales.y.getPixelForValue(0);
    const y05  = scales.y.getPixelForValue(0.5);
    ctx.save();
    // 紅：<0 倒掛
    if (y0 <= chartArea.bottom) {{
      ctx.fillStyle = 'rgba(255,71,87,0.08)';
      ctx.fillRect(chartArea.left, Math.max(y0, chartArea.top), chartArea.right - chartArea.left, chartArea.bottom - Math.max(y0, chartArea.top));
    }}
    // 黃：0–0.5% 警戒
    const y0c  = Math.min(y0, chartArea.bottom);
    const y05c = Math.max(y05, chartArea.top);
    if (y0c > y05c) {{
      ctx.fillStyle = 'rgba(255,170,0,0.06)';
      ctx.fillRect(chartArea.left, y05c, chartArea.right - chartArea.left, y0c - y05c);
    }}
    // 綠：≥0.5% 正常
    if (y05 >= chartArea.top) {{
      ctx.fillStyle = 'rgba(0,214,143,0.06)';
      ctx.fillRect(chartArea.left, chartArea.top, chartArea.right - chartArea.left, y05c - chartArea.top);
    }}
    // 參考線
    ctx.setLineDash([5,4]); ctx.lineWidth = 1;
    if (y0 >= chartArea.top && y0 <= chartArea.bottom) {{
      ctx.strokeStyle = 'rgba(255,71,87,0.5)';
      ctx.beginPath(); ctx.moveTo(chartArea.left, y0); ctx.lineTo(chartArea.right, y0); ctx.stroke();
      ctx.fillStyle = 'rgba(255,71,87,0.7)';
      ctx.font = "10px 'Share Tech Mono'"; ctx.setLineDash([]);
      ctx.fillText('0 倒掛', chartArea.left + 6, y0 - 4);
      ctx.setLineDash([5,4]);
    }}
    if (y05 >= chartArea.top && y05 <= chartArea.bottom) {{
      ctx.strokeStyle = 'rgba(255,170,0,0.4)';
      ctx.beginPath(); ctx.moveTo(chartArea.left, y05); ctx.lineTo(chartArea.right, y05); ctx.stroke();
    }}
    ctx.restore();
  }}
}};
const yieldCurveChart = makeChart('yieldCurveChart', [{{
  data: T10Y2Y_DATA,
  borderColor: '#c8d6e8',
  borderWidth: 1.5,
  pointRadius: 0,
  fill: false,
  parsing: {{ xAxisKey: 'x', yAxisKey: 'y' }},
  tension: 0.2,
  segment: {{
    borderColor: ctx => ctx.p1.parsed.y < 0 ? '#ff4757' :
                        ctx.p1.parsed.y < 0.5 ? '#ffaa00' : '#00d68f',
  }},
}}], null, null);
Chart.register(ycPlugin);
yieldCurveChart.config.plugins = [ycPlugin];
yieldCurveChart.update();

// Range selector
function setRange(days) {{
  document.querySelectorAll('.btn').forEach(b => b.classList.remove('active'));
  event.target.classList.add('active');

  const cutoff = days > 0
    ? new Date(Date.now() - days * 86400000).toISOString().slice(0,10)
    : '1900-01-01';

  function filter(arr) {{
    return arr.filter(d => d.x >= cutoff);
  }}

  vixChart.data.datasets[0].data = filter(VIX_DATA);
  vixChart.update('none');

  fgChart.data.datasets[0].data = filter(FG_DATA);
  fgChart.update('none');

  spChart.data.datasets[0].data = filter(SP_DATA);
  spChart.data.datasets[1].data = filter(MA_DATA);
  spChart.update('none');

  twChart.data.datasets[0].data = filter(TW_DATA);
  twChart.data.datasets[1].data = filter(TW_MA_DATA);
  twChart.update('none');

  capeChart.data.datasets[0].data = filter(CAPE_DATA);
  capeChart.update('none');

  unrateChart.data.datasets[0].data = filter(UNRATE_DATA);
  unrateChart.update('none');
  cpiChart.data.datasets[0].data = filter(CPI_YOY_DATA);
  cpiChart.update('none');
  ismChart.data.datasets[0].data = filter(ISM_DATA);
  ismChart.update('none');
  yieldCurveChart.data.datasets[0].data = filter(T10Y2Y_DATA);
  yieldCurveChart.update('none');
}}
</script>
</body>
</html>"""
    return html


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取 VIX 資料...")
    vix_data = fetch_yfinance("^VIX", "20y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取 S&P 500 資料與 200MA...")
    sp_data, ma_data, sp_hist_high = fetch_sp500_with_ma("20y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取 CNN Fear & Greed...")
    fg_data = fetch_fear_greed()

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取台股加權指數資料...")
    tw_data, tw_ma_data, tw_hist_high = fetch_taiex_with_ma("20y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取席勒本益比 CAPE...")
    cape_data = fetch_cape()

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取衰退預警指標...")
    recession = fetch_recession_signals()

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 生成 HTML...")

    html = generate_html(vix_data, sp_data, ma_data, sp_hist_high, fg_data, tw_data, tw_ma_data, tw_hist_high, cape_data, recession, generated_at)
    OUTPUT.write_text(html, encoding="utf-8")

    vix_current = vix_data[-1]["y"] if vix_data else "N/A"
    sp_current = sp_data[-1]["y"] if sp_data else "N/A"
    ma_current = ma_data[-1]["y"] if ma_data else "N/A"
    fg_score = fg_data["score"] or "N/A"
    fg_rating = fg_data["rating"]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成 → {OUTPUT}")
    sp_vs_ma_str = f"{trunc2((sp_current/ma_current-1)*100):+.2f}%" if isinstance(sp_current, float) and isinstance(ma_current, float) else "N/A"
    above_below = "高於" if isinstance(sp_current, float) and isinstance(ma_current, float) and sp_current >= ma_current else "低於"
    tw_current = tw_data[-1]["y"] if tw_data else "N/A"
    tw_ma_current = tw_ma_data[-1]["y"] if tw_ma_data else "N/A"
    tw_vs_ma_str = f"{trunc2((tw_current/tw_ma_current-1)*100):+.2f}%" if isinstance(tw_current, float) and isinstance(tw_ma_current, float) else "N/A"
    tw_vs_high_str = f"{trunc2((tw_current/tw_hist_high-1)*100):.1f}%" if isinstance(tw_current, float) and tw_hist_high else "N/A"
    print(f"  VIX: {vix_current}  |  S&P: {sp_current}  |  200MA: {ma_current}  |  {above_below} 200MA ({sp_vs_ma_str})  |  F&G: {fg_score} ({fg_rating})")
    print(f"  加權指數: {tw_current}  |  200MA: {tw_ma_current}  |  vs 200MA: {tw_vs_ma_str}  |  距高點: {tw_vs_high_str}  |  ATH: {tw_hist_high}")

    send_telegram(
        f"📊 市場儀表板已更新 {generated_at}\n"
        f"VIX：{vix_current}\n"
        f"Fear & Greed：{fg_score} ({fg_rating})\n"
        f"S&P 500：{sp_current:,.2f}\n"
        f"台股加權：{tw_current:,.0f}　vs 200MA {tw_vs_ma_str}　距高點 {tw_vs_high_str}"
    )

    # S&P 500 vs 200MA 狀態變化通知（只在跌破 / 收復時各通知一次）
    if isinstance(sp_current, float) and isinstance(ma_current, float):
        sp_vs_ma = trunc2((sp_current / ma_current - 1) * 100)
        current_state = "below" if sp_current < ma_current else "above"
        prev_state = json.loads(SP_STATE.read_text()).get("state") if SP_STATE.exists() else None
        SP_STATE.write_text(json.dumps({"state": current_state, "date": generated_at}))

        if current_state == "below":
            send_telegram(
                f"🚨 S&P 500 低於 200 日均線\n"
                f"S&P 500：{sp_current:,.0f}\n"
                f"200MA：{ma_current:,.2f}\n"
                f"偏離：{sp_vs_ma}%"
            )
        elif current_state == "above":
            send_telegram(
                f"✅ S&P 500 高於 200 日均線\n"
                f"S&P 500：{sp_current:,.0f}\n"
                f"200MA：{ma_current:,.2f}\n"
                f"偏離：+{sp_vs_ma}%"
            )


if __name__ == "__main__":
    main()
