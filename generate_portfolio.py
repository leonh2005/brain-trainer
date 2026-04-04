#!/usr/bin/env python3
"""抓取五年歷史資料並生成靜態 portfolio-monitor.html"""
import yfinance as yf
import json, os

HOLDINGS = [
    {'yahoo': '006208.TW', 'display': '006208', 'name': '富邦台50',       'amount': 140, 'color': '#c8a96e'},
    {'yahoo': '00881.TW',  'display': '00881',  'name': '國泰永續高股息', 'amount': 140, 'color': '#3dd68c'},
    {'yahoo': 'VT',        'display': 'VWRA',   'name': '先鋒全球（VT）', 'amount': 140, 'color': '#60a5fa'},
    {'yahoo': 'GRID',      'display': 'GRID',   'name': '全球電力基建',   'amount': 105, 'color': '#a78bfa'},
    {'yahoo': 'XLU',       'display': 'XLU',    'name': '美國公用事業',   'amount': 35,  'color': '#fb923c'},
    {'yahoo': '00865B.TW', 'display': '00864B', 'name': '中信美債0-1年',  'amount': 35,  'color': '#94a3b8'},
    {'yahoo': '00635U.TW', 'display': '00635U', 'name': '元大黃金',       'amount': 70,  'color': '#fbbf24'},
]

print("抓取五年歷史資料...")
data = {}
for h in HOLDINGS:
    try:
        import pandas as pd
        df = yf.download(h['yahoo'], period='5y', interval='1mo', auto_adjust=True, progress=False)
        if df.empty:
            print(f"  ⚠ {h['display']}: 無資料")
            continue
        # yfinance 1.2+ MultiIndex columns
        if isinstance(df.columns, pd.MultiIndex):
            closes = df[('Close', h['yahoo'])].dropna()
        else:
            closes = df['Close'].dropna()
        pairs = [{'x': pd.Timestamp(d).strftime('%Y/%m'), 'y': round(float(v), 4)} for d, v in closes.items()]
        # normalize to % return
        base = pairs[0]['y']
        norm = [{'x': p['x'], 'y': round((p['y'] / base - 1) * 100, 2)} for p in pairs]
        total_ret = norm[-1]['y']
        data[h['display']] = {'norm': norm, 'ret': total_ret}
        print(f"  ✓ {h['display']:8s} 五年報酬: {total_ret:+.1f}%  ({len(norm)} 筆)")
    except Exception as e:
        print(f"  ✗ {h['display']}: {e}")

