#!/usr/bin/env python3
"""連假守衛儀表板：質押健康度、台股庫存、下次連假倒數。

直接重用 market-analysis/holiday_guard.py 的計算邏輯，不重複實作。
資料快取 30 秒，避免每次刷新都打 gateway。
"""

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template_string

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "market-analysis"))
import holiday_guard as hg  # noqa: E402

app = Flask(__name__)

CACHE_TTL = 30
_cache: dict = {"data": None, "ts": 0.0}


def build_status() -> dict:
    cfg: dict = {}
    if hg.CONFIG.exists():
        try:
            import json
            cfg = json.loads(hg.CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            cfg = {}

    positions, source = hg.get_positions(cfg, {})
    codes = hg.collect_codes(positions, cfg)
    prices, failed = hg.get_prices(codes)
    pledge = hg.compute_pledge(cfg, prices)

    pledged_codes = {str(p.get("code", "")): int(p.get("quantity", 0))
                     for p in cfg.get("pledged", [])}

    rows = []
    total_value = 0.0
    total_pnl = 0.0
    for pos in positions:
        code = str(pos.get("code", ""))
        qty = int(pos.get("quantity", 0) or 0)
        close = prices.get(code, {}).get("close")
        if close is None:
            rows.append({"code": code, "quantity": qty, "price": None,
                         "value": None, "pnl": None,
                         "pledged": code in pledged_codes})
            continue
        value = float(close) * qty
        pnl = pos.get("pnl")
        total_value += value
        if pnl is not None:
            total_pnl += float(pnl)
        rows.append({"code": code, "quantity": qty, "price": float(close),
                     "value": value, "pnl": float(pnl) if pnl is not None else None,
                     "pledged": code in pledged_codes})
    rows.sort(key=lambda r: -(r["value"] or 0))

    holidays, years = hg.load_holidays()
    today = date.today()
    t1 = hg.find_t1(today, holidays) if today.year in years else None
    holiday = {"t1": None, "resume": None, "offset": None,
               "days_to_t1": None, "status": "unknown"}
    events: list[str] = []
    calendar: list[dict] = []
    calendar_status = "ok"
    war: list[dict] = []
    if t1:
        offset = hg.trading_offset(today, t1, holidays)
        resume = hg.next_trading_day(t1, holidays)
        holiday = {
            "t1": t1.isoformat(),
            "resume": resume.isoformat() if resume else None,
            "offset": offset,
            "days_to_t1": (t1 - today).days,
            "status": f"T-{offset}" if offset <= hg.CHECK_WINDOW else "idle",
        }
        if resume:
            events, _ = hg.get_events(t1 + timedelta(days=1), resume)

    calendar, calendar_status = hg.get_calendar(30)
    war = hg.get_war_news()
    jev_bias = hg.jev_event_bias(calendar)

    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "pledge": {
            "status": pledge["status"],
            "value": pledge.get("value", 0),
            "loan": float(cfg.get("loan_amount", 0) or 0),
            "credit_limit": float(cfg.get("credit_limit", 0) or 0),
            "ratio": pledge.get("ratio"),
            "worst_ratio": pledge.get("worst_ratio"),
            "call_pct": pledge.get("call_pct", 130),
            "missing": pledge.get("missing", []),
        },
        "positions": rows,
        "totals": {"market_value": total_value, "pnl": total_pnl},
        "failed_codes": failed,
        "holiday": holiday,
        "events": events,
        "calendar": calendar,
        "calendar_status": calendar_status,
        "war": war,
        "jev_bias": jev_bias,
    }


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/status")
def api_status():
    now = time.time()
    if _cache["data"] is None or now - _cache["ts"] > CACHE_TTL:
        try:
            _cache["data"] = build_status()
            _cache["ts"] = now
        except Exception as exc:  # noqa: BLE001 - 前端要看到錯誤而不是白畫面
            return jsonify({"error": str(exc)[:200]}), 500
    return jsonify(_cache["data"])


@app.get("/")
def index():
    return render_template_string(HTML)


HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>連假守衛</title>
<style>
  :root{
    --bg:#f7f7f5; --card:#fff; --ink:#1a1a18; --muted:#6b6b66;
    --line:#e4e4df; --accent:#2f6f4f; --warn:#a8501f; --danger:#c0392b;
    --up:#d32f2f; --down:#1b8a4b;
  }
  @media (prefers-color-scheme: dark){
    :root{ --bg:#16161a; --card:#1e1e23; --ink:#ececee; --muted:#9a9aa2;
           --line:#2e2e35; --accent:#6fbf94; --warn:#e08a55; --danger:#e8756a;
           --up:#ff7061; --down:#4ad98a; }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:15px/1.7 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;}
  .wrap{max-width:960px;margin:0 auto;padding:36px 20px 80px}
  h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
  .sub{color:var(--muted);font-size:13px;margin-bottom:28px}
  h2{font-size:16px;margin:32px 0 10px;padding-bottom:8px;border-bottom:1px solid var(--line)}
  .grid{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:12px;align-items:stretch}
  @media (max-width:720px){.grid{grid-template-columns:1fr}}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:16px 18px;display:flex;flex-direction:column;min-height:150px}
  .card .label{font-size:12px;color:var(--muted);letter-spacing:.04em;min-height:18px}
  .metric{font-size:34px;font-weight:650;line-height:1.2;margin-top:2px;
          font-variant-numeric:tabular-nums;white-space:nowrap}
  .metric .unit{font-size:15px;font-weight:400;color:var(--muted);margin-left:2px}
  .hint{font-size:12.5px;color:var(--muted);margin-top:auto;padding-top:10px;line-height:1.5}
  .bar{height:8px;background:var(--bg);border-radius:4px;overflow:hidden;margin-top:10px}
  .bar span{display:block;height:100%;border-radius:4px;transition:width .4s}
  table{width:100%;border-collapse:collapse;background:var(--card);
        border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13.5px;
        table-layout:fixed}
  th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  th{font-weight:600;color:var(--muted);font-size:12px;letter-spacing:.03em;background:var(--bg)}
  tr:last-child td{border-bottom:none}
  th.num,td.num{text-align:right;font-variant-numeric:tabular-nums}
  .up{color:var(--up)} .down{color:var(--down)}
  .tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:20px;
       background:var(--bg);border:1px solid var(--line);color:var(--muted);margin-left:6px}
  ul.events{list-style:none;padding:0;margin:0;font-size:13.5px}
  ul.events li{background:var(--card);border:1px solid var(--line);border-radius:8px;
               padding:8px 12px;margin-bottom:6px;color:var(--muted)}
  .err{background:var(--card);border:1px solid var(--danger);border-radius:10px;
       padding:12px 14px;color:var(--danger);font-size:13.5px;display:none}
</style>
</head>
<body>
<div class="wrap">
  <h1>連假守衛</h1>
  <div class="sub">質押健康度 ｜ 最後更新 <span id="ts">--</span> ｜ 每 60 秒自動刷新</div>
  <div class="err" id="err"></div>

  <div class="grid">
    <div class="card">
      <div class="label">質押維持率</div>
      <div class="metric" id="ratio">--</div>
      <div class="bar"><span id="ratio-bar" style="width:0"></span></div>
      <div class="hint" id="ratio-hint">--</div>
    </div>
    <div class="card">
      <div class="label">壓力測試（跌 9.7%）</div>
      <div class="metric" id="worst">--</div>
      <div class="bar"><span id="worst-bar" style="width:0"></span></div>
      <div class="hint" id="worst-hint">--</div>
    </div>
    <div class="card">
      <div class="label">下次連假 T-1</div>
      <div class="metric" id="holiday">--</div>
      <div class="bar"><span id="holiday-bar" style="width:0"></span></div>
      <div class="hint" id="holiday-hint">--</div>
    </div>
  </div>

  <h2>質押部位</h2>
  <table>
    <colgroup>
      <col style="width:18%"><col style="width:20%"><col style="width:18%">
      <col style="width:24%"><col style="width:20%">
    </colgroup>
    <thead><tr><th>代號</th><th class="num">股數</th><th class="num">現價</th>
      <th class="num">市值</th><th class="num">損益</th></tr></thead>
    <tbody id="pledged-body"></tbody>
  </table>

  <h2>台股庫存 <span class="tag" id="source">--</span></h2>
  <table>
    <colgroup>
      <col style="width:14%"><col style="width:16%"><col style="width:16%">
      <col style="width:22%"><col style="width:18%"><col style="width:14%">
    </colgroup>
    <thead><tr><th>代號</th><th class="num">股數</th><th class="num">現價</th>
      <th class="num">市值</th><th class="num">損益</th><th>質押</th></tr></thead>
    <tbody id="positions-body"></tbody>
  </table>

  <h2>國際行事曆 <span class="tag" id="next-event-tag">--</span></h2>
  <table>
    <colgroup>
      <col style="width:11%"><col style="width:9%"><col style="width:26%">
      <col style="width:14%"><col style="width:40%">
    </colgroup>
    <thead><tr><th>日期</th><th class="num">倒數</th><th>事件</th>
      <th>Jev 判斷</th><th>觀察重點</th></tr></thead>
    <tbody id="calendar-body"></tbody>
  </table>

  <h2>地緣消息</h2>
  <ul class="events" id="war"><li>--</li></ul>
