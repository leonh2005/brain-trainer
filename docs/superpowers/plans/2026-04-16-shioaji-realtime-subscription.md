# Shioaji 即時 Tick 訂閱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將今日盤中 K 棒資料從 Shioaji `kbars()`（固定 ~20 分鐘延遲）改為 tick 訂閱即時組合（延遲 < 3 秒）。

**Architecture:** Backend 透過 `api.on_tick_stk_v1()` 訂閱 Shioaji tick 串流，在記憶體中逐秒組合 1 分 K 棒；`/api/kbars` 今日資料改為回傳「歷史 kbars（用來補足開盤到訂閱前的空白）+ 即時 tick-built bars」的合併結果；前端把今日 live poll 間隔從 60 秒縮短到 5 秒，並新增 `/api/subscribe` 呼叫觸發後端訂閱。

**Tech Stack:** Python 3.14, Shioaji 1.3.3（`QuoteType.Tick`, `QuoteVersion.v1`, `TickSTKv1`）, Flask, threading.Lock, JavaScript ES2020

---

## 背景知識（實作前必讀）

### TickSTKv1 欄位
```python
# shioaji.stream_data_type.TickSTKv1 annotations:
# code: str            — 股票代號 e.g. '2330'
# datetime: datetime   — tick 時間（台灣本地時間）
# close: float         — 成交價
# open: float          — （tick 級別，通常同 close）
# high: float          — （tick 級別）
# low: float           — （tick 級別）
# volume: int          — 本次 tick 成交量（張）
# total_volume: int    — 今日累積量（張）
```

### Callback 登記方式（Shioaji 1.3.3）
```python
@api.on_tick_stk_v1()
def handler(exchange: sj.Exchange, tick: TickSTKv1) -> None:
    ...

api.quote.subscribe(
    api.Contracts.Stocks['2330'],
    quote_type=sj.constant.QuoteType.Tick,
    version=sj.constant.QuoteVersion.v1,
)
```
`on_tick_stk_v1` 是 **per-api-instance** decorator，重新 login 後需重新登記。

### 今日資料合併策略
```
Historical kbars()  → 09:00 ~ (now - ~20min)   # 舊 API，無 tick
Tick-built bars     → subscription start ~ now  # 新即時資料
Merged result       → union by ts，tick 優先（tick 精度高）
Gap 10:42~11:00     → 第一次訂閱時可能有空白，隨時間自動補齊
```

---

## 檔案異動清單

| 檔案 | 動作 | 說明 |
|------|------|------|
| `daytrade-replay/data.py` | **Modify** | 新增 `RealTimeFeed` class；修改 `_login()` 登記 tick callback；修改 `get_1min_kbars()` 合併即時資料 |
| `daytrade-replay/app.py` | **Modify** | 新增 `/api/subscribe` 路由 |
| `daytrade-replay/static/app.js` | **Modify** | `loadKbars` 呼叫 `/api/subscribe`；`startLiveRefresh` 間隔 60000→5000 |

---

## Task 1：新增 RealTimeFeed class 到 data.py

**Files:**
- Modify: `daytrade-replay/data.py`

`RealTimeFeed` 負責：
- 在記憶體維護目前訂閱的 stock_id
- 收到 tick 後組合 1 分 K 棒
- 提供 `get_bars(date_str)` 回傳 completed bars（不含 forming bar，避免前端顯示不完整 bar）

- [ ] **Step 1：在 `data.py` 頂部加入 import 和 RealTimeFeed class**

在 `data.py` 現有 `import` 區塊後、`CACHE_FILE = ...` 之前，加入以下程式碼：

