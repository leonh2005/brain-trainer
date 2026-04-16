# Stock Screener AI 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個本地 Flask 網頁，整合 FinMind + Shioaji + DeepSeek V3，讓使用者可調整篩選條件、一鍵取得 AI 選股推薦，並保存歷史記錄。

**Architecture:** Flask + SQLite 儲存歷史，FinMind 提供法人/基本面資料，Shioaji 提供即時量價，DeepSeek `deepseek-chat` 模型分析並給出買進/觀望/避開建議。前端純 HTML/JS，無框架依賴。

**Tech Stack:** Python 3.11, Flask, SQLite, FinMind API, Shioaji, DeepSeek API (`deepseek-chat`), Vanilla JS/HTML

---

## 檔案結構

```
~/CCProject/stock-screener-ai/
├── app.py              # Flask 路由與主程式
├── screener.py         # 篩選邏輯（合併各維度資料、套用條件）
├── data_fetcher.py     # FinMind + Shioaji 資料抓取
├── ai_analyzer.py      # DeepSeek API 呼叫
├── database.py         # SQLite CRUD（歷史記錄）
├── config.py           # 預設篩選參數與 API keys
├── requirements.txt
├── templates/
│   └── index.html      # 單頁前端
└── tests/
    ├── test_screener.py
    ├── test_data_fetcher.py
    └── test_ai_analyzer.py
```

---

## Task 1：專案初始化與 Flask 骨架

**Files:**
- Create: `~/CCProject/stock-screener-ai/app.py`
- Create: `~/CCProject/stock-screener-ai/config.py`
- Create: `~/CCProject/stock-screener-ai/requirements.txt`
- Create: `~/CCProject/stock-screener-ai/templates/index.html`

- [ ] **Step 1: 建立目錄與 requirements.txt**

```bash
mkdir -p ~/CCProject/stock-screener-ai/templates ~/CCProject/stock-screener-ai/tests
cd ~/CCProject/stock-screener-ai
cat > requirements.txt << 'EOF'
flask
requests
shioaji
finmind
openai
EOF
pip install -r requirements.txt
```

- [ ] **Step 2: 建立 config.py**

```python
# config.py
import os

DEEPSEEK_API_KEY = "sk-49f9f0a651514aff96412fa7ad11ae85"
FINMIND_TOKEN = open(os.path.expanduser("~/.secrets/finmind_token.txt") if os.path.exists(os.path.expanduser("~/.secrets/finmind_token.txt")) else os.path.expanduser("~/CCProject/.secrets/finmind_token.txt")).read().strip()
SHIOAJI_API_KEY = "hj7FsrPYHW9nNiHrcDB2DLHu6LhH3uYvjpR2NdK23E9"
SHIOAJI_SECRET_KEY = "A8CRXZEvWePQgvdZdmCUjzNWwP4xtLf7AdzYE8Cz3Vig"

PORT = 5400

# 預設篩選條件
DEFAULT_PARAMS = {
    # 技術面
    "min_volume": 3000,        # 最低成交量（張）
    "min_price_change": 1.0,   # 最低漲幅（%）
    "max_price_change": 9.5,   # 最高漲幅（%，避免已漲停）

    # 籌碼面
    "min_foreign_buy": 500,    # 外資買超最低（張）
    "min_trust_buy": 0,        # 投信買超最低（張，0=不限）
    "max_margin_increase": 20, # 融資增加上限（%，太高表示散戶追）

    # 基本面
    "min_revenue_growth": 10,  # 月營收年增率最低（%）
    "max_per": 50,             # 本益比上限

    # 輸出
    "top_n": 20,               # 初篩保留前 N 檔送給 AI
}
```

- [ ] **Step 3: 建立最小 app.py**

```python
# app.py
from flask import Flask, render_template, jsonify, request
from config import PORT

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
```

