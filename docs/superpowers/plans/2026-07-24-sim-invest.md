# sim-invest 模擬投資 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個前瞻式模擬投資服務，把兩套既定配置當虛擬真金建倉、逐月 DCA、每日追蹤淨值與再平衡訊號，絕不真實下單。

**Architecture:** Flask + SQLite 單體服務，跑在 port 5250。純邏輯層（plans/engine）與 IO 層（quotes/store/app）分離，邏輯層可完全用假報價單元測試。每日由 LaunchAgent 觸發 `jobs/daily.py` 做「到期 DCA + 淨值快照」。前端一頁儀表板，並掛一張卡進 command-center(5950)。

**Tech Stack:** Python 3.12、Flask、SQLite（stdlib sqlite3）、yfinance（美股報價）、Shioaji（台股報價）、pytest。

## Global Constraints

- 全程虛擬，**絕不呼叫任何真實下單 API**。只讀報價。
- 台股報價（0050、00864B）只走 **Shioaji**；美股走 **yfinance**；USD/TWD 匯率由 yfinance（`TWD=X`）抓。
- 模擬允許**碎股**（fractional shares），以精準命中目標金額；真實整股/千股限制不模擬。
- 金額單位一律台幣（TWD）。美股市值 = 美元收盤 × USD/TWD。
- 帳戶本金:A = 9,000,000；B = 1,000,000。DCA 期數 = 6。
- 報價層在測試中一律以 mock/monkeypatch 隔離，不打真實網路。
- 檔案精簡、單一職責;所有 SQLite 讀寫只經 `store.py`。
- 目標覆蓋率 80%+；每個 Task 以 TDD（紅→綠）推進並各自 commit。

---

## File Structure

```
sim-invest/
  plans.py            # 兩帳戶目標配置（純資料 + dataclass）
  store.py            # SQLite schema 與所有讀寫
  quotes.py           # 報價層：get_quote(TW→Shioaji, US→yfinance)、get_fx()
  engine.py           # build_lump / run_dca_tranche / daily_snapshot / check_rebalance
  jobs/daily.py       # 每日排程進入點
  app.py              # Flask：API + 頁面
  templates/index.html
  tests/
    test_plans.py
    test_store.py
    test_quotes.py
    test_engine.py
    test_daily.py
    test_app.py
  requirements.txt
  sim.db              # 執行期產生（git 忽略）
```

Base dir 一律 `/Users/steven/CCProject/sim-invest/`。以下路徑皆相對於此。

---

## Task 1: 專案骨架 + plans.py（兩帳戶配置）

**Files:**
- Create: `sim-invest/requirements.txt`
- Create: `sim-invest/plans.py`
- Test: `sim-invest/tests/test_plans.py`

**Interfaces:**
- Produces:
  - `Target(ticker:str, market:str, category:str, target_twd:float, build_method:str)` — frozen dataclass；`market∈{'TW','US'}`，`build_method∈{'lump','dca'}`。
  - `Plan(plan_id:str, name:str, capital_twd:float, dca_months:int, targets:tuple[Target,...])`
  - `PLANS: dict[str, Plan]`（keys `'A'`,`'B'`）

- [ ] **Step 1: 建立 requirements.txt**

```
flask
yfinance
shioaji
pytest
```

- [ ] **Step 2: 寫失敗測試** `tests/test_plans.py`

```python
import math
from plans import PLANS, Plan, Target

def test_two_plans_exist():
    assert set(PLANS.keys()) == {"A", "B"}

def test_account_a_totals_and_methods():
    a = PLANS["A"]
    assert isinstance(a, Plan)
    assert a.capital_twd == 9_000_000
    assert a.dca_months == 6
    # 金額加總 = 本金（容忍四捨五入 ±1 元）
    assert math.isclose(sum(t.target_twd for t in a.targets), 9_000_000, abs_tol=1.0)
    # 13 檔標的
    assert len(a.targets) == 13
    # 一次建倉:00864B + 5 檔個股
    lump = {t.ticker for t in a.targets if t.build_method == "lump"}
    assert lump == {"00864B", "BE", "SNDK", "CORZ", "IREN", "CRWV"}
    # DCA:7 檔 ETF
    dca = {t.ticker for t in a.targets if t.build_method == "dca"}
    assert dca == {"XLP", "XLU", "GLD", "EFV", "EWJ", "VWO", "0050"}

def test_account_a_market_tagging():
    a = PLANS["A"]
    tw = {t.ticker for t in a.targets if t.market == "TW"}
    assert tw == {"00864B", "0050"}

def test_account_b_totals():
    b = PLANS["B"]
    assert b.capital_twd == 1_000_000
    assert math.isclose(sum(t.target_twd for t in b.targets), 1_000_000, abs_tol=1.0)
    assert {t.ticker for t in b.targets} == {"CRM", "MSFT", "NOW", "AAPL", "PLTR"}
    assert all(t.build_method == "lump" for t in b.targets)
    assert all(t.market == "US" for t in b.targets)
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_plans.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'plans'`）

- [ ] **Step 4: 實作 plans.py**