```python
import threading
from datetime import time as _time

class RealTimeFeed:
    """
    訂閱 Shioaji tick 串流，在記憶體組合 1 分 K 棒。
    執行緒安全（_lock 保護所有狀態）。
    只保留「已完成」的 bar（minute 已結束），避免前端顯示不完整 bar。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._stock_id: str | None = None
        self._completed: list[dict] = []   # [{ts, open, high, low, close, volume}, ...]
        self._forming: dict | None = None  # 目前正在形成的 bar

    # ── 供外部呼叫 ────────────────────────────────────────────────────────────

    def set_stock(self, stock_id: str) -> None:
        """切換訂閱股票，清空舊資料。由 subscribe_realtime() 呼叫。"""
        with self._lock:
            if self._stock_id == stock_id:
                return
            self._stock_id = stock_id
            self._completed = []
            self._forming = None
        print(f'[feed] 切換訂閱股票 → {stock_id}')

    def on_tick(self, tick) -> None:
        """
        Shioaji on_tick_stk_v1 callback 呼叫此方法。
        tick 為 TickSTKv1 instance。
        """
        try:
            tick_dt = tick.datetime
            t = tick_dt.time()
            # 只處理盤中 09:00~13:30
            if t < _time(9, 0) or t > _time(13, 30):
                return

            # 取「分鐘開始」時間字串，例如 "2026-04-16 10:41"
            minute_str = tick_dt.strftime('%Y-%m-%d %H:%M')
            price  = round(float(tick.close), 2)
            volume = int(tick.volume)

            with self._lock:
                # 不是我們訂閱的股票，略過
                if self._stock_id and tick.code != self._stock_id:
                    return

                if self._forming is None:
                    # 第一根 bar
                    self._forming = _make_bar(minute_str, price, volume)
                elif self._forming['ts'] == minute_str:
                    # 同一分鐘，更新 forming bar
                    b = self._forming
                    b['high']   = max(b['high'],  price)
                    b['low']    = min(b['low'],   price)
                    b['close']  = price
                    b['volume'] += volume
                else:
                    # 新的一分鐘開始 → 把 forming bar 存入 completed
                    self._completed.append(dict(self._forming))
                    self._forming = _make_bar(minute_str, price, volume)
        except Exception as e:
            print(f'[feed] on_tick error: {e}')

    def get_bars(self, date_str: str) -> list[dict]:
        """回傳 date_str 當日所有已完成的 1 分 K bar（不含 forming bar）。"""
        prefix = date_str + ' '
        with self._lock:
            return [dict(b) for b in self._completed if b['ts'].startswith(prefix)]

    def current_stock(self) -> str | None:
        return self._stock_id


def _make_bar(ts: str, price: float, volume: int) -> dict:
    return {'ts': ts, 'open': price, 'high': price,
            'low': price, 'close': price, 'volume': volume}


# 全域單例
_realtime_feed = RealTimeFeed()
```

- [ ] **Step 2：驗證語法正確（不含 Shioaji 連線）**

```bash
cd /Users/steven/CCProject/daytrade-replay
./venv/bin/python -c "import data; f = data._realtime_feed; print('RealTimeFeed OK:', type(f))"
```
Expected output: `RealTimeFeed OK: <class 'data.RealTimeFeed'>`

---

## Task 2：修改 `_login()` 登記 tick callback

**Files:**
- Modify: `daytrade-replay/data.py`

Shioaji callback 是 per-api-instance。每次 `_login()` 都需要重新登記，才能確保重連後 tick 繼續進來。

- [ ] **Step 1：修改 `_login()` 函數，在 login 後立刻登記 callback**

找到 `data.py` 中的 `_login()` 函數：
```python
    def _login():
        load_dotenv(os.path.join(os.path.dirname(__file__), '../finmind/.env'))
        api = sj.Shioaji(simulation=False)
        api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        print('[sj] 永豐 API 登入成功')
        return api
```

改成：
```python
    def _login():
        load_dotenv(os.path.join(os.path.dirname(__file__), '../finmind/.env'))
        api = sj.Shioaji(simulation=False)
        api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        print('[sj] 永豐 API 登入成功')

        # 登記 tick callback（per-api-instance，重連後必須重新登記）
        @api.on_tick_stk_v1()
        def _on_tick_stk(exchange, tick):
            _realtime_feed.on_tick(tick)

        print('[sj] tick callback 已登記')
        return api
```

- [ ] **Step 2：新增 `subscribe_realtime(stock_id)` 公開函數**

在 `data.py` 的 `_get_sj()` 函數之後（或任何便利位置）加入：

```python
def subscribe_realtime(stock_id: str) -> None:
    """
    對指定股票開始 Shioaji tick 訂閱。
    切換股票時自動取消舊訂閱再訂新的。
    只在今日盤中才需要呼叫。
    """
    try:
        api = _get_sj()

        old_id = _realtime_feed.current_stock()
        # 取消舊訂閱（不同股票才需要）
        if old_id and old_id != stock_id:
            try:
                old_contract = api.Contracts.Stocks[old_id]
                api.quote.unsubscribe(
                    old_contract,
                    quote_type=sj.constant.QuoteType.Tick,
                    version=sj.constant.QuoteVersion.v1,
                )
                print(f'[sj] 取消訂閱 {old_id}')
            except Exception as e:
                print(f'[sj] 取消訂閱失敗: {e}')

        # 切換 feed 的目標股票（清空舊 bars）
        _realtime_feed.set_stock(stock_id)

        # 訂閱新股票
        contract = api.Contracts.Stocks[stock_id]
        api.quote.subscribe(
            contract,
            quote_type=sj.constant.QuoteType.Tick,
            version=sj.constant.QuoteVersion.v1,
        )
        print(f'[sj] 訂閱 {stock_id} tick 成功')
    except Exception as e:
        print(f'[sj] subscribe_realtime {stock_id} 失敗: {e}')
```