html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Portfolio Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Cormorant:ital,wght@0,300;0,400;0,600;1,300&family=Azeret+Mono:wght@300;400;500&family=Figtree:wght@300;400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg:#080a0e;--surface:#0f1117;--border:#1e2130;
      --gold:#c8a96e;--gold-dim:#8a7049;--green:#3dd68c;--red:#f87171;
      --text:#dde1ec;--text-dim:#5a6078;
    }}
    *{{margin:0;padding:0;box-sizing:border-box}}
    body{{background:var(--bg);color:var(--text);font-family:'Figtree',sans-serif;font-weight:300;min-height:100vh}}
    .container{{max-width:1400px;margin:0 auto;padding:0 32px;position:relative}}
    header{{padding:40px 0 24px;border-bottom:1px solid var(--border);display:flex;align-items:flex-end;justify-content:space-between;animation:fd .7s ease both}}
    .logo h1{{font-family:'Cormorant',serif;font-size:2.8rem;font-weight:300;letter-spacing:.02em;line-height:1}}
    .logo h1 span{{color:var(--gold);font-style:italic}}
    .logo p{{font-family:'Azeret Mono',monospace;font-size:.68rem;color:var(--text-dim);letter-spacing:.15em;text-transform:uppercase;margin-top:6px}}
    .total-v{{font-family:'Azeret Mono',monospace;font-size:2rem;font-weight:500;color:var(--gold);text-align:right}}
    .total-l{{font-family:'Azeret Mono',monospace;font-size:.65rem;color:var(--text-dim);letter-spacing:.15em;text-transform:uppercase;margin-top:4px;text-align:right}}
    .sec-label{{font-family:'Azeret Mono',monospace;font-size:.65rem;color:var(--text-dim);letter-spacing:.2em;text-transform:uppercase;margin-bottom:16px}}
    .alloc-bar{{display:flex;height:6px;border-radius:3px;overflow:hidden;margin-bottom:20px;gap:2px}}
    .alloc-seg{{height:100%;border-radius:2px;transition:opacity .2s;cursor:pointer}}.alloc-seg:hover{{opacity:.7}}
    .alloc-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px}}
    .alloc-card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px;transition:border-color .2s,transform .2s}}
    .alloc-card:hover{{border-color:var(--gold-dim);transform:translateY(-2px)}}
    .a-tick{{font-family:'Azeret Mono',monospace;font-size:.8rem;font-weight:500;margin-bottom:6px}}
    .a-name{{font-size:.7rem;color:var(--text-dim);margin-bottom:10px;line-height:1.3}}
    .a-amt{{font-family:'Azeret Mono',monospace;font-size:1.1rem}}
    .a-pct{{font-family:'Azeret Mono',monospace;font-size:.65rem;color:var(--text-dim);margin-top:2px}}
    .dot{{width:6px;height:6px;border-radius:50%;display:inline-block;margin-right:6px;vertical-align:middle}}
    .divider{{width:100%;height:1px;background:var(--border);margin:0 0 32px}}
    .sc-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
    .sc-card{{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px 24px;display:flex;align-items:center;gap:20px}}
    .sc-icon{{font-size:1.8rem;line-height:1}}
    .sc-label{{font-family:'Azeret Mono',monospace;font-size:.65rem;color:var(--text-dim);letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px}}
    .sc-val{{font-family:'Azeret Mono',monospace;font-size:1.5rem;font-weight:500}}
    .sc-sub{{font-family:'Azeret Mono',monospace;font-size:.7rem;color:var(--text-dim);margin-top:2px}}
    .up{{color:var(--green)}}.down{{color:var(--red)}}.gold{{color:var(--gold)}}
    .chart-tabs{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
    .chart-tab{{font-family:'Azeret Mono',monospace;font-size:.72rem;padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--text-dim);cursor:pointer;transition:all .2s;letter-spacing:.05em}}
    .chart-tab:hover{{color:var(--text)}}
    .chart-box{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:24px 24px 16px}}
    .chart-note{{font-family:'Azeret Mono',monospace;font-size:.62rem;color:var(--text-dim);margin-top:10px;text-align:right}}
    .mini-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:16px}}
    .mini-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;padding:14px 16px 12px;transition:border-color .2s}}
    .mini-card:hover{{border-color:var(--gold-dim)}}
    .mini-hdr{{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}}
    .mini-tick{{font-family:'Azeret Mono',monospace;font-size:.82rem;font-weight:500}}
    .mini-ret{{font-family:'Azeret Mono',monospace;font-size:.78rem;font-weight:500}}
    .mini-alloc{{font-family:'Azeret Mono',monospace;font-size:.62rem;color:var(--text-dim)}}
    footer{{border-top:1px solid var(--border);padding:20px 0;display:flex;align-items:center;justify-content:space-between}}
    .foot-note{{font-family:'Azeret Mono',monospace;font-size:.62rem;color:var(--text-dim)}}
    section{{animation:fd .7s ease both}}
    .s1{{animation-delay:.1s}}.s2{{animation-delay:.2s}}.s3{{animation-delay:.3s}}.s4{{animation-delay:.4s}}
    @keyframes fd{{from{{opacity:0;transform:translateY(-10px)}}to{{opacity:1;transform:translateY(0)}}}}
    ::-webkit-scrollbar{{width:4px}}::-webkit-scrollbar-track{{background:var(--bg)}}::-webkit-scrollbar-thumb{{background:var(--border);border-radius:2px}}
  </style>
</head>
<body>
<div class="container">

<header>
  <div class="logo">
    <h1>Portfolio <span>Monitor</span></h1>
    <p>黃金版 · 穩健成長配置 · 五年績效追蹤</p>
  </div>
  <div>
    <div class="total-v">NT$ 700萬</div>
    <div class="total-l">總投入資產</div>
  </div>
</header>

<section class="s1" style="padding:32px 0">
  <div class="sec-label">持倉配置</div>
  <div class="alloc-bar" id="aBar"></div>
  <div class="alloc-grid" id="aGrid"></div>
</section>

<div class="divider"></div>