- [ ] **Step 4: 建立最小 index.html**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>AI 選股系統</title>
  <style>
    body { font-family: system-ui; max-width: 1200px; margin: 0 auto; padding: 20px; background: #0f1117; color: #e0e0e0; }
    h1 { color: #4fc3f7; }
  </style>
</head>
<body>
  <h1>🤖 AI 選股系統</h1>
  <p id="status">載入中...</p>
  <script>
    fetch('/api/health').then(r => r.json()).then(d => {
      document.getElementById('status').textContent = '系統正常';
    });
  </script>
</body>
</html>
```

- [ ] **Step 5: 啟動確認**

```bash
cd ~/CCProject/stock-screener-ai && python app.py &
sleep 2 && curl -s http://localhost:5400/api/health
```

預期輸出：`{"status": "ok"}`

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject/stock-screener-ai
git init && git add -A
git commit -m "feat: stock-screener-ai 初始化"
```

---

## Task 2：SQLite 歷史記錄

**Files:**
- Create: `~/CCProject/stock-screener-ai/database.py`
- Create: `~/CCProject/stock-screener-ai/tests/test_database.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_database.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tempfile, database

def test_save_and_list():
    db = database.DB(":memory:")
    db.save_query(
        params={"min_volume": 3000},
        results=[{"code": "2330", "name": "台積電", "recommendation": "買進"}]
    )
    history = db.list_history(limit=10)
    assert len(history) == 1
    assert history[0]["results"][0]["code"] == "2330"

def test_empty_history():
    db = database.DB(":memory:")
    assert db.list_history(limit=10) == []
```

- [ ] **Step 2: 確認測試失敗**

```bash
cd ~/CCProject/stock-screener-ai && python -m pytest tests/test_database.py -v
```

預期：`ModuleNotFoundError: No module named 'database'`

- [ ] **Step 3: 實作 database.py**

```python
# database.py
import sqlite3, json
from datetime import datetime

class DB:
    def __init__(self, path="stock_screener.db"):
        self.path = path
        self._init()

    def _init(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    params TEXT NOT NULL,
                    results TEXT NOT NULL,
                    summary TEXT
                )
            """)

    def save_query(self, params: dict, results: list, summary: str = ""):
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                "INSERT INTO queries (created_at, params, results, summary) VALUES (?, ?, ?, ?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 json.dumps(params, ensure_ascii=False),
                 json.dumps(results, ensure_ascii=False),
                 summary)
            )

    def list_history(self, limit=20):
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id, created_at, params, results, summary FROM queries ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [{"id": r[0], "created_at": r[1],
                 "params": json.loads(r[2]), "results": json.loads(r[3]),
                 "summary": r[4]} for r in rows]
```

- [ ] **Step 4: 確認測試通過**

```bash
cd ~/CCProject/stock-screener-ai && python -m pytest tests/test_database.py -v
```

預期：`2 passed`

- [ ] **Step 5: Commit**

```bash
git add database.py tests/test_database.py && git commit -m "feat: SQLite 歷史記錄"
```

---

## Task 3：FinMind 資料抓取（法人 + 基本面）

**Files:**
- Create: `~/CCProject/stock-screener-ai/data_fetcher.py`（FinMind 部分）
- Create: `~/CCProject/stock-screener-ai/tests/test_data_fetcher.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_data_fetcher.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import patch, MagicMock
import data_fetcher

def test_get_institutional_buy_returns_dict():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": 200,
        "data": [
            {"stock_id": "2330", "Foreign_Investor_Buy_Sell": 5000,
             "Investment_Trust_Buy_Sell": 200, "date": "2026-04-16"}
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        result = data_fetcher.get_institutional_data("2026-04-16")
    assert "2330" in result
    assert result["2330"]["foreign_buy"] == 5000

def test_get_revenue_growth_returns_dict():
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": 200,
        "data": [
            {"stock_id": "2330", "revenue": 250000000000,
             "revenue_year": 2026, "revenue_month": 3}
        ]
    }
    with patch("requests.get", return_value=mock_resp):
        result = data_fetcher.get_revenue_data()
    assert "2330" in result
```

- [ ] **Step 2: 確認測試失敗**

```bash
python -m pytest tests/test_data_fetcher.py -v
```

- [ ] **Step 3: 實作 data_fetcher.py（FinMind 部分）**

```python
# data_fetcher.py
import requests
from datetime import datetime, timedelta
from config import FINMIND_TOKEN

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"

def get_institutional_data(date: str) -> dict:
    """回傳 {stock_id: {foreign_buy, trust_buy, date}}"""
    resp = requests.get(FINMIND_URL, params={
        "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
        "data_id": "",
        "start_date": date,
        "end_date": date,
        "token": FINMIND_TOKEN,
    }, timeout=30)
    data = resp.json().get("data", [])
    result = {}
    for row in data:
        sid = row.get("stock_id", "")
        if not sid or len(sid) != 4:
            continue
        result[sid] = {
            "foreign_buy": row.get("Foreign_Investor_Buy_Sell", 0),
            "trust_buy": row.get("Investment_Trust_Buy_Sell", 0),
            "date": row.get("date", date),
        }
    return result

def get_revenue_data() -> dict:
    """回傳 {stock_id: {revenue, yoy_growth}} 近兩個月計算年增率"""
    today = datetime.today()
    start = (today - timedelta(days=90)).strftime("%Y-%m-%d")
    resp = requests.get(FINMIND_URL, params={
        "dataset": "TaiwanStockMonthRevenue",
        "data_id": "",
        "start_date": start,
        "end_date": today.strftime("%Y-%m-%d"),
        "token": FINMIND_TOKEN,
    }, timeout=30)
    rows = resp.json().get("data", [])
    # 整理：取最近兩筆同月份去年比較
    by_stock = {}
    for row in rows:
        sid = row.get("stock_id", "")
        if not sid or len(sid) != 4:
            continue
        by_stock.setdefault(sid, []).append(row)
    result = {}
    for sid, entries in by_stock.items():
        entries.sort(key=lambda x: (x.get("revenue_year", 0), x.get("revenue_month", 0)))
        if len(entries) >= 2:
            latest = entries[-1]
            prev = entries[-2]
            rev_now = latest.get("revenue", 0) or 0
            rev_prev = prev.get("revenue", 1) or 1
            yoy = round((rev_now - rev_prev) / rev_prev * 100, 1)
            result[sid] = {"revenue": rev_now, "yoy_growth": yoy}
    return result
```

- [ ] **Step 4: 確認測試通過**

```bash
python -m pytest tests/test_data_fetcher.py -v
```

預期：`2 passed`

- [ ] **Step 5: Commit**

```bash
git add data_fetcher.py tests/test_data_fetcher.py && git commit -m "feat: FinMind 法人/營收資料抓取"
```

---

## Task 4：Shioaji 即時量價抓取

**Files:**
- Modify: `~/CCProject/stock-screener-ai/data_fetcher.py`（新增 Shioaji 函數）

- [ ] **Step 1: 寫失敗測試（加到 test_data_fetcher.py）**

```python
def test_get_realtime_quotes_returns_dict():
    mock_api = MagicMock()
    mock_snap = MagicMock()
    mock_snap.close = 580.0
    mock_snap.volume = 45000
    mock_snap.change_rate = 2.5
    mock_snap.open = 568.0
    mock_api.snapshots.return_value = [mock_snap]

    with patch("shioaji.Shioaji", return_value=mock_api):
        with patch("data_fetcher._get_sj_api", return_value=mock_api):
            result = data_fetcher.get_realtime_quotes(["2330"])
    assert "2330" in result
    assert result["2330"]["close"] == 580.0
```

- [ ] **Step 2: 實作（附加到 data_fetcher.py）**

```python
# 加到 data_fetcher.py 底部
import shioaji as sj
from config import SHIOAJI_API_KEY, SHIOAJI_SECRET_KEY

_sj_api = None

def _get_sj_api():
    global _sj_api
    if _sj_api is None:
        api = sj.Shioaji(simulation=False)
        api.login(SHIOAJI_API_KEY, SHIOAJI_SECRET_KEY, fetch_contract=False)
        _sj_api = api
    return _sj_api

def get_realtime_quotes(stock_ids: list) -> dict:
    """回傳 {stock_id: {close, volume, change_rate, open}}"""
    api = _get_sj_api()
    contracts = [api.Contracts.Stocks[sid] for sid in stock_ids
                 if sid in api.Contracts.Stocks]
    if not contracts:
        return {}
    snapshots = api.snapshots(contracts)
    result = {}
    for sid, snap in zip(stock_ids, snapshots):
        result[sid] = {
            "close": snap.close,
            "volume": snap.volume // 1000,  # 換算成張
            "change_rate": snap.change_rate,
            "open": snap.open,
        }
    return result
```

- [ ] **Step 3: 確認所有測試仍通過**

```bash
python -m pytest tests/test_data_fetcher.py -v
```

- [ ] **Step 4: Commit**

```bash
git add data_fetcher.py && git commit -m "feat: Shioaji 即時量價抓取"
```

---

## Task 5：篩選邏輯

**Files:**
- Create: `~/CCProject/stock-screener-ai/screener.py`
- Create: `~/CCProject/stock-screener-ai/tests/test_screener.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_screener.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import screener
from config import DEFAULT_PARAMS

MOCK_QUOTES = {
    "2330": {"close": 580, "volume": 50000, "change_rate": 2.5, "open": 568},
    "2317": {"close": 95, "volume": 500,   "change_rate": 0.5, "open": 94},   # 量太少
    "2454": {"close": 720, "volume": 8000, "change_rate": 1.5, "open": 710},
}
MOCK_INST = {
    "2330": {"foreign_buy": 3000, "trust_buy": 100},
    "2317": {"foreign_buy": -500, "trust_buy": -50},   # 外資賣超
    "2454": {"foreign_buy": 1200, "trust_buy": 50},
}
MOCK_REVENUE = {
    "2330": {"yoy_growth": 25.0},
    "2317": {"yoy_growth": 5.0},   # 營收成長不足
    "2454": {"yoy_growth": 18.0},
}

def test_filter_removes_low_volume():
    params = {**DEFAULT_PARAMS, "min_volume": 3000}
    result = screener.filter_stocks(MOCK_QUOTES, MOCK_INST, MOCK_REVENUE, params)
    codes = [r["code"] for r in result]
    assert "2317" not in codes  # 量 500 < 3000

def test_filter_keeps_qualifying_stocks():
    params = {**DEFAULT_PARAMS, "min_volume": 3000, "min_foreign_buy": 500}
    result = screener.filter_stocks(MOCK_QUOTES, MOCK_INST, MOCK_REVENUE, params)
    codes = [r["code"] for r in result]
    assert "2330" in codes
    assert "2454" in codes

def test_result_contains_score():
    params = DEFAULT_PARAMS
    result = screener.filter_stocks(MOCK_QUOTES, MOCK_INST, MOCK_REVENUE, params)
    for r in result:
        assert "score" in r
        assert "code" in r
```

- [ ] **Step 2: 確認失敗**

```bash
python -m pytest tests/test_screener.py -v
```

- [ ] **Step 3: 實作 screener.py**

```python
# screener.py

def filter_stocks(quotes: dict, institutional: dict, revenue: dict, params: dict) -> list:
    """
    合併三維度資料，套用篩選條件，回傳候選清單（含評分）。
    回傳格式：[{code, close, volume, change_rate, foreign_buy, trust_buy, yoy_growth, score}, ...]
    """
    results = []
    for code, q in quotes.items():
        # 技術面
        if q.get("volume", 0) < params["min_volume"]:
            continue
        cr = q.get("change_rate", 0)
        if cr < params["min_price_change"] or cr > params["max_price_change"]:
            continue

        # 籌碼面
        inst = institutional.get(code, {})
        foreign = inst.get("foreign_buy", 0)
        trust = inst.get("trust_buy", 0)
        if foreign < params["min_foreign_buy"]:
            continue
        if trust < params["min_trust_buy"]:
            continue

        # 基本面
        rev = revenue.get(code, {})
        yoy = rev.get("yoy_growth", 0)
        if yoy < params["min_revenue_growth"]:
            continue

        # 綜合評分（0~100）
        score = _calc_score(q, inst, rev, params)

        results.append({
            "code": code,
            "close": q.get("close", 0),
            "volume": q.get("volume", 0),
            "change_rate": cr,
            "foreign_buy": foreign,
            "trust_buy": trust,
            "yoy_growth": yoy,
            "score": score,
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:params.get("top_n", 20)]


def _calc_score(q: dict, inst: dict, rev: dict, params: dict) -> int:
    score = 0
    # 量（最高 30 分）
    vol_ratio = min(q.get("volume", 0) / max(params["min_volume"], 1), 5)
    score += int(vol_ratio * 6)
    # 外資買超（最高 30 分）
    foreign_ratio = min(inst.get("foreign_buy", 0) / max(params["min_foreign_buy"], 1), 5)
    score += int(foreign_ratio * 6)
    # 營收成長（最高 40 分）
    yoy = rev.get("yoy_growth", 0)
    score += min(int(yoy / 2), 40)
    return min(score, 100)
```

- [ ] **Step 4: 確認測試通過**

```bash
python -m pytest tests/test_screener.py -v
```

預期：`3 passed`

- [ ] **Step 5: Commit**

```bash
git add screener.py tests/test_screener.py && git commit -m "feat: 多維度篩選邏輯含評分"
```

---

## Task 6：DeepSeek AI 分析

**Files:**
- Create: `~/CCProject/stock-screener-ai/ai_analyzer.py`
- Create: `~/CCProject/stock-screener-ai/tests/test_ai_analyzer.py`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_ai_analyzer.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from unittest.mock import patch, MagicMock
import ai_analyzer

MOCK_CANDIDATES = [
    {"code": "2330", "close": 580, "volume": 50000, "change_rate": 2.5,
     "foreign_buy": 3000, "trust_buy": 100, "yoy_growth": 25.0, "score": 85},
    {"code": "2454", "close": 720, "volume": 8000, "change_rate": 1.5,
     "foreign_buy": 1200, "trust_buy": 50, "yoy_growth": 18.0, "score": 72},
]

def test_analyze_returns_list_with_recommendation():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="""
[
  {"code": "2330", "name": "台積電", "recommendation": "買進", "reason": "外資大買，營收亮眼"},
  {"code": "2454", "name": "聯發科", "recommendation": "觀望", "reason": "漲幅偏小"}
]
"""))]
    )
    with patch("ai_analyzer._get_client", return_value=mock_client):
        result = ai_analyzer.analyze(MOCK_CANDIDATES)
    assert len(result) == 2
    assert result[0]["recommendation"] in ["買進", "觀望", "避開"]

