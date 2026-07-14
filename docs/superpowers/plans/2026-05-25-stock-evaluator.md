# Stock Evaluator 單股評估 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 擴充 `stock_analyzer` 讓使用者輸入一支股票代碼後，同時看到技術面（量價/均線/評分）、基本面（DCF/EPS/ROE/PE）、AI 大師分析三個區塊。

**Architecture:** 在現有 `app.py` 新增兩條路由（`/api/fundamentals`、`/api/ai-analysis`），各自獨立呼叫 yfinance 與 DeepSeek API。`templates/index.html` 改成三個 Tab，技術面資料已有，其他兩個 Tab 在使用者點擊時才非同步載入。

**Tech Stack:** Python 3.14、Flask、yfinance、openai SDK（呼叫 DeepSeek）、原生 JS fetch（無 framework）

---

## 檔案清單

| 動作 | 路徑 | 負責 |
|------|------|------|
| Modify | `stock_analyzer/app.py` | 新增 `/api/fundamentals` 與 `/api/ai-analysis` 路由 |
| Modify | `stock_analyzer/templates/index.html` | 加入基本面與 AI 分析 Tab UI |

---

### Task 1：安裝 openai 套件

**Files:**
- Modify: `stock_analyzer/venv/`（執行 pip install）

- [ ] **Step 1：安裝 openai**

```bash
/Users/steven/CCProject/stock_analyzer/venv/bin/pip install openai
```

預期輸出包含 `Successfully installed openai-...`

- [ ] **Step 2：驗證 import**

```bash
/Users/steven/CCProject/stock_analyzer/venv/bin/python -c "from openai import OpenAI; print('ok')"
```

預期：`ok`

---

### Task 2：新增 `/api/fundamentals` 路由

基本面資料：EPS、成長率、ROE、本益比、DCF 估值、高/低估判斷。

**Files:**
- Modify: `stock_analyzer/app.py`

- [ ] **Step 1：在 `app.py` import 區補上 openai**

在 `import requests` 之後加入：

```python
from openai import OpenAI
```

- [ ] **Step 2：加入 DCF 計算函式**

在 `analyze()` 函式之前加入：

```python
DEEPSEEK_API_KEY = "<REDACTED>"
DISCOUNT_RATE = 0.10
TERMINAL_GROWTH = 0.03


def fetch_fundamentals(code: str) -> dict:
    ticker = code + ".TW"
    tk = yf.Ticker(ticker)
    info = tk.info
    if not info.get("currentPrice"):
        ticker = code + ".TWO"
        tk = yf.Ticker(ticker)
        info = tk.info

    eps = info.get("trailingEps") or info.get("epsTrailingTwelveMonths") or 0
    growth = info.get("earningsGrowth") or info.get("revenueGrowth") or 0.05
    growth = max(min(float(growth), 0.30), -0.05)
    price = info.get("currentPrice") or info.get("regularMarketPrice") or 0
    pe = info.get("trailingPE") or info.get("forwardPE") or 0
    roe = info.get("returnOnEquity") or 0
    name = info.get("longName") or info.get("shortName") or code
    sector = info.get("sector") or "—"
    market_cap = info.get("marketCap") or 0

    # 兩段式 DCF
    dcf = 0.0
    if eps > 0:
        current_eps = eps
        pv = 0.0
        high_g = growth
        low_g = (growth + TERMINAL_GROWTH) / 2
        for i in range(1, 11):
            g = high_g if i <= 5 else low_g
            current_eps *= (1 + g)
            pv += current_eps / (1 + DISCOUNT_RATE) ** i
        terminal_eps = current_eps * (1 + TERMINAL_GROWTH)
        terminal_value = terminal_eps / (DISCOUNT_RATE - TERMINAL_GROWTH)
        pv += terminal_value / (1 + DISCOUNT_RATE) ** 10
        dcf = round(pv, 2)

    upside = None
    verdict = "資料不足"
    if dcf > 0 and price > 0:
        upside = round((dcf - price) / price * 100, 1)
        verdict = "低估 ▲" if upside > 20 else ("高估 ▼" if upside < -20 else "合理區間")

    return {
        "name": name,
        "sector": sector,
        "market_cap": market_cap,
        "eps": round(float(eps), 2),
        "growth": round(growth * 100, 1),
        "pe": round(float(pe), 1) if pe else None,
        "roe": round(float(roe) * 100, 1) if roe else None,
        "price": round(float(price), 2),
        "dcf": dcf,
        "upside": upside,
        "verdict": verdict,
    }
```