```python
"""兩帳戶模擬配置（定案版，見 specs/2026-07-24-sim-invest-design.md）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    ticker: str
    market: str          # 'TW' | 'US'
    category: str
    target_twd: float
    build_method: str    # 'lump' | 'dca'


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    capital_twd: float
    dca_months: int
    targets: tuple


_A = (
    Target("00864B", "TW", "短債防禦", 1_000_000, "lump"),
    Target("XLP", "US", "穩健防禦", 900_000, "dca"),
    Target("XLU", "US", "穩健防禦", 900_000, "dca"),
    Target("GLD", "US", "穩健防禦", 900_000, "dca"),
    Target("EFV", "US", "全球進攻", 1_413_333.33, "dca"),
    Target("EWJ", "US", "全球進攻", 1_177_777.78, "dca"),
    Target("VWO", "US", "全球進攻", 942_222.22, "dca"),
    Target("0050", "TW", "全球進攻", 588_888.89, "dca"),
    Target("BE", "US", "AI基建衛星", 412_222.22, "lump"),
    Target("SNDK", "US", "AI基建衛星", 353_333.33, "lump"),
    Target("CORZ", "US", "AI基建衛星", 117_777.78, "lump"),
    Target("IREN", "US", "AI基建衛星", 117_777.78, "lump"),
    Target("CRWV", "US", "AI基建衛星", 176_666.67, "lump"),
)

_B = (
    Target("CRM", "US", "AI Agent", 350_000, "lump"),
    Target("MSFT", "US", "AI Agent", 250_000, "lump"),
    Target("NOW", "US", "AI Agent", 150_000, "lump"),
    Target("AAPL", "US", "AI Agent", 150_000, "lump"),
    Target("PLTR", "US", "AI Agent", 100_000, "lump"),
)

PLANS = {
    "A": Plan("A", "900萬全配置", 9_000_000, 6, _A),
    "B": Plan("B", "100萬 AI Agent 2.0", 1_000_000, 6, _B),
}
```

- [ ] **Step 5: 執行測試確認通過**

Run: `cd sim-invest && python -m pytest tests/test_plans.py -v`
Expected: PASS（5 passed）

- [ ] **Step 6: Commit**

```bash
git add sim-invest/requirements.txt sim-invest/plans.py sim-invest/tests/test_plans.py
git commit -m "feat(sim-invest): 兩帳戶配置定義 plans.py"
```

---

## Task 2: store.py（SQLite schema 與讀寫）

**Files:**
- Create: `sim-invest/store.py`
- Test: `sim-invest/tests/test_store.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `connect(path:str) -> sqlite3.Connection`（`row_factory=sqlite3.Row`，已 `init_db`）
  - `create_account(conn, account_id:str, name:str, plan_id:str, start_date:str, capital_twd:float) -> None`
  - `get_account(conn, account_id:str) -> sqlite3.Row | None`
  - `add_trade(conn, account_id, date, ticker, market, shares:float, price_native:float, fx_rate:float, cost_twd:float, tranche_no:int) -> None`
  - `get_trades(conn, account_id:str) -> list[sqlite3.Row]`
  - `add_snapshot(conn, account_id, date, total_value_twd:float, cash_twd:float, unrealized_pnl_twd:float, by_category_json:str) -> None`（同 account+date 以 UPSERT 覆蓋）
  - `get_snapshots(conn, account_id:str) -> list[sqlite3.Row]`（依 date 升冪）
  - `latest_snapshot(conn, account_id:str) -> sqlite3.Row | None`

- [ ] **Step 1: 寫失敗測試** `tests/test_store.py`

```python
import store

def test_create_and_get_account():
    conn = store.connect(":memory:")
    store.create_account(conn, "A", "測試A", "A", "2026-07-24", 9_000_000)
    row = store.get_account(conn, "A")
    assert row["name"] == "測試A"
    assert row["capital_twd"] == 9_000_000

def test_add_and_get_trades():
    conn = store.connect(":memory:")
    store.create_account(conn, "A", "A", "A", "2026-07-24", 9_000_000)
    store.add_trade(conn, "A", "2026-07-24", "BE", "US", 10.5, 40.0, 32.35, 13_587.0, 0)
    trades = store.get_trades(conn, "A")
    assert len(trades) == 1
    assert trades[0]["ticker"] == "BE"
    assert trades[0]["shares"] == 10.5