<section class="s2" style="padding:0 0 32px">
  <div class="sec-label">情境模擬</div>
  <div class="sc-grid">
    <div class="sc-card"><div class="sc-icon">📈</div><div>
      <div class="sc-label">大漲（006208 +35%）</div>
      <div class="sc-val up">+24.0%</div>
      <div class="sc-sub up">+168萬獲利</div>
    </div></div>
    <div class="sc-card"><div class="sc-icon">📉</div><div>
      <div class="sc-label">大跌（大盤 -20%）</div>
      <div class="sc-val down">-15.9%</div>
      <div class="sc-sub down">-111萬縮水</div>
    </div></div>
    <div class="sc-card"><div class="sc-icon">⚖️</div><div>
      <div class="sc-label">賺賠比</div>
      <div class="sc-val gold">1.51</div>
      <div class="sc-sub">穩健成長型最佳化</div>
    </div></div>
  </div>
</section>

<div class="divider"></div>

<section class="s3" style="padding:0 0 32px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
    <div class="sec-label" style="margin:0">五年績效比較（% 報酬）</div>
    <div style="font-family:'Azeret Mono',monospace;font-size:.65rem;color:var(--text-dim)">Yahoo Finance · 月線 · 已調整股息</div>
  </div>
  <div class="chart-tabs" id="tabs"></div>
  <div class="chart-box"><canvas id="mainChart" height="380"></canvas></div>
  <div class="chart-note">⚠ VT / GRID / XLU 為美元計價，與台幣標的含匯率影響，僅供趨勢參考</div>
</section>

<section class="s4" style="padding:0 0 40px">
  <div class="sec-label">個別標的走勢（五年）</div>
  <div class="mini-grid" id="mGrid"></div>
</section>

<footer>
  <div class="foot-note">資料來源：Yahoo Finance · 本頁僅供參考，不構成投資建議</div>
  <div class="foot-note" id="ts"></div>
</footer>
</div>

<script>
const HOLDINGS = {json.dumps(HOLDINGS, ensure_ascii=False)};
const DATA = {json.dumps(data, ensure_ascii=False)};
const TOTAL = 700;

// ── Allocation ──
const aBar = document.getElementById('aBar');
const aGrid = document.getElementById('aGrid');
HOLDINGS.forEach(h => {{
  const seg = document.createElement('div');
  seg.className = 'alloc-seg';
  seg.style.cssText = `width:${{(h.amount/TOTAL*100).toFixed(1)}}%;background:${{h.color}}`;
  seg.title = `${{h.display}} ${{(h.amount/TOTAL*100).toFixed(1)}}%`;
  aBar.appendChild(seg);

  const card = document.createElement('div');
  card.className = 'alloc-card';
  card.innerHTML = `
    <div class="a-tick"><span class="dot" style="background:${{h.color}}"></span>${{h.display}}</div>
    <div class="a-name">${{h.name}}</div>
    <div class="a-amt">NT$${{h.amount}}萬</div>
    <div class="a-pct">${{(h.amount/TOTAL*100).toFixed(1)}}% 佔比</div>`;
  aGrid.appendChild(card);
}});

// ── Main Chart ──
const ctx = document.getElementById('mainChart').getContext('2d');
const visible = new Set(HOLDINGS.map(h => h.display));

const datasets = HOLDINGS.map(h => {{
  const d = DATA[h.display];
  return {{
    label: h.display,
    data: d ? d.norm : [],
    borderColor: h.color,
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
    pointHoverRadius: 4,
    tension: 0.3,
  }};
}});

// Collect all x labels from first available dataset
const allLabels = datasets.find(ds => ds.data.length)?.data.map(p => p.x) || [];
const chartData = datasets.map(ds => ({{ ...ds, data: ds.data.map(p => p.y) }}));

const mainChart = new Chart(ctx, {{
  type: 'line',
  data: {{ labels: allLabels, datasets: chartData }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ labels: {{ color:'#8892aa', font:{{family:'Azeret Mono',size:11}}, boxWidth:12, padding:16 }} }},
      tooltip: {{
        backgroundColor:'#161820', borderColor:'#1e2130', borderWidth:1,
        titleColor:'#8892aa', bodyColor:'#dde1ec',
        titleFont:{{family:'Azeret Mono',size:10}}, bodyFont:{{family:'Azeret Mono',size:11}},
        callbacks: {{ label: c => ` ${{c.dataset.label}}  ${{c.parsed.y>=0?'+':''}}${{c.parsed.y.toFixed(1)}}%` }}
      }}
    }},
    scales: {{
      x: {{
        grid: {{ color:'#1e2130' }},
        ticks: {{ color:'#5a6078', font:{{family:'Azeret Mono',size:10}}, maxTicksLimit:12,
          callback: (val,i) => i%6===0 ? allLabels[i] : '' }}
      }},
      y: {{
        grid: {{ color:'#1e2130' }},
        ticks: {{ color:'#5a6078', font:{{family:'Azeret Mono',size:10}},
          callback: v => (v>=0?'+':'')+v+'%' }}
      }}
    }}
  }}
}});