</div>
<script>
const fmt = n => n == null ? '--' : n.toLocaleString('zh-TW', {maximumFractionDigits: 0});
const fmt2 = n => n == null ? '--' : n.toLocaleString('zh-TW', {minimumFractionDigits: 2, maximumFractionDigits: 2});

function colorFor(ratio, call) {
  // 台股慣例：紅 = 好、綠 = 壞
  if (ratio == null) return 'var(--muted)';
  if (ratio < call) return 'var(--down)';
  if (ratio < call + 20) return 'var(--warn)';
  return 'var(--up)';
}

function render(d) {
  document.getElementById('ts').textContent = d.ts;
  document.getElementById('source').textContent = d.source;
  const p = d.pledge;

  const ratioEl = document.getElementById('ratio');
  const barEl = document.getElementById('ratio-bar');
  const hintEl = document.getElementById('ratio-hint');
  if (p.status === 'ok') {
    ratioEl.innerHTML = fmt(p.ratio) + '<span class="unit">%</span>';
    ratioEl.style.color = colorFor(p.ratio, p.call_pct);
    barEl.style.width = Math.min(100, p.ratio / 3) + '%';
    barEl.style.background = colorFor(p.ratio, p.call_pct);
    hintEl.textContent = `借款 ${fmt(p.loan)} ／ 擔保品 ${fmt(p.value)} ／ 追繳線 ${p.call_pct}%`;
  } else if (p.status === 'missing') {
    ratioEl.textContent = '缺價';
    ratioEl.style.color = 'var(--warn)';
    hintEl.textContent = '缺價代碼：' + p.missing.join(', ');
  } else {
    ratioEl.textContent = '未設定';
    ratioEl.style.color = 'var(--muted)';
    hintEl.textContent = 'config/pledge_portfolio.json 尚未填寫';
  }

  const worstEl = document.getElementById('worst');
  const worstBar = document.getElementById('worst-bar');
  const worstHint = document.getElementById('worst-hint');
  if (p.status === 'ok') {
    worstEl.innerHTML = fmt(p.worst_ratio) + '<span class="unit">%</span>';
    worstEl.style.color = colorFor(p.worst_ratio, p.call_pct);
    worstBar.style.width = Math.min(100, p.worst_ratio / 3) + '%';
    worstBar.style.background = colorFor(p.worst_ratio, p.call_pct);
    let extra = '';
    if (p.credit_limit > p.loan) {
      extra = ` ／ 借滿 ${fmt(p.credit_limit)} 時 ${fmt(p.value / p.credit_limit * 100)}%`;
    }
    worstHint.textContent = (p.worst_ratio < p.call_pct ? '會跌破追繳線' : '仍高於追繳線') + extra;
  } else {
    worstEl.textContent = '--';
    worstBar.style.width = '0';
    worstHint.textContent = '--';
  }

  const hEl = document.getElementById('holiday');
  const hBar = document.getElementById('holiday-bar');
  const hHint = document.getElementById('holiday-hint');
  if (d.holiday.t1) {
    const active = d.holiday.status !== 'idle';
    hEl.textContent = d.holiday.t1.slice(5);
    hEl.style.color = active ? 'var(--warn)' : 'inherit';
    hBar.style.width = Math.max(0, Math.min(100, (30 - d.holiday.days_to_t1) / 30 * 100)) + '%';
    hBar.style.background = active ? 'var(--warn)' : 'var(--accent)';
    hHint.textContent = (active ? d.holiday.status + ' 進行中 ／ ' : '')
      + `距 T-1 ${d.holiday.days_to_t1} 天 ／ ${d.holiday.resume ? d.holiday.resume.slice(5) + ' 開市' : ''}`;
  } else {
    hEl.textContent = '--';
    hBar.style.width = '0';
    hHint.textContent = '近期無連假';
  }

  const pledgedSet = new Set(d.positions.filter(x => x.pledged).map(x => x.code));
  const pb = document.getElementById('pledged-body');
  pb.innerHTML = d.positions.filter(x => x.pledged).map(x => `
    <tr><td>${x.code}</td><td class="num">${fmt(x.quantity)}</td>
    <td class="num">${fmt2(x.price)}</td><td class="num">${fmt(x.value)}</td>
    <td class="num ${x.pnl < 0 ? 'down' : 'up'}">${fmt(x.pnl)}</td></tr>`).join('')
    || '<tr><td colspan="5" style="color:var(--muted)">無質押部位</td></tr>';

  const body = document.getElementById('positions-body');
  body.innerHTML = d.positions.map(x => `
    <tr><td>${x.code}</td><td class="num">${fmt(x.quantity)}</td>
    <td class="num">${fmt2(x.price)}</td><td class="num">${fmt(x.value)}</td>
    <td class="num ${x.pnl < 0 ? 'down' : 'up'}">${fmt(x.pnl)}</td>
    <td>${x.pledged ? '<span class="tag">質押</span>' : ''}</td></tr>`).join('')
    + `<tr><td><strong>合計</strong></td><td></td><td></td>
       <td class="num"><strong>${fmt(d.totals.market_value)}</strong></td>
       <td class="num ${d.totals.pnl < 0 ? 'down' : 'up'}"><strong>${fmt(d.totals.pnl)}</strong></td>
       <td></td></tr>`;

  const cal = document.getElementById('calendar-body');
  const nextTag = document.getElementById('next-event-tag');
  if (d.calendar && d.calendar.length) {
    const nearest = d.calendar[0];
    nextTag.textContent = `最近：${nearest.title}（${nearest.days === 0 ? '今天' : nearest.days + ' 天後'}）`;
    const biasMap = d.jev_bias || {};
    cal.innerHTML = d.calendar.map((e, i) => {
      const jb = biasMap[e.date + '|' + e.title];
      let jevCell = '<span style="color:var(--muted)">--</span>';
      if (jb && jb.probabilities) {
        const pct = (jb.probabilities[jb.choice] || 0) * 100;
        const tone = jb.choice === '利多' ? 'var(--up)'
                   : jb.choice === '利空' ? 'var(--down)' : 'var(--muted)';
        jevCell = `<b style="color:${tone}">${jb.choice} ${pct.toFixed(0)}%</b>`;
      }
      const extra = (jb && jb.news && jb.news.length)
        ? `<br><span style="font-size:11.5px;color:var(--muted)">新聞：${jb.news.slice(0, 2).join('｜')}</span>`
        : '';
      return `
      <tr${i === 0 ? ' style="background:var(--bg)"' : ''}>
        <td>${e.date.slice(5)}</td>
        <td class="num">${e.days === 0 ? '今天' : e.days + ' 天'}</td>
        <td>${e.title}</td>
        <td>${jevCell}</td>
        <td style="color:var(--muted);font-size:12.5px">${e.hint}${extra}</td></tr>`;
    }).join('');
  } else {
    nextTag.textContent = '';
    cal.innerHTML = '<tr><td colspan="5" style="color:var(--muted)">近期無事件</td></tr>';
  }

  const warEl = document.getElementById('war');
  warEl.innerHTML = (d.war && d.war.length)
    ? d.war.map(w => `<li>${w.date ? w.date + '　' : ''}${w.title}</li>`).join('')
    : '<li>目前無地緣消息</li>';
}

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    document.getElementById('err').style.display = 'none';
    render(d);
  } catch (e) {
    const box = document.getElementById('err');
    box.textContent = '無法取得資料：' + e.message;
    box.style.display = 'block';
  }
}
refresh();
setInterval(refresh, 60000);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5991, debug=False)