- [ ] **Step 3：加入 `/api/fundamentals` 路由**

在 `@app.route("/api/analyze")` 之後加入：

```python
@app.route("/api/fundamentals")
def api_fundamentals():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        code = resolve_symbol(symbol)
        result = fetch_fundamentals(code)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 4：驗證路由可呼叫（不需啟動服務，只測函式）**

```bash
cd /Users/steven/CCProject/stock_analyzer && \
venv/bin/python -c "
from app import fetch_fundamentals
d = fetch_fundamentals('2330')
print(d['name'], d['dcf'], d['verdict'])
"
```

預期：印出台積電名稱、DCF 數字、verdict（低估/高估/合理區間）

---

### Task 3：新增 `/api/ai-analysis` 路由

使用 DeepSeek V3 分析這支股票的技術面 + 基本面，給出買/觀察/避開建議。

**Files:**
- Modify: `stock_analyzer/app.py`

- [ ] **Step 1：加入 `/api/ai-analysis` 路由**

在 `if __name__ == "__main__":` 之前加入：

```python
@app.route("/api/ai-analysis")
def api_ai_analysis():
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "請輸入股票代碼"}), 400
    try:
        code = resolve_symbol(symbol)
        tech = analyze(code)
        fund = fetch_fundamentals(code)

        prompt = f"""你是台股專業分析師，請針對以下個股給出簡潔的買賣建議（繁體中文，200字以內）：

股票：{fund['name']}（{code}）
產業：{fund['sector']}

【技術面】
- 收盤：{tech['price']} | 漲跌：{tech['change_pct']}%
- 量價型態：{tech['pattern']}（{tech['pattern_desc']}）
- 均線：{tech['ma_status']}
- 技術評分：{tech['pattern_score']}/100

【基本面】
- EPS：{fund['eps']} | 預估成長率：{fund['growth']}%
- 本益比：{fund['pe']} | ROE：{fund['roe']}%
- DCF 估值：{fund['dcf']} | 現價：{fund['price']} → {fund['verdict']}（溢/折價 {fund['upside']}%）

請給出：1) 整體評估（買入/觀察/避開）2) 主要理由（技術+基本面各一點）3) 注意風險"""

        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
        resp = client.chat.completions.create(
            model="deepseek-chat",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return jsonify({"analysis": resp.choices[0].message.content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 2：驗證 DeepSeek 連線**

```bash
cd /Users/steven/CCProject/stock_analyzer && \
venv/bin/python -c "
from openai import OpenAI
client = OpenAI(api_key='<REDACTED>', base_url='https://api.deepseek.com')
r = client.chat.completions.create(model='deepseek-chat', max_tokens=20, messages=[{'role':'user','content':'hi'}])
print(r.choices[0].message.content)
"
```

預期：DeepSeek 回傳任意短回應，不報錯。

---

### Task 4：更新 HTML Template（三個 Tab）

**Files:**
- Modify: `stock_analyzer/templates/index.html`

- [ ] **Step 1：將 `index.html` 改為三 Tab 版本**

將 `templates/index.html` 完整替換為以下內容：

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>台股個股評估</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Noto Sans TC", sans-serif; background: #0f1117; color: #e0e0e0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; padding: 40px 20px; }
  h1 { font-size: 1.6rem; margin-bottom: 30px; color: #fff; letter-spacing: 2px; }
  .search-box { display: flex; gap: 10px; margin-bottom: 30px; }
  input { background: #1e2130; border: 1px solid #333; border-radius: 8px; color: #fff; font-size: 1.2rem; padding: 12px 20px; width: 200px; outline: none; text-align: center; letter-spacing: 3px; }
  input:focus { border-color: #4a9eff; }
  button.search-btn { background: #4a9eff; border: none; border-radius: 8px; color: #fff; cursor: pointer; font-size: 1rem; padding: 12px 24px; transition: background 0.2s; }
  button.search-btn:hover { background: #2d7dd2; }
  .container { width: 100%; max-width: 560px; display: none; }
  .stock-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
  .stock-name { font-size: 1rem; color: #888; }
  .stock-symbol { font-size: 1.6rem; font-weight: bold; color: #fff; }
  .price-row { display: flex; align-items: baseline; gap: 12px; margin-bottom: 20px; }
  .price { font-size: 2.4rem; font-weight: bold; }
  .change-up { color: #ff4d4d; }
  .change-down { color: #4dff88; }
  .change-flat { color: #aaa; }
  .tabs { display: flex; border-bottom: 1px solid #333; margin-bottom: 20px; }
  .tab { flex: 1; text-align: center; padding: 10px; cursor: pointer; color: #888; font-size: 0.95rem; border-bottom: 2px solid transparent; transition: all 0.2s; }
  .tab.active { color: #4a9eff; border-bottom-color: #4a9eff; }
  .tab-content { display: none; }
  .tab-content.active { display: block; }
  .card { background: #1e2130; border-radius: 16px; padding: 24px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0; }
  .stat { background: #262a3d; border-radius: 10px; padding: 12px 16px; }
  .stat-label { color: #888; font-size: 0.8rem; margin-bottom: 4px; }
  .stat-value { font-size: 1.05rem; font-weight: bold; }
  .divider { border: none; border-top: 1px solid #333; margin: 16px 0; }
  .pattern { font-size: 1.05rem; font-weight: bold; margin-bottom: 6px; }
  .pattern-desc { color: #aaa; font-size: 0.88rem; margin-bottom: 14px; }
  .score-bar-bg { background: #262a3d; border-radius: 999px; height: 12px; overflow: hidden; }
  .score-bar { height: 100%; border-radius: 999px; transition: width 0.6s ease; }
  .score-label { display: flex; justify-content: space-between; margin-top: 6px; font-size: 0.82rem; color: #aaa; }
  .verdict-badge { display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 0.9rem; font-weight: bold; margin-top: 4px; }
  .verdict-low { background: rgba(255,77,77,0.15); color: #ff4d4d; }
  .verdict-high { background: rgba(77,255,136,0.15); color: #4dff88; }
  .verdict-fair { background: rgba(240,192,64,0.15); color: #f0c040; }
  .verdict-na { background: rgba(150,150,150,0.15); color: #aaa; }
  .ai-box { white-space: pre-wrap; line-height: 1.7; font-size: 0.92rem; color: #ddd; }
  .loading-inline { color: #888; font-size: 0.9rem; text-align: center; padding: 20px; }
  .error-msg { color: #ff6b6b; font-size: 0.9rem; }
  .loading-overlay { text-align: center; color: #888; padding: 30px; display: none; }
</style>
</head>
<body>
<h1>📊 台股個股評估</h1>
<div class="search-box">
  <input type="text" id="symbolInput" placeholder="2330" maxlength="6" />
  <button class="search-btn" onclick="runAll()">分析</button>
</div>
<div class="loading-overlay" id="loading">載入中...</div>
<div class="container" id="container">
  <div class="stock-header">
    <div>
      <div class="stock-name" id="stockName">—</div>
      <div class="stock-symbol" id="stockSymbol">—</div>
    </div>
    <div class="price-row">
      <span class="price" id="price">—</span>
      <span id="changeBadge">—</span>
    </div>
  </div>
  <div class="tabs">
    <div class="tab active" onclick="switchTab('tech')">📈 技術面</div>
    <div class="tab" onclick="switchTab('fund')">📋 基本面</div>
    <div class="tab" onclick="switchTab('ai')">🤖 AI 分析</div>
  </div>

  <!-- 技術面 -->
  <div class="tab-content active" id="tab-tech">
    <div class="card">
      <div class="grid">
        <div class="stat"><div class="stat-label">開盤</div><div class="stat-value" id="open">—</div></div>
        <div class="stat"><div class="stat-label">最高</div><div class="stat-value" id="high">—</div></div>
        <div class="stat"><div class="stat-label">最低</div><div class="stat-value" id="low">—</div></div>
        <div class="stat"><div class="stat-label">成交量 / 5日均量</div><div class="stat-value" id="volume">—</div></div>
        <div class="stat"><div class="stat-label">MA5</div><div class="stat-value" id="ma5">—</div></div>
        <div class="stat"><div class="stat-label">MA20</div><div class="stat-value" id="ma20">—</div></div>
      </div>
      <hr class="divider">
      <div class="pattern" id="pattern">—</div>
      <div class="pattern-desc" id="patternDesc">—</div>
      <div class="score-bar-bg">
        <div class="score-bar" id="scoreBar" style="width:0%"></div>
      </div>
      <div class="score-label">
        <span>技術評分</span>
        <span id="scoreLabel">—</span>
      </div>
    </div>
  </div>

  <!-- 基本面 -->
  <div class="tab-content" id="tab-fund">
    <div class="card" id="fundCard">
      <div class="loading-inline">載入基本面資料中...</div>
    </div>
  </div>

  <!-- AI 分析 -->
  <div class="tab-content" id="tab-ai">
    <div class="card" id="aiCard">
      <div class="loading-inline">等待 AI 分析中（約 5–10 秒）...</div>
    </div>
  </div>
</div>

<script>
const input = document.getElementById("symbolInput");
input.addEventListener("keydown", e => { if (e.key === "Enter") runAll(); });

let currentTab = 'tech';

function switchTab(name) {
  currentTab = name;
  document.querySelectorAll('.tab').forEach((t, i) => {
    t.classList.toggle('active', ['tech','fund','ai'][i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
}

async function runAll() {
  const symbol = input.value.trim();
  if (!symbol) return;

  document.getElementById("loading").style.display = "block";
  document.getElementById("container").style.display = "none";

  // 重設基本面與 AI 面板
  document.getElementById("fundCard").innerHTML = '<div class="loading-inline">載入基本面資料中...</div>';
  document.getElementById("aiCard").innerHTML = '<div class="loading-inline">等待 AI 分析中（約 5–10 秒）...</div>';

  try {
    const techRes = await fetch(`/api/analyze?symbol=${encodeURIComponent(symbol)}`);
    const tech = await techRes.json();
    document.getElementById("loading").style.display = "none";

    if (tech.error) {
      document.getElementById("container").style.display = "block";
      document.getElementById("tab-tech").querySelector('.card').innerHTML = `<div class="error-msg">❌ ${tech.error}</div>`;
      return;
    }

    renderTech(tech);
    document.getElementById("container").style.display = "block";
    switchTab('tech');

    // 基本面與 AI 非同步並行
    fetchFundamentals(symbol);
    fetchAI(symbol);

  } catch(e) {
    document.getElementById("loading").style.display = "none";
    alert("錯誤：" + e.message);
  }
}

function renderTech(d) {
  document.getElementById("stockSymbol").textContent = d.symbol;
  document.getElementById("price").textContent = d.price.toFixed(2);
  const cls = d.change > 0 ? "change-up" : d.change < 0 ? "change-down" : "change-flat";
  const sign = d.change > 0 ? "▲" : d.change < 0 ? "▼" : "—";
  const badge = document.getElementById("changeBadge");
  badge.className = cls;
  badge.textContent = `${sign} ${Math.abs(d.change).toFixed(2)} (${d.change_pct}%)`;
  document.getElementById("price").className = "price " + cls;
  document.getElementById("open").textContent = d.open.toFixed(2);
  document.getElementById("high").textContent = d.high.toFixed(2);
  document.getElementById("low").textContent = d.low.toFixed(2);
  document.getElementById("volume").textContent = `${Math.round(d.volume/1000)}張 / ${Math.round(d.vol_5/1000)}張`;
  document.getElementById("ma5").textContent = d.ma5;
  document.getElementById("ma20").textContent = d.ma20;
  document.getElementById("pattern").textContent = d.pattern;
  document.getElementById("patternDesc").textContent = d.pattern_desc;
  const score = d.pattern_score;
  const barColor = score >= 70 ? "#ff4d4d" : score >= 50 ? "#f0c040" : "#4dff88";
  document.getElementById("scoreBar").style.width = score + "%";
  document.getElementById("scoreBar").style.background = barColor;
  document.getElementById("scoreLabel").textContent = score + "/100";
}

async function fetchFundamentals(symbol) {
  try {
    const res = await fetch(`/api/fundamentals?symbol=${encodeURIComponent(symbol)}`);
    const d = await res.json();
    if (d.error) { document.getElementById("fundCard").innerHTML = `<div class="error-msg">❌ ${d.error}</div>`; return; }

    document.getElementById("stockName").textContent = d.name;

    const verdictClass = d.verdict.includes("低估") ? "verdict-low" : d.verdict.includes("高估") ? "verdict-high" : d.verdict === "合理區間" ? "verdict-fair" : "verdict-na";
    const upsideText = d.upside !== null ? `${d.upside > 0 ? "+" : ""}${d.upside}%` : "—";

    document.getElementById("fundCard").innerHTML = `
      <div class="grid">
        <div class="stat"><div class="stat-label">產業</div><div class="stat-value" style="font-size:0.95rem">${d.sector}</div></div>
        <div class="stat"><div class="stat-label">本益比 (PE)</div><div class="stat-value">${d.pe ?? "—"}</div></div>
        <div class="stat"><div class="stat-label">EPS (TTM)</div><div class="stat-value">${d.eps}</div></div>
        <div class="stat"><div class="stat-label">ROE</div><div class="stat-value">${d.roe !== null ? d.roe + "%" : "—"}</div></div>
        <div class="stat"><div class="stat-label">預估成長率</div><div class="stat-value">${d.growth}%</div></div>
        <div class="stat"><div class="stat-label">市值</div><div class="stat-value" style="font-size:0.9rem">${d.market_cap ? (d.market_cap/1e8).toFixed(0) + "億" : "—"}</div></div>
      </div>
      <hr class="divider">
      <div class="stat-label" style="margin-bottom:8px">DCF 估值（10年兩段式）</div>
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div>
          <div style="font-size:1.6rem;font-weight:bold">${d.dcf > 0 ? d.dcf : "—"}</div>
          <div style="color:#888;font-size:0.82rem">現價 ${d.price} | 差異 ${upsideText}</div>
        </div>
        <span class="verdict-badge ${verdictClass}">${d.verdict}</span>
      </div>`;
  } catch(e) {
    document.getElementById("fundCard").innerHTML = `<div class="error-msg">❌ ${e.message}</div>`;
  }
}

async function fetchAI(symbol) {
  try {
    const res = await fetch(`/api/ai-analysis?symbol=${encodeURIComponent(symbol)}`);
    const d = await res.json();
    if (d.error) { document.getElementById("aiCard").innerHTML = `<div class="error-msg">❌ ${d.error}</div>`; return; }
    document.getElementById("aiCard").innerHTML = `<div class="ai-box">${d.analysis}</div>`;
  } catch(e) {
    document.getElementById("aiCard").innerHTML = `<div class="error-msg">❌ ${e.message}</div>`;
  }
}
</script>
</body>
</html>
```

- [ ] **Step 2：啟動服務確認頁面正常**

```bash
cd /Users/steven/CCProject/stock_analyzer && \
venv/bin/python app.py &
sleep 2
curl -s http://localhost:5100/ | grep -o "<title>.*</title>"
```

預期：`<title>台股個股評估</title>`

- [ ] **Step 3：測試技術面 API**

```bash
curl -s "http://localhost:5100/api/analyze?symbol=2330" | python3 -m json.tool | grep -E "symbol|price|pattern_score"
```

預期：看到 `symbol`, `price`, `pattern_score` 欄位

- [ ] **Step 4：測試基本面 API**

```bash
curl -s "http://localhost:5100/api/fundamentals?symbol=2330" | python3 -m json.tool | grep -E "name|dcf|verdict"
```

預期：看到台積電名稱、DCF 數值、verdict

- [ ] **Step 5：測試 AI 分析 API**

```bash
curl -s "http://localhost:5100/api/ai-analysis?symbol=2330" | python3 -m json.tool | grep -o '"analysis":.*' | head -3
```

預期：看到 DeepSeek 回傳的繁體中文分析文字

- [ ] **Step 6：kill 測試程序**

```bash
pkill -f "python app.py"
```

---

### Task 5：設定 LaunchAgent（可選）

`stock_analyzer` 目前沒有 LaunchAgent，若要讓它開機自動啟動：

- [ ] **Step 1：確認是否需要**

```bash
ls ~/Library/LaunchAgents/ | grep stock
```

若已存在則跳過此 Task。

- [ ] **Step 2：建立 LaunchAgent plist**

```bash
cat > ~/Library/LaunchAgents/com.steven.stock-analyzer.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.steven.stock-analyzer</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/steven/CCProject/stock_analyzer/venv/bin/python</string>
    <string>/Users/steven/CCProject/stock_analyzer/app.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/steven/CCProject/logs/stock-analyzer.log</string>
  <key>StandardErrorPath</key><string>/Users/steven/CCProject/logs/stock-analyzer.err</string>
</dict>
</plist>
EOF
```

- [ ] **Step 3：載入**

```bash
launchctl load ~/Library/LaunchAgents/com.steven.stock-analyzer.plist
sleep 2
curl -s http://localhost:5100/ | grep -o "<title>.*</title>"
```

預期：`<title>台股個股評估</title>`
