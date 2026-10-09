# 持倉標的清單網頁管理 — 設計文件

- **日期**：2026-10-09
- **狀態**：設計已確認，待實作
- **作者**：Steven + Claude

## 1. 背景

`portfolio-news/portfolio_news.py` 每日兩次推播持倉新聞多空判斷（08:30 台股、21:00 美股）。使用者反映美股時段幾乎所有標的都顯示「過去 24 小時無相關新聞」。

### 根因（已實測）

`fetch_news()` 把 Google News RSS 的地區參數寫死為台灣版：

```python
f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
```

台股標的用這設定正確，但**拿英文關鍵字去搜台灣版**，Google 索引到的來源極少且排序偏舊，24 小時時間窗一過濾就全部落空。實測對照（2026-10-08）：

| 標的 | zh-TW 最新一則 | en-US 最新一則 |
|------|---------------|---------------|
| ServiceNow | 168 小時前 | 1 小時前 |
| Adobe | 366 小時前 | 1 小時前 |
| Alphabet | 1206 小時前 | 20 小時前 |
| 美光 | 52 小時前 | 0 小時前 |
| NVIDIA | 22 小時前 | 23 小時前 |

反之，中文商品新聞（石油 17 則、黃金 5 則）只在台灣版有量，切到美國版反而變 0。**兩個地區互補，不是二選一。**

另有 8 檔冷門 ETF（VWO／EFV／XLP／SLVP／EWJ／XOVR／AGIX／AIPO）兩版本都幾乎抓不到，屬關鍵字選詞問題。

### 第二個痛點

標的清單寫死在原始碼（第 36–192 行）。使用者要調整標的或關鍵字必須改程式碼，且**改完不知道有沒有效**，要等隔天推播才發現還是空白。

## 2. 目標 / 非目標

### 目標

- 標的清單抽離原始碼，成為可編輯的資料檔。
- 提供網頁介面（整合進 command-center）供使用者自行新增／刪除／修改標的。
- 每檔標的可指定新聞搜尋地區（`locale`），修正上述根因。
- 網頁提供「即時測試」按鈕：存檔前就能看到該檔在兩個地區各抓到幾則。

### 非目標（YAGNI）

- 不改動既有推播排程、LaunchAgent 或通知格式。
- 不做標的的歷史績效統計、不接即時報價。
- 不做多使用者或權限系統（沿用 command-center 既有的 BasicAuth）。
- 不自動猜測或建議關鍵字（只呈現實測結果，判斷留給使用者）。

## 3. 架構

```
config/portfolio_holdings.json   ← 單一真實來源
     ↑ 讀寫                        ↓ 讀
command-center (5950)        portfolio_news.py（排程 08:30 / 21:00）
  /portfolio-holdings 頁面         └─ probe 子命令（供測試鈕呼叫）
```

放在 `config/` 是沿用既有慣例 —— 同目錄已有 `ma_watchlist.json`，模式完全一致（command-center 編輯、腳本讀取）。

## 4. 資料檔 schema

`config/portfolio_holdings.json`：

```json
{
  "tw": [
    {
      "code": "2330",
      "name": "台積電",
      "locale": "zh-TW",
      "individual": true,
      "queries": ["台積電 TSMC", "TSMC semiconductor earnings"]
    }
  ],
  "us": [
    {
      "code": "NVDA",
      "name": "NVIDIA",
      "locale": "en-US",
      "queries": ["NVIDIA NVDA stock AI chip earnings"]
    },
    {
      "code": "UKOIL",
      "name": "布蘭特原油",
      "locale": "zh-TW",
      "queries": ["Brent crude oil price OPEC", "原油 布蘭特"]
    }
  ]
}
```

| 欄位 | 型別 | 說明 |
|------|------|------|
| `code` | str | 代號。台股為 4 位數字或 ETF 代號；美股為 ticker |
| `name` | str | 顯示名稱 |
| `locale` | `"en-US"` \| `"zh-TW"` | 新聞搜尋地區。新增欄位 |
| `individual` | bool，選填 | 是否追蹤法人籌碼。僅台股個股適用，預設 false |
| `queries` | list[str] | Google News 搜尋關鍵字，1～5 組 |

### 遷移的 locale 指派

依 2026-10-08 實測結果：

| 群組 | 檔數 | locale | 依據 |
|------|------|--------|------|
| 台股全部 | 6 | `zh-TW` | 台股新聞以中文媒體為主 |
| 美股個股（NOW～SNDK） | 9 | `en-US` | 實測 zh-TW 抓到的新聞多為 100+ 小時前 |
| UKOIL、GOLD、XLU | 3 | `zh-TW` | 中文新聞分別有 17／5／1 則，切 en-US 反而變 0 |
| 冷門 ETF | 8 | `en-US` | 兩版本皆接近 0，暫定 en-US 待使用者用測試鈕調整 |