- [ ] **Step 3：驗證語法正確**

```bash
./venv/bin/python -c "import data; print('subscribe_realtime:', callable(data.subscribe_realtime))"
```
Expected: `subscribe_realtime: True`

---

## Task 3：修改 `get_1min_kbars()` 合併即時資料

**Files:**
- Modify: `daytrade-replay/data.py`

今日資料 = `_sj_stock_1min()` 歷史 bars（補足早盤至訂閱前）+ `_realtime_feed.get_bars()`（近即時），以 `ts` 去重合並，優先保留 tick-built bar（精度較高）。

- [ ] **Step 1：修改 `get_1min_kbars()` 加入合併邏輯**

找到現有的 `get_1min_kbars` 函數，將開頭的 `_sj_stock_1min` 呼叫段落改為：

```python
def get_1min_kbars(stock_id: str, date_str: str) -> list:
    """
    取指定股票指定日的1分K。
    今日：歷史 kbars() + 即時 tick bars 合併回傳。
    非今日：Shioaji kbars() 優先，失敗 fallback yfinance。
    """
    today_str = str(date.today())

    if date_str == today_str:
        # ── 今日：歷史 + 即時合併 ──────────────────────────────────────────
        hist_bars  = _sj_stock_1min(stock_id, date_str)   # up to ~20min ago
        live_bars  = _realtime_feed.get_bars(date_str)    # near real-time

        if not hist_bars and not live_bars:
            return []

        # 以 ts 為 key 合併，tick-built bars 優先（live 覆蓋 hist 相同 ts）
        merged: dict[str, dict] = {}
        for b in hist_bars:
            merged[b['ts']] = b
        for b in live_bars:
            merged[b['ts']] = b   # tick-built 覆蓋同 ts 的歷史 bar

        return sorted(merged.values(), key=lambda b: b['ts'])

    # ── 非今日：原邏輯不變 ────────────────────────────────────────────────
    bars = _sj_stock_1min(stock_id, date_str)
    if bars:
        return bars

    # Fallback: yfinance（最近7天）
    cache_path = f'/tmp/kbar_{stock_id}_{date_str}.json'
    if os.path.exists(cache_path):
        return json.load(open(cache_path))

    import yfinance as yf
    ticker = _yf_ticker(stock_id)
    try:
        df = yf.download(ticker, interval='1m', period='7d', progress=False, auto_adjust=True)
    except Exception as e:
        print(f'[yf] kbar {stock_id} 失敗: {e}')
        return []

    if df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df.index = df.index.tz_convert('Asia/Taipei')
    df = df[df.index.date == date.fromisoformat(date_str)]
    df = df.between_time('09:01', '13:30')
    if df.empty:
        return []
    df = df.reset_index()
    df.rename(columns={'Datetime': 'ts', 'Open': 'open', 'High': 'high',
                       'Low': 'low', 'Close': 'close', 'Volume': 'volume'}, inplace=True)
    df['ts'] = df['ts'].dt.strftime('%Y-%m-%d %H:%M')
    df['volume'] = df['volume'].fillna(0).astype(int)
    result = df[['ts', 'open', 'high', 'low', 'close', 'volume']].to_dict('records')
    json.dump(result, open(cache_path, 'w'), ensure_ascii=False)
    return result
```

> **注意**：舊 `get_1min_kbars` 的 `if date_str != today_str_yf` 快取判斷已移除，改為只快取「非今日」資料（if 段落現在直接 cache，不需判斷）。

- [ ] **Step 2：驗證語法正確**

```bash
./venv/bin/python -c "import data; print('get_1min_kbars:', callable(data.get_1min_kbars))"
```
Expected: `get_1min_kbars: True`

---

## Task 4：新增 `/api/subscribe` 路由到 app.py

**Files:**
- Modify: `daytrade-replay/app.py`

前端在載入今日 K 棒時呼叫此路由，觸發後端開始 tick 訂閱。

- [ ] **Step 1：在 `app.py` 的 `import data` 後加入路由**

在 `app.py` 的 `/api/index_data` 路由後面加入新路由：

```python
@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    body = request.get_json() or {}
    stock_id = body.get('stock', '')
    if not stock_id:
        return jsonify({'error': 'stock required'}), 400
    try:
        data.subscribe_realtime(stock_id)
        return jsonify({'ok': True, 'stock': stock_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```

- [ ] **Step 2：重啟 server 讓修改生效**

```bash
# Kill current process, LaunchAgent will restart it
pkill -f "daytrade-replay/app.py" 2>/dev/null; sleep 4
ps aux | grep "daytrade-replay/app.py" | grep -v grep
```
Expected: 看到新 PID，started time 是剛才的時間

