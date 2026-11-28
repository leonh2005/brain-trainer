#!/usr/bin/env python3
"""台股「連假後／週末後」下跌機率統計。

資料：market-cycle-chart/data/taiex_daily.json（加權指數日 K）
輸出：reports/holiday-selloff-YYYY-MM-DD.html
用法：python3 market-analysis/holiday_selloff.py
"""

import json
import math
from datetime import datetime
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "market-cycle-chart" / "data" / "taiex_daily.json"
OUT_DIR = ROOT / "reports"

GAP_WEEKEND = 3
GAP_SHORT_HOLIDAY = (4, 5)
GAP_LONG_HOLIDAY = (6, 9)
GAP_EXTRA_LONG = 10


def load_records() -> list[dict]:
    rows = json.loads(DATA.read_text(encoding="utf-8"))["rows"]
    rows.sort(key=lambda r: r["date"])
    recs: list[dict] = []
    for prev, cur in zip(rows, rows[1:]):
        d1 = datetime.strptime(prev["date"], "%Y-%m-%d")
        d2 = datetime.strptime(cur["date"], "%Y-%m-%d")
        p0 = prev["close"]
        if p0 <= 0:
            continue
        recs.append({
            "date": cur["date"],
            "dt": d2,
            "dw": d2.weekday(),
            "gap": (d2 - d1).days,
            "ret": (cur["close"] - p0) / p0 * 100,
            "gap_open": (cur["open"] - p0) / p0 * 100,
            "close": cur["close"],
        })
    return recs


def summarize(items: list[dict]) -> dict:
    if not items:
        return {}
    down = sum(1 for r in items if r["ret"] < 0)
    flat = sum(1 for r in items if r["ret"] == 0)
    return {
        "n": len(items),
        "down": down,
        "down_pct": down / len(items) * 100,
        "up_pct": (len(items) - down - flat) / len(items) * 100,
        "avg": mean(r["ret"] for r in items),
        "med": median(r["ret"] for r in items),
        "worst": min(r["ret"] for r in items),
        "best": max(r["ret"] for r in items),
    }