台股 6 + 美股個股 9 + 商品與公用事業 3 + 冷門 ETF 8 = 現有 26 檔。

## 5. `portfolio_news.py` 改動

1. **移除寫死的 `TW_HOLDINGS` / `US_HOLDINGS`**（第 36–192 行），改為啟動時讀 `config/portfolio_holdings.json`。檔案不存在或格式錯誤時，記錄明確錯誤並結束（不靜默使用空清單 —— 避免把「設定檔壞了」誤判成「今天沒新聞」）。
2. **`fetch_news(queries, locale)`** — 把寫死的地區參數（第 205 行）改為由 `locale` 參數決定：
   - `zh-TW` → `hl=zh-TW&gl=TW&ceid=TW:zh-Hant`
   - `en-US` → `hl=en-US&gl=US&ceid=US:en`
3. **新增 `probe` CLI 子命令** — 從 stdin 讀入單一標的的 JSON（`{"name", "queries", "locale"}`），同時以 `en-US` 與 `zh-TW` 各抓一次，輸出 JSON 結果至 stdout：

```json
{
  "en-US": {"within_24h": 3, "within_48h": 8, "titles": [{"title": "...", "age_hours": 1.1}]},
  "zh-TW": {"within_24h": 0, "within_48h": 0, "titles": []}
}
```

`titles` 取最新 3 則。此子命令為 command-center 與 portfolio-news 之間的唯一介面，兩者不互相 import（portfolio-news 有獨立 venv，且其模組頂層會讀 secrets，import 會產生耦合與副作用）。

## 6. `command-center` 改動

| 檔案 | 改動 |
|------|------|
| `app.py` | 新增 4 個端點（見下） |
| `templates/portfolio_holdings.html` | 新頁面，照 `ma_watchlist.html` 的模式（vanilla JS + `fetch`） |
| `templates/index.html` | 工具區加一張卡片連到新頁 |

### 端點規格

| 方法 | 路徑 | 說明 |
|------|------|------|
| `GET` | `/api/portfolio-holdings` | 回傳資料檔內容。檔案不存在回傳空結構 |
| `POST` | `/api/portfolio-holdings` | 覆寫資料檔。Pydantic 驗證 + 原子寫入（`tempfile` + `os.replace`），與 `set_ma_watchlist` 同模式 |
| `POST` | `/api/portfolio-test` | 收單一標的設定，以 subprocess 呼叫 `portfolio_news.py probe`，回傳兩地區抓取結果 |
| `GET` | `/portfolio-holdings` | 回傳管理頁面 HTML |

驗證規則：`locale` 僅接受 `en-US` / `zh-TW`；`queries` 至少 1 組、最多 5 組，每組去空白後不得為空；`code` 不得重複；美股上限 50 檔、台股上限 50 檔。

**不動 `PROXY_PORTS`** —— 該名單是唯讀白名單（原始碼註解明說會轉發所有方法含 POST）。新頁面位於 command-center 本體內，直接受 `BasicAuthMiddleware` 保護。

### 測試鈕行為

按下後呼叫 `/api/portfolio-test`，在該列下方展開結果面板，並列顯示兩個地區的「24h: N 則 / 48h: M 則」與前 3 則標題（含幾小時前）。使用者據此判斷該用哪個地區、關鍵字是否夠力。

## 7. 驗證計畫

1. **`portfolio_news.py`**：改完實跑 `.venv/bin/python portfolio_news.py us`，比對新聞量是否從近乎全 0 變為多數標的有值（對照本文件第 1 節的實測基準）。
2. **Import 驗證**：`python3 -c "import portfolio_news"` 無 ImportError。
3. **`probe` 子命令**：直接以 stdin 餵入 NVDA 設定，確認兩個地區都有結果。
4. **command-center**：重啟服務後 `curl /api/health`，再 `curl /api/portfolio-holdings`。
5. **網頁**：Playwright 實測新增標的、修改關鍵字、刪除標的、按測試鈕。
6. **code-review**：全部改完後執行。

## 8. 風險

| 風險 | 處理 |
|------|------|
| 資料檔損壞導致推播靜默失效 | 讀檔失敗時明確報錯結束，不 fallback 成空清單（此坑已有既有教訓） |
| subprocess 呼叫的參數注入 | 以 list 傳參、不用 `shell=True`；標的設定經 stdin 以 JSON 傳入 |
| 測試鈕被連點造成 Google News 請求過多 | 前端按鈕按下後停用至回應為止 |
| 改動讓標的清單與文件描述不同步 | 完成後更新 memory 的 `project_portfolio_news.md` |