def test_analyze_handles_empty_input():
    result = ai_analyzer.analyze([])
    assert result == []
```

- [ ] **Step 2: 確認失敗**

```bash
python -m pytest tests/test_ai_analyzer.py -v
```

- [ ] **Step 3: 實作 ai_analyzer.py**

```python
# ai_analyzer.py
import json
from openai import OpenAI
from config import DEEPSEEK_API_KEY

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )
    return _client

def analyze(candidates: list) -> list:
    """送候選清單給 DeepSeek，回傳含推薦的清單。"""
    if not candidates:
        return []

    table = "\n".join([
        f"{c['code']} | 收盤:{c['close']} | 量:{c['volume']}張 | 漲:{c['change_rate']}% | "
        f"外資:{c['foreign_buy']}張 | 投信:{c['trust_buy']}張 | 月營收年增:{c['yoy_growth']}% | 評分:{c['score']}"
        for c in candidates
    ])

    prompt = f"""你是台股專業分析師。以下是今日初篩候選股資料：

{table}

請分析每一檔，給出：
1. 推薦方向：買進 / 觀望 / 避開
2. 一句話理由（30字以內）

回傳嚴格 JSON 格式，範例：
[
  {{"code": "2330", "name": "台積電", "recommendation": "買進", "reason": "外資連續買超，營收創高"}},
  {{"code": "2454", "name": "聯發科", "recommendation": "觀望", "reason": "漲幅不足，等突破訊號"}}
]

只回傳 JSON，不要其他文字。"""

    client = _get_client()
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
    )
    raw = resp.choices[0].message.content.strip()
    # 清除可能的 markdown code block
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)
```

- [ ] **Step 4: 確認測試通過**

```bash
python -m pytest tests/test_ai_analyzer.py -v
```

預期：`2 passed`

- [ ] **Step 5: Commit**

```bash
git add ai_analyzer.py tests/test_ai_analyzer.py && git commit -m "feat: DeepSeek AI 分析整合"
```

---

## Task 7：Flask API 路由整合

**Files:**
- Modify: `~/CCProject/stock-screener-ai/app.py`（完整版）

- [ ] **Step 1: 完整 app.py**

```python
# app.py
from flask import Flask, render_template, jsonify, request
from config import PORT, DEFAULT_PARAMS
from database import DB
import screener, data_fetcher, ai_analyzer
from datetime import datetime
import traceback