- [ ] **Step 3：驗證 /api/subscribe 路由存在**

```bash
curl -s -X POST http://localhost:5400/api/subscribe \
  -H "Content-Type: application/json" \
  -d '{"stock":"2330"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d)"
```
Expected: `{'ok': True, 'stock': '2330'}` 或 `{'error': '...'}` — 只要不是 404 就表示路由正確。

---

## Task 5：前端修改（app.js）

**Files:**
- Modify: `daytrade-replay/static/app.js`

三處修改：
1. `loadKbars()` 中今日資料呼叫 `/api/subscribe`
2. `startLiveRefresh()` 間隔 60000 → 5000 ms
3. 更新等待狀態文字

- [ ] **Step 1：在 `loadKbars()` 中新增 subscribe 呼叫**

找到 `loadKbars` 函數中這段（約 line 383 附近）：
```javascript
  const data = await fetch(`/api/kbars?stock=${stockId}&date=${dateStr}`).then(r => r.json());
```

在這行**之前**插入：
```javascript
  // 今日資料 → 啟動後端 tick 訂閱
  if (isToday(dateStr)) {
    fetch('/api/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stock: stockId }),
    }).catch(() => {});  // 非同步，不阻塞載入
  }
```

- [ ] **Step 2：修改 startLiveRefresh 的間隔與狀態文字**

找到 `startLiveRefresh` 函數中的：
```javascript
  setStatus('等待新K棒...');
  liveRefreshTimer = setInterval(async () => {
```

改為：
```javascript
  setStatus('即時模式 — 等待新K棒...');
  liveRefreshTimer = setInterval(async () => {
```

再找到函數最底部的：
```javascript
  }, 60000);  // 每分鐘輪詢一次
```

改為：
```javascript
  }, 5000);  // 即時模式每 5 秒輪詢一次
```

- [ ] **Step 3：驗證 JS 修改後瀏覽器不報錯**

用 Playwright 開頁面確認無 console error：
```bash
# 確認 server 運行中
curl -s http://localhost:5400/ | grep -c "當沖回放"
```
Expected: `1`

---

## Task 6：整合驗證

- [ ] **Step 1：確認 server 正常運行且有 tick callback**

```bash
tail -20 /Users/steven/CCProject/daytrade-replay/server.log
```
Expected: 看到 `[sj] 永豐 API 登入成功`、`[sj] tick callback 已登記`

- [ ] **Step 2：呼叫 /api/subscribe 並確認訂閱成功**

```bash
curl -s -X POST http://localhost:5400/api/subscribe \
  -H "Content-Type: application/json" \
  -d '{"stock":"2330"}' && echo ""
tail -5 /Users/steven/CCProject/daytrade-replay/server.log
```
Expected log: `[feed] 切換訂閱股票 → 2330`、`[sj] 訂閱 2330 tick 成功`

- [ ] **Step 3：等 30 秒後確認 tick bars 有進來**

```bash
sleep 30
curl -s "http://localhost:5400/api/kbars?stock=2330&date=$(date +%Y-%m-%d)" | \
  python3 -c "
import sys, json, datetime
d = json.load(sys.stdin)
bars = d.get('bars', [])
if bars:
    last = bars[-1]['ts']
    lag = int((datetime.datetime.now() - datetime.datetime.strptime(last, '%Y-%m-%d %H:%M')).total_seconds() / 60)
    print(f'bars: {len(bars)}, last: {last}, lag: {lag} min')
"
```
Expected: `lag` < 5（代表 tick 即時資料進來）
若仍 ~20 → 表示 tick 訂閱尚未收到資料（可能盤中才有 tick 進來，非盤中時段正常）

- [ ] **Step 4：Playwright 開啟頁面，選今日股票，確認 live refresh 每 5 秒觸發**

用 Playwright MCP 開 `http://localhost:5400/`，選股票 + 今日日期，按播放到底，觀察 status bar 文字每 5 秒更新一次（「即時模式 — 等待新K棒...」），且 network tab 看到每 5 秒一次 `/api/kbars` 請求。

---

## 已知限制

- **Gap**：第一次載入今日資料時，`(now-20min)` 到 `subscription_start` 之間可能有空白（~0 到 20 分鐘），隨時間推進自動補齊（歷史 kbars 每分鐘往前推進）
- **Forming bar**：目前「正在形成中」的 bar 不顯示（等該分鐘結束後才出現），避免前端顯示不完整 K 棒
- **重連後訂閱**：Shioaji session 重連時 `_login()` 會自動重新登記 callback，但 `subscribe_realtime()` 不會自動重新訂閱舊股票。若重連後需繼續收 tick，前端下次 poll 時會自動帶舊資料，但 gap 會出現。可接受。