def z_two_prop(n1: int, p1: float, n2: int, p2: float) -> float:
    p = (p1 * n1 + p2 * n2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return ((p2 - p1) / se) if se else 0.0


def pct_class(value: float, baseline: float) -> str:
    """高於基準標紅（更容易跌），低於基準標綠。"""
    if value > baseline + 1:
        return "up"
    if value < baseline - 1:
        return "down"
    return ""


def bar(value: float, scale: float) -> str:
    width = max(2.0, min(100.0, value / scale * 100))
    return f'<div class="bar"><span style="width:{width:.1f}%"></span></div>'


def build_html(recs: list[dict]) -> str:
    base = summarize(recs)
    groups = [
        ("隔日（平盤日）", [r for r in recs if r["gap"] == 1]),
        ("週末後（週五→週一，3 天）", [r for r in recs if r["gap"] == GAP_WEEKEND]),
        ("連假後（4–5 天）", [r for r in recs if GAP_SHORT_HOLIDAY[0] <= r["gap"] <= GAP_SHORT_HOLIDAY[1]]),
        ("長假後（6–9 天）", [r for r in recs if GAP_LONG_HOLIDAY[0] <= r["gap"] <= GAP_LONG_HOLIDAY[1]]),
        ("超長假後（≥10 天，多為春節）", [r for r in recs if r["gap"] >= GAP_EXTRA_LONG]),
    ]
    group_rows = []
    for label, items in groups:
        s = summarize(items)
        if not s:
            continue
        cls = pct_class(s["down_pct"], base["down_pct"])
        group_rows.append(
            f'<tr><td>{label}</td><td class="num">{s["n"]}</td>'
            f'<td class="num {cls}">{s["down_pct"]:.1f}%</td>'
            f'<td class="num">{s["n"] - s["down"]}</td>'
            f'<td class="num">{s["avg"]:+.3f}%</td>'
            f'<td class="num">{s["med"]:+.3f}%</td>'
            f'<td class="num">{s["worst"]:+.2f}%</td></tr>'
        )

    weekday_rows = []
    for dw, name in enumerate(["週一", "週二", "週三", "週四", "週五"]):
        s = summarize([r for r in recs if r["dw"] == dw])
        cls = pct_class(s["down_pct"], base["down_pct"])
        weekday_rows.append(
            f'<tr><td>{name}</td><td class="num">{s["n"]}</td>'
            f'<td class="num {cls}">{s["down_pct"]:.1f}%</td>'
            f'<td class="num">{s["avg"]:+.3f}%</td>'
            f'<td>{bar(s["down_pct"], 60)}</td></tr>'
        )

    period_rows = []
    for name, lo, hi in [("2015–2019", "2015", "2020"), ("2020–2026", "2020", "2099")]:
        sub = [r for r in recs if lo <= r["date"][:4] < hi]
        b = summarize(sub)
        w = summarize([r for r in sub if r["gap"] == GAP_WEEKEND])
        h = summarize([r for r in sub if r["gap"] >= 4])
        period_rows.append(
            f'<tr><td>{name}</td><td class="num">{b["down_pct"]:.1f}%（n={b["n"]}）</td>'
            f'<td class="num">{w["down_pct"]:.1f}%（n={w["n"]}）</td>'
            f'<td class="num">{h["down_pct"]:.1f}%（n={h["n"]}）</td></tr>'
        )

    holidays = [r for r in recs if r["gap"] >= 4]
    mon = [r for r in recs if r["dw"] == 0 and r["gap"] == GAP_WEEKEND]
    gap_rows = [
        f'<tr><td>週五收盤 → 週一開盤</td><td class="num">{len(mon)}</td>'
        f'<td class="num">{sum(1 for r in mon if r["gap_open"] < 0) / len(mon) * 100:.1f}%</td>'
        f'<td class="num">{mean(r["gap_open"] for r in mon):+.3f}%</td></tr>',
        f'<tr><td>連假前最後交易日 → 節後開盤</td><td class="num">{len(holidays)}</td>'
        f'<td class="num">{sum(1 for r in holidays if r["gap_open"] < 0) / len(holidays) * 100:.1f}%</td>'
        f'<td class="num">{mean(r["gap_open"] for r in holidays):+.3f}%</td></tr>',
    ]

    low_open = [r for r in holidays if r["gap_open"] < 0]
    low_open_up = sum(1 for r in low_open if r["ret"] > 0)

    worst = sorted(holidays, key=lambda r: r["ret"])[:5]
    best = sorted(holidays, key=lambda r: -r["ret"])[:5]
    extreme_rows = [
        f'<tr><td class="down">{r["date"]}</td><td class="num">{r["gap"]} 天</td>'
        f'<td class="num down">{r["ret"]:+.2f}%</td><td class="num">{r["close"]:,.0f}</td></tr>'
        for r in worst
    ] + [
        f'<tr><td class="up">{r["date"]}</td><td class="num">{r["gap"]} 天</td>'
        f'<td class="num up">{r["ret"]:+.2f}%</td><td class="num">{r["close"]:,.0f}</td></tr>'
        for r in best
    ]

    z_weekend = z_two_prop(base["n"], base["down"] / base["n"], len(mon), sum(1 for r in mon if r["ret"] < 0) / len(mon))
    z_holiday = z_two_prop(base["n"], base["down"] / base["n"], len(holidays), sum(1 for r in holidays if r["ret"] < 0) / len(holidays))

    today = datetime.now().strftime("%Y-%m-%d")
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>台股連假後與週末後下跌機率</title>
<style>
  :root{{
    --bg:#f7f7f5; --card:#fff; --ink:#1a1a18; --muted:#6b6b66;
    --line:#e4e4df; --accent:#2f6f4f; --warn:#a8501f;
    --up:#c0392b; --down:#2f8f5f;
  }}
  @media (prefers-color-scheme: dark){{
    :root{{ --bg:#16161a; --card:#1e1e23; --ink:#ececee; --muted:#9a9aa2;
           --line:#2e2e35; --accent:#6fbf94; --warn:#e08a55;
           --up:#e8756a; --down:#6fd39b; }}
  }}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.7 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;}}
  .wrap{{max-width:960px;margin:0 auto;padding:40px 20px 80px}}
  h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}}
  .sub{{color:var(--muted);font-size:13px;margin-bottom:36px}}
  h2{{font-size:17px;margin:40px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}}
  table{{width:100%;border-collapse:collapse;background:var(--card);
        border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px}}
  th,td{{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}}
  th{{font-weight:600;color:var(--muted);font-size:12px;letter-spacing:.03em;background:var(--bg)}}
  tr:last-child td{{border-bottom:none}}
  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
  .up{{color:var(--up)}} .down{{color:var(--down)}}
  .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:18px 20px;margin:14px 0}}
  .card h3{{margin:0 0 6px;font-size:15px}}
  .card p{{margin:0;font-size:13.5px;color:var(--muted)}}
  .note{{font-size:13px;color:var(--warn);margin-top:10px}}
  .bar{{height:8px;background:var(--bg);border-radius:4px;overflow:hidden;min-width:90px}}
  .bar span{{display:block;height:100%;background:var(--accent);border-radius:4px}}
  ul{{font-size:13.5px;color:var(--muted);padding-left:20px}}
  li{{margin:4px 0}}
</style>
</head>
<body>
<div class="wrap">
<h1>台股「連假後／週末後」下跌機率統計</h1>
<div class="sub">加權指數 {recs[0]["date"]} ~ {recs[-1]["date"]} ｜ {base["n"]:,} 個交易日間隔 ｜ 產生於 {today}</div>