app = Flask(__name__)
db = DB()

@app.route("/")
def index():
    return render_template("index.html", default_params=DEFAULT_PARAMS)

@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/api/screen", methods=["POST"])
def screen():
    try:
        params = {**DEFAULT_PARAMS, **request.json}

        # 抓今日日期
        today = datetime.today().strftime("%Y-%m-%d")

        # 1. 抓法人資料（FinMind）
        institutional = data_fetcher.get_institutional_data(today)

        # 2. 抓所有有法人資料的股票代號（避免抓全市場）
        stock_ids = [sid for sid in institutional.keys()
                     if institutional[sid]["foreign_buy"] >= params["min_foreign_buy"]][:200]

        if not stock_ids:
            return jsonify({"error": "今日無符合條件的法人資料"}), 400

        # 3. 抓即時量價（Shioaji）
        quotes = data_fetcher.get_realtime_quotes(stock_ids)

        # 4. 抓月營收（FinMind）
        revenue = data_fetcher.get_revenue_data()

        # 5. 篩選
        candidates = screener.filter_stocks(quotes, institutional, revenue, params)

        if not candidates:
            return jsonify({"candidates": [], "ai_results": [], "params": params})

        # 6. AI 分析
        ai_results = ai_analyzer.analyze(candidates)

        # 7. 合併結果
        ai_map = {r["code"]: r for r in ai_results}
        final = []
        for c in candidates:
            rec = ai_map.get(c["code"], {})
            final.append({**c,
                "name": rec.get("name", c["code"]),
                "recommendation": rec.get("recommendation", "觀望"),
                "reason": rec.get("reason", ""),
            })

        # 8. 存歷史
        db.save_query(params=params, results=final)

        return jsonify({"candidates": final, "params": params})

    except Exception as e:
        return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500

