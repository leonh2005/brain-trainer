#!/usr/bin/env python3
"""
市場恐慌儀表板生成器
每日抓取 VIX、S&P 500 200MA、CNN Fear & Greed，生成自含 HTML
"""

import json
import requests
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("請安裝：pip3 install yfinance pandas")
    sys.exit(1)

OUTPUT   = Path(__file__).parent / "index.html"
FG_CACHE = Path(__file__).parent / "fg_history.json"
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
        result.append({"x": date.strftime("%Y-%m-%d"), "y": round(float(val), 2)})
    return result


def fetch_sp500_with_ma(period="20y"):
    df = yf.download("^GSPC", period=period, progress=False, auto_adjust=True)
    close = df[("Close", "^GSPC")].dropna()
    ma200 = close.rolling(200).mean()
    prices, mas = [], []
    for date, val in close.items():
        prices.append({"x": date.strftime("%Y-%m-%d"), "y": round(float(val), 2)})
    for date, val in ma200.items():
        if pd.notna(val):
            mas.append({"x": date.strftime("%Y-%m-%d"), "y": round(float(val), 2)})
    return prices, mas


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
             "y": round(p["y"], 1),
             "rating": p.get("rating", "")}
            for p in hist_raw
        ]
        score = round(current["score"], 1)
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


def generate_html(vix_data, sp_data, ma_data, fg_data, generated_at):
    vix_current = vix_data[-1]["y"] if vix_data else 0
    sp_current = sp_data[-1]["y"] if sp_data else 0
    ma_current = ma_data[-1]["y"] if ma_data else 0
    sp_vs_ma = round((sp_current / ma_current - 1) * 100, 2) if ma_current else 0
    fg_score = fg_data["score"]
    fg_rating = fg_data["rating"]

    # 顏色判斷
    vix_color = "#ff4757" if vix_current > 30 else ("#ffaa00" if vix_current > 20 else "#00d68f")
    fg_color = "#ff4757" if (fg_score or 50) < 25 else ("#ffaa00" if (fg_score or 50) < 45 else ("#00d68f" if (fg_score or 50) > 55 else "#e0e0e0"))
    sp_color = "#00d68f" if sp_vs_ma > 0 else "#ff4757"

    fg_score_display = str(fg_score) if fg_score is not None else "N/A"
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

  @media (max-width: 768px) {{
    .cards {{ grid-template-columns: 1fr; margin: 1rem; }}
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
    <div class="card-value">{vix_current}</div>
    <div class="card-sub">
      {'<b>極度恐慌 &gt;30</b>' if vix_current > 30 else ('<b>警戒 20–30</b>' if vix_current > 20 else '<b>低波動 &lt;20</b>')}
      &nbsp;·&nbsp; 標普 500 30日隱含波動率
    </div>
  </div>

  <div class="card" style="--accent: {fg_color}">
    <div class="card-label"><span class="pulse"></span>CNN 恐懼貪婪指數</div>
    <div class="card-value">{fg_score_display}</div>
    <div class="card-sub">
      <b>{fg_label}</b>
      &nbsp;·&nbsp; 0=極恐 / 100=極貪
    </div>
  </div>

  <div class="card" style="--accent: {sp_color}">
    <div class="card-label"><span class="pulse"></span>S&P 500 vs 200MA</div>
    <div class="card-value">{sp_current:,.0f}</div>
    <div class="card-sub">
      200MA：{ma_current:,.0f}
      &nbsp;·&nbsp; <b>{'▲' if sp_vs_ma > 0 else '▼'} {abs(sp_vs_ma)}%</b>
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
      <div class="chart-title"><b>S&P 500</b> 收盤價 vs 200日均線</div>
      <div class="chart-legend">
        <div class="legend-item"><div class="legend-dot" style="background:#00d68f"></div>S&P 500</div>
        <div class="legend-item"><div class="legend-dot" style="background:#ffaa00"></div>200MA</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="spChart"></canvas></div>
  </div>
</div>

<footer>
  <span>資料來源：Yahoo Finance · CNN Fear &amp; Greed</span>
  <span>每日 07:00 自動更新</span>
</footer>

<script>
const VIX_DATA = {json.dumps(vix_data)};
const SP_DATA  = {json.dumps(sp_data)};
const MA_DATA  = {json.dumps(ma_data)};
const FG_DATA  = {json.dumps(fg_data["history"])};

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

// VIX chart with danger band
const vixPlugin = {{
  id: 'vixBand',
  beforeDraw(chart) {{
    const {{ ctx, chartArea, scales }} = chart;
    if (!chartArea) return;
    const y30 = scales.y.getPixelForValue(30);
    const yTop = chartArea.top;
    ctx.save();
    ctx.fillStyle = 'rgba(255,71,87,0.06)';
    ctx.fillRect(chartArea.left, yTop, chartArea.right - chartArea.left, y30 - yTop);
    ctx.strokeStyle = 'rgba(255,71,87,0.3)';
    ctx.setLineDash([4,4]);
    ctx.beginPath(); ctx.moveTo(chartArea.left, y30); ctx.lineTo(chartArea.right, y30); ctx.stroke();
    const y20 = scales.y.getPixelForValue(20);
    ctx.strokeStyle = 'rgba(255,170,0,0.3)';
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

// S&P 500 chart
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
}}
</script>
</body>
</html>"""
    return html


def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取 VIX 資料...")
    vix_data = fetch_yfinance("^VIX", "20y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取 S&P 500 資料與 200MA...")
    sp_data, ma_data = fetch_sp500_with_ma("20y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 抓取 CNN Fear & Greed...")
    fg_data = fetch_fear_greed()

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] 生成 HTML...")

    html = generate_html(vix_data, sp_data, ma_data, fg_data, generated_at)
    OUTPUT.write_text(html, encoding="utf-8")

    vix_current = vix_data[-1]["y"] if vix_data else "N/A"
    sp_current = sp_data[-1]["y"] if sp_data else "N/A"
    fg_score = fg_data["score"] or "N/A"
    fg_rating = fg_data["rating"]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成 → {OUTPUT}")
    print(f"  VIX: {vix_current}  |  S&P: {sp_current}  |  F&G: {fg_score} ({fg_rating})")

    send_telegram(
        f"📊 市場儀表板已更新 {generated_at}\n"
        f"VIX：{vix_current}\n"
        f"Fear & Greed：{fg_score} ({fg_rating})\n"
        f"S&P 500：{sp_current:,.0f}"
    )


if __name__ == "__main__":
    main()