// ── Tabs ──
const tabsEl = document.getElementById('tabs');
const allBtn = document.createElement('button');
allBtn.className = 'chart-tab';
allBtn.textContent = '全部';
allBtn.style.cssText = 'border-color:var(--gold);color:var(--gold);background:rgba(200,169,110,.1)';
allBtn.onclick = () => {{
  HOLDINGS.forEach(h => visible.add(h.display));
  mainChart.data.datasets.forEach(ds => ds.hidden = false);
  mainChart.update();
  document.querySelectorAll('.chart-tab[data-d]').forEach(t => {{
    const h = HOLDINGS.find(x => x.display===t.dataset.d);
    if(h) {{ t.style.background=h.color+'18'; t.style.color=h.color; t.style.borderColor=h.color; }}
  }});
}};
tabsEl.appendChild(allBtn);

HOLDINGS.forEach(h => {{
  const btn = document.createElement('button');
  btn.className = 'chart-tab';
  btn.dataset.d = h.display;
  btn.style.cssText = `border-color:${{h.color}};color:${{h.color}};background:${{h.color}}18`;
  btn.innerHTML = `<span class="dot" style="background:${{h.color}}"></span>${{h.display}}`;
  const d = DATA[h.display];
  if (d) btn.title = `五年: ${{d.ret>=0?'+':''}}${{d.ret.toFixed(1)}}%`;
  btn.onclick = () => {{
    if(visible.has(h.display)) {{
      visible.delete(h.display);
      btn.style.background='var(--surface)';btn.style.color='var(--text-dim)';btn.style.borderColor='var(--border)';
    }} else {{
      visible.add(h.display);
      btn.style.background=h.color+'18';btn.style.color=h.color;btn.style.borderColor=h.color;
    }}
    mainChart.data.datasets.forEach(ds => {{ ds.hidden = !visible.has(ds.label); }});
    mainChart.update();
  }};
  tabsEl.appendChild(btn);
}});

// ── Mini Charts ──
const mGrid = document.getElementById('mGrid');
HOLDINGS.forEach(h => {{
  const d = DATA[h.display];
  const ret = d ? `${{d.ret>=0?'+':''}}${{d.ret.toFixed(1)}}%` : 'N/A';
  const retColor = d && d.ret>=0 ? 'var(--green)' : 'var(--red)';
  const cid = 'mc-' + h.display.replace(/[^a-z0-9]/gi,'');
  const card = document.createElement('div');
  card.className = 'mini-card';
  card.innerHTML = `
    <div class="mini-hdr">
      <div class="mini-tick"><span class="dot" style="background:${{h.color}}"></span>${{h.display}}</div>
      <div style="text-align:right">
        <div class="mini-ret" style="color:${{retColor}}">${{ret}}</div>
        <div class="mini-alloc">NT$${{h.amount}}萬</div>
      </div>
    </div>
    <div style="height:110px"><canvas id="${{cid}}"></canvas></div>`;
  mGrid.appendChild(card);

  if (!d) return;
  const mCtx = document.getElementById(cid).getContext('2d');
  new Chart(mCtx, {{
    type:'line',
    data: {{ labels: d.norm.map(p=>p.x), datasets: [{{
      data: d.norm.map(p=>p.y),
      borderColor: h.color,
      backgroundColor: h.color+'22',
      fill: true, borderWidth:1.5, pointRadius:0, tension:0.3
    }}] }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      plugins:{{legend:{{display:false}},tooltip:{{enabled:false}}}},
      scales:{{ x:{{display:false}}, y:{{display:false}} }}
    }}
  }});
}});

document.getElementById('ts').textContent = '更新：' + new Date().toLocaleString('zh-TW',{{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}});
</script>
</body>
</html>"""

out = '/Users/steven/CCProject/portfolio-monitor.html'
with open(out, 'w', encoding='utf-8') as f:
    f.write(html)
print(f"\n✓ 已生成 {out}")