@app.route("/api/history")
def history():
    limit = int(request.args.get("limit", 20))
    return jsonify(db.list_history(limit=limit))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
```

- [ ] **Step 2: 啟動測試路由**

```bash
pkill -f "python app.py" 2>/dev/null; sleep 1
cd ~/CCProject/stock-screener-ai && python app.py &
sleep 2
curl -s http://localhost:5400/api/health
curl -s http://localhost:5400/api/history
```

預期：`{"status": "ok"}` 和 `[]`

- [ ] **Step 3: Commit**

```bash
git add app.py && git commit -m "feat: Flask API 路由完整版"
```

---

## Task 8：前端介面

**Files:**
- Modify: `~/CCProject/stock-screener-ai/templates/index.html`（完整版）

- [ ] **Step 1: 完整 index.html**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>AI 選股系統</title>
  <style>
    * { box-sizing: border-box; }
    body { font-family: system-ui, sans-serif; background: #0f1117; color: #e0e0e0; margin: 0; padding: 20px; }
    h1 { color: #4fc3f7; margin-bottom: 4px; }
    .subtitle { color: #888; margin-bottom: 24px; font-size: 14px; }
    .panel { background: #1a1d27; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
    .panel h2 { color: #81c784; margin-top: 0; font-size: 16px; }
    .params-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
    .param-item label { display: block; font-size: 12px; color: #aaa; margin-bottom: 4px; }
    .param-item input { width: 100%; background: #252836; border: 1px solid #333; color: #e0e0e0; padding: 6px 10px; border-radius: 6px; font-size: 14px; }
    button#run { background: #4fc3f7; color: #0f1117; border: none; padding: 12px 32px; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; }
    button#run:disabled { background: #333; color: #666; cursor: not-allowed; }
    .progress { color: #ffd54f; font-size: 14px; margin-top: 12px; min-height: 20px; }
    .results-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
    .card { background: #1e2230; border-radius: 10px; padding: 16px; border-left: 4px solid #555; }
    .card.buy { border-color: #ef5350; }
    .card.watch { border-color: #ffd54f; }
    .card.avoid { border-color: #78909c; }
    .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
    .card-code { font-size: 18px; font-weight: bold; color: #4fc3f7; }
    .card-name { font-size: 13px; color: #aaa; }
    .tag { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }
    .tag.buy { background: #ef5350; color: white; }
    .tag.watch { background: #ffd54f; color: #333; }
    .tag.avoid { background: #546e7a; color: white; }
    .card-reason { font-size: 13px; color: #ccc; margin: 8px 0; }
    .card-stats { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-top: 10px; font-size: 12px; }
    .stat { background: #252836; padding: 4px 8px; border-radius: 6px; text-align: center; }
    .stat-label { color: #888; }
    .stat-value { color: #e0e0e0; font-weight: bold; }
    .history-item { padding: 10px; border-bottom: 1px solid #252836; cursor: pointer; }
    .history-item:hover { background: #1e2230; }
    .history-time { font-size: 12px; color: #888; }
    .error { color: #ef5350; background: #1e1010; padding: 12px; border-radius: 8px; }
    .score-bar { height: 4px; background: #252836; border-radius: 2px; margin-top: 8px; }
    .score-fill { height: 100%; border-radius: 2px; background: linear-gradient(90deg, #4fc3f7, #81c784); }
  </style>
</head>
<body>
  <h1>🤖 AI 選股系統</h1>
  <p class="subtitle">FinMind + Shioaji + DeepSeek V3 · 混合多維度篩選</p>

  <div class="panel">
    <h2>⚙️ 篩選條件</h2>
    <div class="params-grid" id="params-grid">
      <!-- 動態生成 -->
    </div>
    <div style="margin-top:16px">
      <button id="run" onclick="runScreen()">🔍 開始分析</button>
      <span class="progress" id="progress"></span>
    </div>
  </div>

  <div class="panel" id="results-panel" style="display:none">
    <h2>📊 分析結果 <span id="result-count" style="color:#888;font-size:13px"></span></h2>
    <div class="results-grid" id="results-grid"></div>
  </div>

  <div class="panel">
    <h2>🕐 歷史記錄</h2>
    <div id="history-list"><p style="color:#888;font-size:14px">載入中...</p></div>
  </div>

  <script>
    const PARAM_LABELS = {
      min_volume: "最低成交量（張）",
      min_price_change: "最低漲幅（%）",
      max_price_change: "最高漲幅（%）",
      min_foreign_buy: "外資買超最低（張）",
      min_trust_buy: "投信買超最低（張）",
      max_margin_increase: "融資增加上限（%）",
      min_revenue_growth: "月營收年增率（%）",
      max_per: "本益比上限",
      top_n: "送 AI 分析前 N 檔",
    };

    const defaults = {{ default_params | tojson }};

    // 建立參數輸入框
    const grid = document.getElementById("params-grid");
    for (const [key, val] of Object.entries(defaults)) {
      const div = document.createElement("div");
      div.className = "param-item";
      div.innerHTML = `<label>${PARAM_LABELS[key] || key}</label><input type="number" id="p_${key}" value="${val}" step="any">`;
      grid.appendChild(div);
    }

    function getParams() {
      const p = {};
      for (const key of Object.keys(defaults)) {
        p[key] = parseFloat(document.getElementById("p_" + key).value);
      }
      return p;
    }

    const tagClass = { "買進": "buy", "觀望": "watch", "避開": "avoid" };
    const tagLabel = { "買進": "買進", "觀望": "觀望", "避開": "避開" };

    function renderCard(c) {
      const cls = tagClass[c.recommendation] || "watch";
      return `
        <div class="card ${cls}">
          <div class="card-header">
            <div>
              <span class="card-code">${c.code}</span>
              <span class="card-name">${c.name || ""}</span>
            </div>
            <span class="tag ${cls}">${c.recommendation}</span>
          </div>
          <div class="card-reason">${c.reason || ""}</div>
          <div class="score-bar"><div class="score-fill" style="width:${c.score}%"></div></div>
          <div class="card-stats">
            <div class="stat"><div class="stat-label">收盤</div><div class="stat-value">${c.close}</div></div>
            <div class="stat"><div class="stat-label">量(張)</div><div class="stat-value">${c.volume?.toLocaleString()}</div></div>
            <div class="stat"><div class="stat-label">漲幅</div><div class="stat-value" style="color:${c.change_rate>0?'#ef5350':'#81c784'}">${c.change_rate}%</div></div>
            <div class="stat"><div class="stat-label">外資</div><div class="stat-value">${c.foreign_buy?.toLocaleString()}</div></div>
            <div class="stat"><div class="stat-label">投信</div><div class="stat-value">${c.trust_buy}</div></div>
            <div class="stat"><div class="stat-label">營收年增</div><div class="stat-value">${c.yoy_growth}%</div></div>
          </div>
        </div>`;
    }

    async function runScreen() {
      const btn = document.getElementById("run");
      const progress = document.getElementById("progress");
      btn.disabled = true;
      const steps = ["正在抓取法人資料...", "正在抓取即時量價...", "正在抓取月營收...", "正在執行篩選...", "DeepSeek AI 分析中..."];
      let i = 0;
      const timer = setInterval(() => { progress.textContent = steps[Math.min(i++, steps.length-1)]; }, 3000);

      try {
        const resp = await fetch("/api/screen", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(getParams())
        });
        clearInterval(timer);
        const data = await resp.json();

        if (data.error) {
          progress.innerHTML = `<span class="error">❌ ${data.error}</span>`;
          return;
        }

        progress.textContent = `✅ 完成，共 ${data.candidates.length} 檔`;
        document.getElementById("result-count").textContent = `（${data.candidates.length} 檔）`;
        document.getElementById("results-panel").style.display = "block";
        document.getElementById("results-grid").innerHTML = data.candidates.map(renderCard).join("");
        loadHistory();
      } catch(e) {
        clearInterval(timer);
        progress.innerHTML = `<span class="error">❌ ${e.message}</span>`;
      } finally {
        btn.disabled = false;
      }
    }

    async function loadHistory() {
      const resp = await fetch("/api/history?limit=10");
      const data = await resp.json();
      const el = document.getElementById("history-list");
      if (!data.length) { el.innerHTML = "<p style='color:#888;font-size:14px'>尚無記錄</p>"; return; }
      el.innerHTML = data.map(h => {
        const buys = h.results.filter(r => r.recommendation === "買進").length;
        return `<div class="history-item">
          <span class="history-time">${h.created_at}</span>
          ｜共 ${h.results.length} 檔，買進 ${buys} 檔
        </div>`;
      }).join("");
    }

    loadHistory();
  </script>
</body>
</html>
```

- [ ] **Step 2: 重啟並開瀏覽器驗證**

```bash
pkill -f "python app.py" 2>/dev/null; sleep 1
cd ~/CCProject/stock-screener-ai && python app.py &
sleep 2
open -a Firefox http://localhost:5400
```

確認：頁面正常載入、參數輸入框顯示、歷史記錄區顯示「尚無記錄」

- [ ] **Step 3: Commit**

```bash
git add templates/index.html && git commit -m "feat: 前端選股介面完成"
```

---

## 自我審查

**Spec coverage:**
- ✅ 可調整篩選條件 → Task 8 前端參數輸入框
- ✅ 混合技術/籌碼/基本面 → Task 5 screener.py
- ✅ AI 判斷 → Task 6 ai_analyzer.py (DeepSeek V3)
- ✅ 歷史記錄 → Task 2 database.py + Task 7 /api/history
- ✅ 家用（無 Cloudflare） → port 5400 localhost

**Placeholder scan:** 無 TBD/TODO

**Type consistency:** `filter_stocks` 輸入/輸出格式在 Task 5 定義，Task 7 app.py 直接使用，欄位名稱一致。