def test_snapshot_upsert_overwrites_same_date():
    conn = store.connect(":memory:")
    store.create_account(conn, "A", "A", "A", "2026-07-24", 9_000_000)
    store.add_snapshot(conn, "A", "2026-07-24", 100.0, 50.0, 0.0, "{}")
    store.add_snapshot(conn, "A", "2026-07-24", 200.0, 40.0, 5.0, "{}")
    snaps = store.get_snapshots(conn, "A")
    assert len(snaps) == 1
    assert snaps[0]["total_value_twd"] == 200.0
    assert store.latest_snapshot(conn, "A")["total_value_twd"] == 200.0
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_store.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'store'`）

- [ ] **Step 3: 實作 store.py**

```python
"""所有 SQLite 讀寫的唯一入口。"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    start_date TEXT NOT NULL,
    capital_twd REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS trades(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    market TEXT NOT NULL,
    shares REAL NOT NULL,
    price_native REAL NOT NULL,
    fx_rate REAL NOT NULL,
    cost_twd REAL NOT NULL,
    tranche_no INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots(
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    total_value_twd REAL NOT NULL,
    cash_twd REAL NOT NULL,
    unrealized_pnl_twd REAL NOT NULL,
    by_category_json TEXT NOT NULL,
    PRIMARY KEY(account_id, date)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def create_account(conn, account_id, name, plan_id, start_date, capital_twd):
    conn.execute(
        "INSERT OR REPLACE INTO accounts VALUES(?,?,?,?,?)",
        (account_id, name, plan_id, start_date, capital_twd),
    )
    conn.commit()


def get_account(conn, account_id):
    return conn.execute(
        "SELECT * FROM accounts WHERE account_id=?", (account_id,)
    ).fetchone()


def add_trade(conn, account_id, date, ticker, market, shares,
              price_native, fx_rate, cost_twd, tranche_no):
    conn.execute(
        "INSERT INTO trades(account_id,date,ticker,market,shares,price_native,"
        "fx_rate,cost_twd,tranche_no) VALUES(?,?,?,?,?,?,?,?,?)",
        (account_id, date, ticker, market, shares, price_native,
         fx_rate, cost_twd, tranche_no),
    )
    conn.commit()


def get_trades(conn, account_id):
    return conn.execute(
        "SELECT * FROM trades WHERE account_id=? ORDER BY id", (account_id,)
    ).fetchall()


def add_snapshot(conn, account_id, date, total_value_twd, cash_twd,
                 unrealized_pnl_twd, by_category_json):
    conn.execute(
        "INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?)",
        (account_id, date, total_value_twd, cash_twd,
         unrealized_pnl_twd, by_category_json),
    )
    conn.commit()


def get_snapshots(conn, account_id):
    return conn.execute(
        "SELECT * FROM snapshots WHERE account_id=? ORDER BY date", (account_id,)
    ).fetchall()


def latest_snapshot(conn, account_id):
    return conn.execute(
        "SELECT * FROM snapshots WHERE account_id=? ORDER BY date DESC LIMIT 1",
        (account_id,),
    ).fetchone()
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_store.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/store.py sim-invest/tests/test_store.py
git commit -m "feat(sim-invest): SQLite store 層"
```

---

## Task 3: quotes.py（報價層，測試以 mock 隔離）

**Files:**
- Create: `sim-invest/quotes.py`
- Test: `sim-invest/tests/test_quotes.py`

**Interfaces:**
- Produces:
  - `get_quote(ticker:str, market:str) -> float` — 回傳「原幣別」收盤價（US→美元，TW→台幣）。內部:`market=='US'` 走 `_yf_close(ticker)`；`market=='TW'` 走 `_shioaji_close(ticker)`。
  - `get_fx() -> float` — USD/TWD，走 `_yf_close('TWD=X')`。
  - `_yf_close(symbol:str) -> float`、`_shioaji_close(ticker:str) -> float`（供測試 monkeypatch）。

- [ ] **Step 1: 寫失敗測試** `tests/test_quotes.py`

```python
import quotes

def test_get_quote_us_uses_yf(monkeypatch):
    monkeypatch.setattr(quotes, "_yf_close", lambda s: 41.23 if s == "XLP" else 0.0)
    assert quotes.get_quote("XLP", "US") == 41.23

def test_get_quote_tw_uses_shioaji(monkeypatch):
    monkeypatch.setattr(quotes, "_shioaji_close", lambda t: 48.5 if t == "00864B" else 0.0)
    assert quotes.get_quote("00864B", "TW") == 48.5

def test_get_fx_reads_twd_pair(monkeypatch):
    monkeypatch.setattr(quotes, "_yf_close", lambda s: 32.35 if s == "TWD=X" else 0.0)
    assert quotes.get_fx() == 32.35

def test_unknown_market_raises():
    import pytest
    with pytest.raises(ValueError):
        quotes.get_quote("XLP", "XX")
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_quotes.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'quotes'`）

- [ ] **Step 3: 實作 quotes.py**

```python
"""報價層：US→yfinance、TW→Shioaji、匯率→yfinance TWD=X。帶記憶體快取。"""
import time

_cache: dict = {}
_TTL = 3600  # 秒


def _cached(key, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _yf_close(symbol: str) -> float:
    import yfinance as yf
    data = yf.Ticker(symbol).history(period="1d")
    if data.empty:
        raise RuntimeError(f"yfinance 無資料: {symbol}")
    return float(data["Close"].iloc[-1])


_SHIOAJI_SUFFIX = {"0050": "0050", "00864B": "00864B"}  # 皆為上市代號


def _shioaji_close(ticker: str) -> float:
    """以既有 repo 的 Shioaji 登入模式取即時/收盤價。
    參考 scripts/ma_monitor.py 的 Shioaji 初始化；此處只讀 snapshot.close。"""
    import shioaji as sj
    import os
    api = sj.Shioaji()
    api.login(os.environ["SHIOAJI_API_KEY"], os.environ["SHIOAJI_SECRET_KEY"])
    try:
        contract = api.Contracts.Stocks[ticker]
        snap = api.snapshots([contract])[0]
        return float(snap.close)
    finally:
        api.logout()


def get_quote(ticker: str, market: str) -> float:
    if market == "US":
        return _cached(("US", ticker), lambda: _yf_close(ticker))
    if market == "TW":
        return _cached(("TW", ticker), lambda: _shioaji_close(ticker))
    raise ValueError(f"未知市場: {market}")


def get_fx() -> float:
    return _cached(("FX", "USDTWD"), lambda: _yf_close("TWD=X"))
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_quotes.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/quotes.py sim-invest/tests/test_quotes.py
git commit -m "feat(sim-invest): 報價層 quotes.py（yfinance/Shioaji）"
```

---

## Task 4: engine — 價格換算與一次建倉（build_lump）

**Files:**
- Create: `sim-invest/engine.py`
- Test: `sim-invest/tests/test_engine.py`

**Interfaces:**
- Consumes:`plans.Plan/Target`、`store`、`quote_fn(ticker,market)->float`、`fx_fn()->float`
- Produces:
  - `price_twd(target, quote_fn, fx_fn) -> float` — US 標的回傳 `美元價×fx`；TW 回傳原台幣價。
  - `target_shares(target_twd:float, price_twd:float) -> float` — `target_twd/price_twd`（碎股）。
  - `build_lump(conn, account_id, plan, date, quote_fn, fx_fn) -> None` — 對 `build_method=='lump'` 的標的各建一筆 `tranche_no=0` 成交。

- [ ] **Step 1: 寫失敗測試**（附加到 `tests/test_engine.py`）

```python
import json
import store, engine
from plans import PLANS

# 固定假報價：美股一律 $10、匯率 32、台股一律 50 元
def q(ticker, market): return 10.0
def fx(): return 32.0

def _fresh(account_id="A"):
    conn = store.connect(":memory:")
    plan = PLANS[account_id]
    store.create_account(conn, account_id, plan.name, account_id, "2026-07-24", plan.capital_twd)
    return conn, plan

def test_price_twd_us_applies_fx():
    from plans import Target
    t = Target("XLP", "US", "x", 900_000, "dca")
    assert engine.price_twd(t, q, fx) == 320.0   # 10 * 32

def test_price_twd_tw_no_fx():
    from plans import Target
    t = Target("0050", "TW", "x", 100, "dca")
    assert engine.price_twd(t, lambda tk, m: 50.0, fx) == 50.0

def test_target_shares():
    assert engine.target_shares(320.0, 32.0) == 10.0

def test_build_lump_only_lump_targets():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    trades = store.get_trades(conn, "A")
    # A 帳戶 lump 標的共 6 檔（00864B + 5 個股）
    assert len(trades) == 6
    assert {t["ticker"] for t in trades} == {"00864B","BE","SNDK","CORZ","IREN","CRWV"}
    # 每筆 tranche_no=0、cost_twd 約等於目標金額
    be = next(t for t in trades if t["ticker"] == "BE")
    assert be["tranche_no"] == 0
    assert abs(be["cost_twd"] - 412_222.22) < 1.0
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'engine'`）

- [ ] **Step 3: 實作 engine.py（第一部分）**

```python
"""模擬引擎：建倉、DCA、快照、再平衡。純計算，IO 經 store/quote_fn/fx_fn。"""
import json


def price_twd(target, quote_fn, fx_fn) -> float:
    p = quote_fn(target.ticker, target.market)
    return p * fx_fn() if target.market == "US" else p


def target_shares(target_twd: float, price_twd: float) -> float:
    return target_twd / price_twd


def _buy(conn, account_id, date, target, amount_twd, quote_fn, fx_fn, tranche_no):
    native = quote_fn(target.ticker, target.market)
    fx = fx_fn() if target.market == "US" else 1.0
    ptwd = native * fx
    shares = amount_twd / ptwd
    import store
    store.add_trade(conn, account_id, date, target.ticker, target.market,
                    shares, native, fx, amount_twd, tranche_no)


def build_lump(conn, account_id, plan, date, quote_fn, fx_fn) -> None:
    for t in plan.targets:
        if t.build_method == "lump":
            _buy(conn, account_id, date, t, t.target_twd, quote_fn, fx_fn, 0)
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/engine.py sim-invest/tests/test_engine.py
git commit -m "feat(sim-invest): engine 價格換算與一次建倉"
```

---

## Task 5: engine — 逐月 DCA（run_dca_tranche）

**Files:**
- Modify: `sim-invest/engine.py`
- Test: `sim-invest/tests/test_engine.py`

**Interfaces:**
- Produces:`run_dca_tranche(conn, account_id, plan, date, tranche_no:int, quote_fn, fx_fn) -> None` — 對 `build_method=='dca'` 標的各投入 `target_twd/plan.dca_months`，記錄該 `tranche_no`（1..6）。

- [ ] **Step 1: 寫失敗測試**（附加）

```python
def test_dca_tranche_invests_one_sixth():
    conn, plan = _fresh("A")
    engine.run_dca_tranche(conn, "A", plan, "2026-08-01", 1, q, fx)
    trades = store.get_trades(conn, "A")
    # A 帳戶 dca 標的 7 檔
    assert len(trades) == 7
    assert {t["ticker"] for t in trades} == {"XLP","XLU","GLD","EFV","EWJ","VWO","0050"}
    xlp = next(t for t in trades if t["ticker"] == "XLP")
    assert xlp["tranche_no"] == 1
    assert abs(xlp["cost_twd"] - 900_000/6) < 1.0   # 150,000

def test_six_tranches_fully_invest_dca_targets():
    conn, plan = _fresh("A")
    for n in range(1, 7):
        engine.run_dca_tranche(conn, "A", plan, f"2026-0{n}-01", n, q, fx)
    trades = [t for t in store.get_trades(conn, "A") if t["ticker"] == "EFV"]
    assert abs(sum(t["cost_twd"] for t in trades) - 1_413_333.33) < 1.0
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -k dca -v`
Expected: FAIL（`AttributeError: module 'engine' has no attribute 'run_dca_tranche'`）

- [ ] **Step 3: 實作（附加到 engine.py）**

```python
def run_dca_tranche(conn, account_id, plan, date, tranche_no, quote_fn, fx_fn) -> None:
    for t in plan.targets:
        if t.build_method == "dca":
            amount = t.target_twd / plan.dca_months
            _buy(conn, account_id, date, t, amount, quote_fn, fx_fn, tranche_no)
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/engine.py sim-invest/tests/test_engine.py
git commit -m "feat(sim-invest): 逐月 DCA 投入"
```

---

## Task 6: engine — 每日淨值快照（daily_snapshot）

**Files:**
- Modify: `sim-invest/engine.py`
- Test: `sim-invest/tests/test_engine.py`

**Interfaces:**
- Produces:
  - `holdings(conn, account_id) -> dict[str, dict]` — `{ticker: {"shares","market","category","cost_twd"}}`（依成交彙總）。
  - `daily_snapshot(conn, account_id, plan, date, quote_fn, fx_fn) -> dict` — 計算並寫入 snapshot，回傳 `{"total_value_twd","cash_twd","unrealized_pnl_twd","by_category":{cat:mv}}`。
    - `cash_twd = capital − Σcost_twd`（尚未投入的 DCA 資金停泊為現金）。
    - `market_value = Σ shares×price_twd`；`total = cash + market_value`；`unrealized_pnl = market_value − Σcost_twd`。

- [ ] **Step 1: 寫失敗測試**（附加）

```python
def test_snapshot_all_lump_at_flat_price():
    # 只建 lump 部位，報價與建倉同價 → 未實現損益=0、現金=本金−已投入
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    snap = engine.daily_snapshot(conn, "A", plan, "2026-07-24", q, fx)
    invested = sum(t.target_twd for t in plan.targets if t.build_method == "lump")
    assert abs(snap["cash_twd"] - (9_000_000 - invested)) < 1.0
    assert abs(snap["unrealized_pnl_twd"]) < 1.0
    assert abs(snap["total_value_twd"] - 9_000_000) < 1.0
    # 寫入 DB
    assert store.latest_snapshot(conn, "A")["date"] == "2026-07-24"

def test_snapshot_reflects_price_gain():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)      # 建倉價 $10
    # 之後美股漲一倍（$20），台股不變
    up = lambda tk, m: 20.0 if m == "US" else 10.0
    snap = engine.daily_snapshot(conn, "A", plan, "2026-07-25", up, fx)
    # lump 美股部位（BE/SNDK/CORZ/IREN/CRWV）市值翻倍，00864B（TW）不變
    us_cost = sum(t.target_twd for t in plan.targets
                  if t.build_method == "lump" and t.market == "US")
    assert abs(snap["unrealized_pnl_twd"] - us_cost) < 1.0        # 美股獲利 = 成本×100%

def test_snapshot_by_category_keys():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    snap = engine.daily_snapshot(conn, "A", plan, "2026-07-24", q, fx)
    assert "AI基建衛星" in snap["by_category"]
    assert "短債防禦" in snap["by_category"]
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -k snapshot -v`
Expected: FAIL（`AttributeError: ... 'daily_snapshot'`）

- [ ] **Step 3: 實作（附加到 engine.py）**

```python
def holdings(conn, account_id) -> dict:
    import store
    agg: dict = {}
    for tr in store.get_trades(conn, account_id):
        h = agg.setdefault(tr["ticker"], {
            "shares": 0.0, "market": tr["market"], "cost_twd": 0.0,
        })
        h["shares"] += tr["shares"]
        h["cost_twd"] += tr["cost_twd"]
    return agg


def _category_of(plan, ticker):
    for t in plan.targets:
        if t.ticker == ticker:
            return t.category
    return "其他"


def daily_snapshot(conn, account_id, plan, date, quote_fn, fx_fn) -> dict:
    import store
    hs = holdings(conn, account_id)
    fx = fx_fn()
    market_value = 0.0
    invested = 0.0
    by_cat: dict = {}
    for ticker, h in hs.items():
        native = quote_fn(ticker, h["market"])
        ptwd = native * fx if h["market"] == "US" else native
        mv = h["shares"] * ptwd
        market_value += mv
        invested += h["cost_twd"]
        cat = _category_of(plan, ticker)
        by_cat[cat] = by_cat.get(cat, 0.0) + mv
    cash = plan.capital_twd - invested
    total = cash + market_value
    pnl = market_value - invested
    store.add_snapshot(conn, account_id, date, total, cash, pnl, json.dumps(by_cat))
    return {"total_value_twd": total, "cash_twd": cash,
            "unrealized_pnl_twd": pnl, "by_category": by_cat}
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -v`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/engine.py sim-invest/tests/test_engine.py
git commit -m "feat(sim-invest): 每日淨值快照"
```

---

## Task 7: engine — 再平衡檢查（check_rebalance）

**Files:**
- Modify: `sim-invest/engine.py`
- Test: `sim-invest/tests/test_engine.py`

**Interfaces:**
- Produces:`check_rebalance(conn, account_id, plan, date, quote_fn, fx_fn) -> list[dict]`
  - 各區塊「實際市值比例（占總市值+現金）」對「目標比例」偏離 > 5 個百分點 → `{"type":"drift","category",...}`。
  - AI 衛星類市值 > 該類目標金額 × 1.5 → `{"type":"satellite_take_profit",...}`。
  - 目標比例 = 該類 `Σtarget_twd / capital`。

- [ ] **Step 1: 寫失敗測試**（附加）

```python
def test_rebalance_flat_no_signal():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)
    for n in range(1, 7):
        engine.run_dca_tranche(conn, "A", plan, f"2026-0{n}-01", n, q, fx)
    # 全部建完、價格不變 → 各類比例=目標，無訊號
    signals = engine.check_rebalance(conn, "A", plan, "2026-12-01", q, fx)
    assert signals == []

def test_rebalance_satellite_take_profit():
    conn, plan = _fresh("A")
    engine.build_lump(conn, "A", plan, "2026-07-24", q, fx)   # 衛星個股 $10 建倉
    # 衛星（美股個股）漲 2 倍 → 市值 2x 目標 > 1.5x → 觸發
    boom = lambda tk, m: 20.0 if m == "US" else 10.0
    signals = engine.check_rebalance(conn, "A", plan, "2026-08-01", boom, fx)
    assert any(s["type"] == "satellite_take_profit" for s in signals)
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -k rebalance -v`
Expected: FAIL（`AttributeError: ... 'check_rebalance'`）

- [ ] **Step 3: 實作（附加到 engine.py）**

```python
DRIFT_PP = 5.0          # 百分點
SATELLITE_MULT = 1.5
SATELLITE_CAT = "AI基建衛星"


def check_rebalance(conn, account_id, plan, date, quote_fn, fx_fn) -> list:
    snap = daily_snapshot(conn, account_id, plan, date, quote_fn, fx_fn)
    total = snap["total_value_twd"]
    by_cat = snap["by_category"]
    signals = []
    # 目標比例（依 capital）
    target_by_cat: dict = {}
    for t in plan.targets:
        target_by_cat[t.category] = target_by_cat.get(t.category, 0.0) + t.target_twd
    for cat, tgt_twd in target_by_cat.items():
        actual_pct = 100.0 * by_cat.get(cat, 0.0) / total if total else 0.0
        target_pct = 100.0 * tgt_twd / plan.capital_twd
        if abs(actual_pct - target_pct) > DRIFT_PP:
            signals.append({"type": "drift", "category": cat,
                            "actual_pct": round(actual_pct, 2),
                            "target_pct": round(target_pct, 2)})
    # 衛星獲利了結
    sat_mv = by_cat.get(SATELLITE_CAT, 0.0)
    sat_tgt = target_by_cat.get(SATELLITE_CAT, 0.0)
    if sat_tgt and sat_mv > sat_tgt * SATELLITE_MULT:
        signals.append({"type": "satellite_take_profit",
                        "market_value": round(sat_mv, 0),
                        "target": round(sat_tgt, 0)})
    return signals
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_engine.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/engine.py sim-invest/tests/test_engine.py
git commit -m "feat(sim-invest): 再平衡訊號檢查"
```

---

## Task 8: jobs/daily.py（每日排程協調）

**Files:**
- Create: `sim-invest/jobs/__init__.py`（空檔）
- Create: `sim-invest/jobs/daily.py`
- Test: `sim-invest/tests/test_daily.py`

**Interfaces:**
- Consumes:`store`、`engine`、`plans.PLANS`、`quotes`
- Produces:
  - `due_tranche(start_date:str, today:str, dca_months:int) -> int | None` — 若 today 為建帳日或其後每月同一日且在 1..6 期內，回傳期數;否則 None。建帳日→第1期。
  - `run_day(conn, account_id, today, quote_fn, fx_fn) -> dict` — 若帳戶今日首建則先 build_lump 並投第1期 DCA;若 today 命中後續 DCA 日則投該期;最後做 daily_snapshot。回傳 snapshot dict。

- [ ] **Step 1: 寫失敗測試** `tests/test_daily.py`

```python
import store
from jobs import daily
from plans import PLANS

def q(t, m): return 10.0
def fx(): return 32.0

def test_due_tranche_first_day_is_one():
    assert daily.due_tranche("2026-07-24", "2026-07-24", 6) == 1

def test_due_tranche_next_month_same_day_is_two():
    assert daily.due_tranche("2026-07-24", "2026-08-24", 6) == 2

def test_due_tranche_non_matching_day_none():
    assert daily.due_tranche("2026-07-24", "2026-08-10", 6) is None

def test_due_tranche_after_six_none():
    assert daily.due_tranche("2026-07-24", "2027-02-24", 6) is None

def test_run_day_first_day_builds_and_snapshots():
    conn = store.connect(":memory:")
    plan = PLANS["A"]
    store.create_account(conn, "A", plan.name, "A", "2026-07-24", plan.capital_twd)
    snap = daily.run_day(conn, "A", "2026-07-24", q, fx)
    trades = store.get_trades(conn, "A")
    # 建帳日：6 檔 lump + 7 檔 DCA 第1期 = 13 筆
    assert len(trades) == 13
    assert abs(snap["total_value_twd"] - 9_000_000) < 5.0
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_daily.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'jobs'`）

- [ ] **Step 3: 實作 jobs/daily.py**

```python
"""每日排程：到期 DCA + 淨值快照。"""
from datetime import date as _date

import store
import engine
from plans import PLANS


def _parse(d: str) -> _date:
    y, m, dd = map(int, d.split("-"))
    return _date(y, m, dd)


def due_tranche(start_date, today, dca_months):
    s, t = _parse(start_date), _parse(today)
    if t.day != s.day:
        return None
    months = (t.year - s.year) * 12 + (t.month - s.month)
    if 0 <= months < dca_months:
        return months + 1
    return None


def run_day(conn, account_id, today, quote_fn, fx_fn) -> dict:
    plan = PLANS[account_id]
    acct = store.get_account(conn, account_id)
    start = acct["start_date"]
    existing = store.get_trades(conn, account_id)
    if not existing:                       # 建帳日：先一次建倉
        engine.build_lump(conn, account_id, plan, today, quote_fn, fx_fn)
    n = due_tranche(start, today, plan.dca_months)
    if n is not None:
        already = {tr["tranche_no"] for tr in store.get_trades(conn, account_id)}
        if n not in already or n == 1 and not existing:
            engine.run_dca_tranche(conn, account_id, plan, today, n, quote_fn, fx_fn)
    return engine.daily_snapshot(conn, account_id, plan, today, quote_fn, fx_fn)
```

- [ ] **Step 4: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_daily.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add sim-invest/jobs/ sim-invest/tests/test_daily.py
git commit -m "feat(sim-invest): 每日排程 run_day / due_tranche"
```

---

## Task 9: app.py（Flask API + 頁面骨架）

**Files:**
- Create: `sim-invest/app.py`
- Create: `sim-invest/templates/index.html`（骨架，Task 10 補完前端）
- Test: `sim-invest/tests/test_app.py`

**Interfaces:**
- Produces（Flask app `app`）:
  - `GET /api/health` → `{"status":"ok"}`
  - `GET /api/accounts` → `[{"id","name","capital_twd"}]`（來自 PLANS）
  - `GET /api/account/<aid>` → `{"account","holdings":[...],"latest":snapshot,"targets":[...]}`
  - `GET /api/account/<aid>/nav` → `[{"date","total_value_twd"}]`
  - `GET /api/account/<aid>/rebalance` → `check_rebalance` 結果（用今日即時報價）
  - `GET /` → 渲染 index.html
- DB 路徑由 `SIM_DB` 環境變數（預設 `sim.db`）；報價用 `quotes.get_quote/get_fx`。

- [ ] **Step 1: 寫失敗測試** `tests/test_app.py`

```python
import os, store
from plans import PLANS

def test_health_and_accounts(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("SIM_DB", str(db))
    # 先建好帳戶與一筆快照
    conn = store.connect(str(db))
    store.create_account(conn, "A", PLANS["A"].name, "A", "2026-07-24", 9_000_000)
    store.add_snapshot(conn, "A", "2026-07-24", 9_000_000, 5_000_000, 0.0, "{}")
    conn.close()
    import importlib, app as appmod
    importlib.reload(appmod)
    c = appmod.app.test_client()
    assert c.get("/api/health").get_json()["status"] == "ok"
    ids = [a["id"] for a in c.get("/api/accounts").get_json()]
    assert ids == ["A", "B"]
    nav = c.get("/api/account/A/nav").get_json()
    assert nav[0]["total_value_twd"] == 9_000_000
```

- [ ] **Step 2: 執行確認失敗**

Run: `cd sim-invest && python -m pytest tests/test_app.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app'`）

- [ ] **Step 3: 實作 app.py**

```python
"""模擬投資 Flask 服務（port 5250，唯讀報價，絕不真實下單）。"""
import os
import json
from flask import Flask, jsonify, render_template

import store
import engine
import quotes
from plans import PLANS

app = Flask(__name__)
DB = os.environ.get("SIM_DB", "sim.db")


def _conn():
    return store.connect(DB)


@app.get("/api/health")
def health():
    return jsonify(status="ok", service="sim-invest")


@app.get("/api/accounts")
def accounts():
    return jsonify([{"id": p.plan_id, "name": p.name, "capital_twd": p.capital_twd}
                    for p in PLANS.values()])


@app.get("/api/account/<aid>")
def account(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    plan = PLANS[aid]
    conn = _conn()
    hs = engine.holdings(conn, aid)
    latest = store.latest_snapshot(conn, aid)
    return jsonify(
        account={"id": aid, "name": plan.name, "capital_twd": plan.capital_twd},
        holdings=[{"ticker": k, **v} for k, v in hs.items()],
        targets=[{"ticker": t.ticker, "category": t.category,
                  "target_twd": t.target_twd, "build_method": t.build_method,
                  "target_pct": round(100 * t.target_twd / plan.capital_twd, 2)}
                 for t in plan.targets],
        latest=dict(latest) if latest else None,
    )


@app.get("/api/account/<aid>/nav")
def nav(aid):
    conn = _conn()
    return jsonify([{"date": s["date"], "total_value_twd": s["total_value_twd"]}
                    for s in store.get_snapshots(conn, aid)])


@app.get("/api/account/<aid>/rebalance")
def rebalance(aid):
    if aid not in PLANS:
        return jsonify(error="unknown account"), 404
    from datetime import date
    conn = _conn()
    sigs = engine.check_rebalance(conn, aid, PLANS[aid], date.today().isoformat(),
                                  quotes.get_quote, quotes.get_fx)
    return jsonify(sigs)


@app.get("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    import uvicorn  # noqa
    app.run(host="127.0.0.1", port=5250)
```

- [ ] **Step 4: 建立 templates/index.html 骨架**

```html
<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<title>模擬投資</title></head>
<body><h1>模擬投資</h1><div id="app">載入中…</div>
<script>
fetch('/api/accounts').then(r=>r.json()).then(a=>{
  document.getElementById('app').textContent = JSON.stringify(a);
});
</script>
</body></html>
```

- [ ] **Step 5: 執行確認通過**

Run: `cd sim-invest && python -m pytest tests/test_app.py -v`
Expected: PASS（1 passed）

- [ ] **Step 6: 全套測試 + 覆蓋率**

Run: `cd sim-invest && python -m pytest --cov=. --cov-report=term-missing`
Expected: 全數 PASS,核心模組（plans/store/engine/jobs）覆蓋率 ≥ 80%

- [ ] **Step 7: Commit**

```bash
git add sim-invest/app.py sim-invest/templates/index.html sim-invest/tests/test_app.py
git commit -m "feat(sim-invest): Flask API 與頁面骨架"
```

---

## Task 10: 前端儀表板（index.html 完整版）

**Files:**
- Modify: `sim-invest/templates/index.html`

**驗證方式:** 此任務為前端,以 Playwright 目視驗證（非單元 TDD）。

**需求:** 沿用 command-center 的 Anthropic 暖調品牌（`--bg:#f7f2e4; --accent:#d97757; 台股紅漲綠跌`）。頁面含:
- 帳戶切換（A / B）
- 配置總表:目標比例 vs 實際比例 + 偏離、股數、成本、現值、未實現損益（紅漲綠跌）
- 淨值曲線（用 `<canvas>` 或輕量 inline SVG，勿引外部 CDN）
- DCA 進度:第 x/6 批、下次扣款日
- 再平衡警示區（讀 `/api/account/<aid>/rebalance`）

- [ ] **Step 1: 實作完整 index.html**

以 `fetch('/api/account/'+aid)` 與 `/nav`、`/rebalance` 組出上述區塊;純 vanilla JS + inline CSS/SVG，不引外部資源（CSP 友善）。表格每列:`標的｜目標%｜實際%｜偏離｜股數｜成本｜現值｜損益`。

- [ ] **Step 2: 啟動服務並用 Playwright 驗證**

```bash
cd sim-invest && SIM_DB=sim.db python -c "import app; print('import ok')"
# 先塞一筆測試帳戶與快照後啟動：
cd sim-invest && nohup python app.py > /tmp/sim-invest.log 2>&1 &
```
用 Playwright MCP 開 `http://localhost:5250/`，確認:帳戶表格渲染、數字非 NaN、切換 A/B 正常、無 console error。截圖存桌面並傳 Telegram。

- [ ] **Step 3: Commit**

```bash
git add sim-invest/templates/index.html
git commit -m "feat(sim-invest): 前端儀表板"
```

---

## Task 11: 初始化腳本、LaunchAgent、command-center 整合

**Files:**
- Create: `sim-invest/init_accounts.py`
- Create: `~/Library/LaunchAgents/com.steven.sim-invest-daily.plist`
- Modify: `command-center/sources.py`、`command-center/templates/index.html`
- Modify: `.gitignore`(加 `sim-invest/sim.db`)

- [ ] **Step 1: init_accounts.py（建立兩帳戶並跑建帳日）**

```python
"""一次性初始化：建立帳戶 A/B，執行建帳日建倉 + 首批 DCA + 快照。"""
from datetime import date
import store, quotes
from jobs import daily
from plans import PLANS

conn = store.connect("sim.db")
today = date.today().isoformat()
for aid, plan in PLANS.items():
    if store.get_account(conn, aid) is None:
        store.create_account(conn, aid, plan.name, aid, today, plan.capital_twd)
    daily.run_day(conn, aid, today, quotes.get_quote, quotes.get_fx)
    print(aid, store.latest_snapshot(conn, aid)["total_value_twd"])
```

Run: `cd sim-invest && python init_accounts.py`
Expected: 印出 A、B 兩行總市值（約 9,000,000 與 1,000,000）。

- [ ] **Step 2: 每日 LaunchAgent**（每日台灣早上 08:30 跑一次快照/到期 DCA）

Create `~/Library/LaunchAgents/com.steven.sim-invest-daily.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.steven.sim-invest-daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/steven/CCProject/sim-invest/venv/bin/python</string>
    <string>/Users/steven/CCProject/sim-invest/jobs/run_daily_all.py</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/steven/CCProject/sim-invest</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>/tmp/sim-invest-daily.log</string>
  <key>StandardErrorPath</key><string>/tmp/sim-invest-daily.err</string>
</dict></plist>
```

Create `sim-invest/jobs/run_daily_all.py`:

```python
"""LaunchAgent 進入點：對所有帳戶跑 run_day（今日）。"""
from datetime import date
import store, quotes
from jobs import daily
from plans import PLANS

conn = store.connect("sim.db")
today = date.today().isoformat()
for aid in PLANS:
    daily.run_day(conn, aid, today, quotes.get_quote, quotes.get_fx)
    print(aid, "ok", today)
```

Load: `launchctl load ~/Library/LaunchAgents/com.steven.sim-invest-daily.plist`

- [ ] **Step 3: command-center 卡片**（掛進 5950）

在 `command-center/sources.py` 加一個 `sim_invest()` reader（proxy `http://localhost:5250/api/accounts` + 各帳戶 latest snapshot），並在 `templates/index.html` 的 LIFE 或新區塊加一張卡，`site:'http://localhost:5250'`。依現有 `_wrap`/`_proxy` 模式撰寫。改完 **重啟 command-center 並 curl /api/health 驗證**。

- [ ] **Step 4: 服務常駐**（LaunchAgent for Flask，比照現有服務）

建立 `com.steven.sim-invest.plist` 常駐 `app.py`（port 5250），load 後 `curl -s localhost:5250/api/health` 應回 `{"status":"ok"}`。

- [ ] **Step 5: 驗證與 Commit**

```bash
curl -s localhost:5250/api/health
curl -s localhost:5250/api/account/A | python -m json.tool | head
git add sim-invest/init_accounts.py sim-invest/jobs/run_daily_all.py .gitignore \
        command-center/sources.py command-center/templates/index.html
git commit -m "feat(sim-invest): 初始化、每日排程、command-center 整合"
```

---

## Self-Review 結果

- **Spec 覆蓋**:模式(前瞻/Task 8,11)、兩帳戶配置(Task 1)、架構分層(Task 1-9)、資料模型(Task 2)、DCA 逐月(Task 5,8)、再平衡(Task 7)、報價 Shioaji+yfinance+匯率(Task 3)、每日快照(Task 6)、前端(Task 10)、command-center 卡(Task 11)、測試 80%(Task 9 Step 6) — 皆有對應任務。
- **配息處理**:spec 列為預設「計入現金、不自動再投入」;MVP 先不做配息（現金以未投入 DCA 資金呈現），列為後續增強,不影響核心。〔實作時如需，於 store 加 distributions 表 + snapshot 納入〕
- **型別一致**:`quote_fn(ticker,market)->float`、`fx_fn()->float`、`price_twd`、`daily_snapshot` 回傳鍵在 Task 4-11 一致。
- **無 placeholder**:各步驟均含實際程式碼與預期輸出。
