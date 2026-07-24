# sim-invest 模擬投資台美股 — 設計規格

**日期**:2026-07-24
**狀態**:設計定案，待實作計畫
**擁有者**:Steven

## 1. 目標與範圍

建立一個**前瞻式模擬投資（paper trading）服務**，把兩套既定的資產配置當成虛擬真金下單、每日追蹤市值與再平衡訊號。**全程虛擬，絕不觸發任何真實下單。**

- 模式:**前瞻模擬** — 從建帳日（今天）用即時價建倉，之後每日以收盤價更新淨值，往前追蹤績效。
- 兩個**獨立**帳戶,合計虛擬資金 1,000 萬台幣:
  - 帳戶 A|900 萬 全配置(平穩核心 + AI 基建衛星)
  - 帳戶 B|100 萬 AI Agent 2.0(5 檔個股)
- 沿用現有生態:Flask + SQLite,做成 command-center(5950)的一張卡。

### 不做（YAGNI）
- 不接真實券商、不下真單。
- 不做台股食品股/黃金存摺對防禦板塊的替代（防禦一律用美股 ETF + 00864B）。
- 不做多使用者、不做登入。

## 2. 最終配置（定案）

### 帳戶 A|900 萬

| 標的 | 市場 | 類型 | 區塊 | 金額(台幣) | 目標比例 | 建倉方式 |
|---|---|---|---|---|---|---|
| 00864B | TW | 債券ETF | 短債防禦(錨) | 1,000,000 | 11.11% | 一次建倉 |
| XLP | US | ETF | 穩健防禦 | 900,000 | 10.00% | 6 個月 DCA |
| XLU | US | ETF | 穩健防禦 | 900,000 | 10.00% | 6 個月 DCA |
| GLD | US | 黃金ETF | 穩健防禦 | 900,000 | 10.00% | 6 個月 DCA |
| EFV | US | ETF | 全球進攻 | 1,413,333 | 15.70% | 6 個月 DCA |
| EWJ | US | ETF | 全球進攻 | 1,177,778 | 13.09% | 6 個月 DCA |
| VWO | US | ETF | 全球進攻 | 942,222 | 10.47% | 6 個月 DCA |
| 0050 | TW | ETF | 全球進攻 | 588,889 | 6.54% | 6 個月 DCA |
| BE | US | 個股 | AI 基建衛星 | 412,222 | 4.58% | 一次建倉 |
| SNDK | US | 個股 | AI 基建衛星 | 353,333 | 3.93% | 一次建倉 |
| CORZ | US | 個股 | AI 基建衛星 | 117,778 | 1.31% | 一次建倉 |
| IREN | US | 個股 | AI 基建衛星 | 117,778 | 1.31% | 一次建倉 |
| CRWV | US | 個股 | AI 基建衛星 | 176,667 | 1.96% | 一次建倉 |
| **合計** | | | | **9,000,000** | **100%** | |

- 一次建倉(建帳日):00864B + 5 檔個股 = **24.2%**
- 6 個月 DCA:7 檔 ETF(XLP/XLU/GLD/EFV/EWJ/VWO/0050)= **75.8%**
- 防禦力(00864B + XLP + XLU + GLD)= **41.1%**（Steven 確認採進攻傾向）
- 金額四捨五入到元,總和誤差 ±1 元;**下單以「目標比例」為準,股數由建倉當日現價換算**。

### 帳戶 B|100 萬 AI Agent 2.0

| 標的 | 市場 | 金額(台幣) | 目標比例 | 建倉方式 |
|---|---|---|---|---|
| CRM | US | 350,000 | 35% | 一次建倉 |
| MSFT | US | 250,000 | 25% | 一次建倉 |
| NOW | US | 150,000 | 15% | 一次建倉 |
| AAPL | US | 150,000 | 15% | 一次建倉 |
| PLTR | US | 100,000 | 10% | 一次建倉 |
| **合計** | | **1,000,000** | **100%** | |

## 3. 系統架構

新服務目錄 `sim-invest/`,Flask + SQLite,port **5250**,並掛進 command-center(5950)。

```
sim-invest/
  app.py          # Flask：API + 頁面
  store.py        # SQLite 存取（唯一寫入點）
  quotes.py       # 報價層：TW→Shioaji、US→yfinance、USD/TWD 匯率
  engine.py       # 引擎：建倉、DCA、每日快照、再平衡檢查
  plans.py        # 兩套 plan 的定義（上表，程式內常數）
  jobs/daily.py   # 每日排程進入點（快照 + 到期 DCA）
  templates/index.html
  sim.db
```