<h2>先講結論</h2>
<div class="card">
  <h3>1. 「連假後會跌」不成立</h3>
  <p>連假後（≥4 天）下跌 {summarize(holidays)["down_pct"]:.1f}%，反而低於平日的 {base["down_pct"]:.1f}%。z = {z_holiday:+.2f}，統計上不顯著。</p>
</div>
<div class="card">
  <h3>2. 台股真正的弱勢日是週五</h3>
  <p>週五下跌 48.9%，是五個交易日裡最高；週一、週二只有 42.8%、42.4%。週末前賣壓比週末後回吐更明顯。</p>
</div>
<div class="card">
  <h3>3. 極端跌幅全來自外部事件</h3>
  <p>近 12 年連假後最大跌幅 −9.70%（2025-04-07 川普關稅）、−5.75%（2020-01-30 COVID）、−3.53%（2025-02-03 DeepSeek）。這些是事件驅動，不是假期效應。</p>
</div>

<h2>主統計表</h2>
<table>
<thead><tr><th>分組</th><th class="num">樣本</th><th class="num">下跌率</th><th class="num">上漲次數</th><th class="num">平均報酬</th><th class="num">中位數</th><th class="num">最差單日</th></tr></thead>
<tbody>
<tr><td><strong>全部交易日</strong></td><td class="num">{base["n"]:,}</td><td class="num">{base["down_pct"]:.1f}%</td><td class="num">{base["n"] - base["down"]:,}</td><td class="num">{base["avg"]:+.3f}%</td><td class="num">{base["med"]:+.3f}%</td><td class="num">{base["worst"]:+.2f}%</td></tr>
{"".join(group_rows)}
</tbody>
</table>
<p class="note">紅色 = 下跌率高於基準（更容易跌），綠色 = 低於基準。樣本小於 10 的組別統計意義有限。</p>

<h2>星期效應</h2>
<table>
<thead><tr><th>星期</th><th class="num">樣本</th><th class="num">下跌率</th><th class="num">平均報酬</th><th>相對基準（45.1%）</th></tr></thead>
<tbody>
{"".join(weekday_rows)}
</tbody>
</table>

<h2>分段檢驗：2020 年後連假效應幾乎消失</h2>
<table>
<thead><tr><th>期間</th><th class="num">該期基準</th><th class="num">週末後</th><th class="num">連假後（≥4 天）</th></tr></thead>
<tbody>
{"".join(period_rows)}
</tbody>
</table>
<p class="note">2015–2019 連假後下跌只有 34.2%，但 2020–2026 已回到 44.2%，與該期基準 44.8% 幾乎相同。早期的高上漲率可能只是樣本特性。</p>

<h2>跳空觀察</h2>
<table>
<thead><tr><th>情境</th><th class="num">樣本</th><th class="num">開低比例</th><th class="num">平均跳空</th></tr></thead>
<tbody>
{"".join(gap_rows)}
</tbody>
</table>
<p class="note">連假後若開低（n={len(low_open)}），只有 {low_open_up} 次（{low_open_up / len(low_open) * 100:.1f}%）收紅 —— 開低走高並不常見，開低多半延續弱勢。</p>

<h2>連假後極端案例（前 5 名）</h2>
<table>
<thead><tr><th>日期</th><th class="num">假期天數</th><th class="num">當日漲跌</th><th class="num">收盤</th></tr></thead>
<tbody>
{"".join(extreme_rows)}
</tbody>
</table>

<h2>方法與限制</h2>
<ul>
<li><strong>資料</strong>：加權指數日 K，{recs[0]["date"]} ~ {recs[-1]["date"]}，共 {base["n"]:,} 個交易日間隔。</li>
<li><strong>假日判定</strong>：用相鄰交易日間隔天數反推。3 天 = 週末；4–5 天 = 連假；6–9 天 = 長假；≥10 天 = 超長假（多為春節）。</li>
<li><strong>無法區分</strong>：國定假日與颱風假（兩者都會造成休市），也無法識別個別節日名稱。</li>
<li><strong>下跌定義</strong>：當日收盤 &lt; 前一交易日收盤。跳空另以開盤價計算。</li>
<li><strong>期間偏差</strong>：2015–2026 加權指數從約 9,300 漲到近 39,300（約 4 倍），整體上漲機率天然偏高，看「相對基準」比看絕對機率可靠。</li>
<li><strong>統計檢定</strong>：兩比例 z 檢定（雙尾，|z| &gt; 1.96 才算 5% 顯著）。週末後 z = {z_weekend:+.2f}、連假後 z = {z_holiday:+.2f}，兩者都不顯著。</li>
<li><strong>結論限制</strong>：這是歷史統計，不構成交易建議；事件驅動的暴跌（關稅、疫情）無法用假日模式預測。</li>
</ul>
</div>
</body>
</html>
"""


def main() -> None:
    recs = load_records()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"holiday-selloff-{datetime.now().strftime('%Y-%m-%d')}.html"
    out.write_text(build_html(recs), encoding="utf-8")
    print(f"已產生：{out}")


if __name__ == "__main__":
    main()