### 模組職責（可獨立測試）
- **quotes.py**:`get_quote(ticker, market) -> price_native`、`get_fx() -> usdtwd`。TW 走 Shioaji（行情只用 Shioaji），US 走 yfinance。帶記憶體/DB 快取。
- **plans.py**:回傳 plan 目標（標的、市場、區塊、目標比例、建倉方式）。純資料,無副作用。
- **engine.py**:純計算 + 呼叫 store。
  - `build_lump(account, date)`:一次建倉部位（帳戶 B 全部、帳戶 A 的 00864B + 5 檔個股）。
  - `run_dca_tranche(account, date, tranche_no)`:投入該批 1/6 金額,依當日現價換算股數。
  - `daily_snapshot(account, date)`:以當日收盤價計算總市值、各區塊市值、未實現損益,寫入 `nav_snapshots`。
  - `check_rebalance(account, date)`:每半年檢視,回傳偏離訊號。
- **store.py**:所有 SQLite 讀寫的唯一入口。
- **app.py**:Flask,只做 HTTP 與模板渲染,商業邏輯全在 engine。

## 4. 資料模型（SQLite）

- `accounts(id, name, plan_id, mode, start_date, initial_capital_twd)`
- `plan_targets(plan_id, ticker, market, category, target_pct, build_method, dca_months)`
- `trades(id, account_id, date, ticker, market, shares, price_native, fx_rate, cost_twd, tranche_no)`
- `distributions(id, account_id, date, ticker, amount_twd)` — ETF/債券配息
- `nav_snapshots(account_id, date, total_value_twd, cash_twd, unrealized_pnl_twd, by_category_json)`
- `prices_cache(ticker, date, close_native)`

## 5. DCA 與時間推進

- **真實逐月自動**:6 批,每批投入該部位目標金額的 1/6。
- 第 1 批於**建帳日**投入,其後**每月同一日**各投一批,共 6 批。
- 排程:LaunchAgent 每日跑 `jobs/daily.py` → (a) 若今日為 DCA 扣款日則投入該批;(b) 產生當日淨值快照。
- 一次建倉部位於建帳日全數投入。

## 6. 再平衡（對應原計畫步驟三）

- 每半年（建帳日起每 180 天）檢視一次。
- 觸發訊號（僅提示,不自動執行）:
  - 任一區塊實際比例偏離目標 **±5 個百分點**,或
  - AI 衛星市值達其目標金額的 **1.5 倍** → 提示減碼、獲利了結回補。
- 訊號顯示於儀表板 + 可選 Telegram 推播。

## 7. 報價與幣別

- TW（00864B、0050）:Shioaji,台幣計價,免換匯。
- US（其餘全部）:yfinance 收盤價（美元）× USD/TWD 匯率 → 台幣市值。
- 更新頻率:**每日美股收盤後（台灣時間早上）一次**淨值快照。
- 00864B 季配息:**配息計入帳戶收益（累積為現金）,預設不自動再投入**。〔可於實作前調整〕

## 8. 前端

- 每個帳戶一個儀表板:
  - 配置總表:目標比例 vs 實際比例 + 偏離、持股股數、成本、現值、未實現損益。
  - 淨值曲線（時間序列）。
  - DCA 進度:第 x/6 批、下次扣款日。
  - 再平衡警示區。
- 風格沿用 command-center 的 Anthropic 暖調品牌;掛一張卡進 5950。

## 9. 測試（TDD，目標覆蓋率 80%+）

- 單元:`engine`（DCA 分批金額/股數換算、比例→股數、再平衡偏離判斷、快照市值計算）、`plans`（比例加總=100%、金額加總=帳戶本金）、`store`。
- 報價以 mock 隔離（不打真實 Shioaji/yfinance）。
- 整合:建帳→數批 DCA→快照 的端到端流程用假報價驗證市值正確。

## 10. 待實作前可調參數（預設值）

| 參數 | 預設 |
|---|---|
| CORZ / IREN | 各半（9萬 base，等比放大後各 117,778） |
| DCA 扣款日 | 建帳日起每月同一日,共 6 批 |
| 再平衡門檻 | 區塊 ±5pp 或 衛星達 1.5 倍 |
| 00864B 配息 | 計入現金,不自動再投入 |
| 帳戶 B 建倉 | 全部一次建倉 |
| 淨值更新 | 每日美股收盤後一次 |
