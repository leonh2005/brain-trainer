# 支撐位偵測功能 設計文件

日期：2026-08-20

## 背景 / 目的

Steven 想在既有的候選股票清單（隔日沖候選、自選股均線警報、回後買上漲選股）上，直接查到每檔股票的支撐位分析，不用另外開一個獨立的查詢工具。支撐位判斷邏輯由 Steven 提供，涵蓋波段低點、趨勢線、均線、整數關卡、K線止跌形態、成交量確認共 6 種訊號。

## 使用情境

搭配現有候選清單使用，不是獨立的「輸入任意股票代碼查詢」工具：
1. 隔日沖候選（command-center swing 卡片）
2. 自選股（command-center 均線警報卡片，讀 `ma_monitor_state.json` 的 STOCKS 清單）
3. 回後買上漲選股（目前是獨立 App `pullback-buy-screener` port 5960，本次一併整合進 command-center 成為卡片）

## 架構總覽

新增共用模組 `command-center/support.py`，被 `command-center/sources.py` import。輸入股票代碼，透過 `shioaji-gateway`（5455）的 `/daily_ohlcv?code=XXX&days=250` 抓近 250 天日K（MA250 與長期波段低點都需要這個長度），跑完 6 種訊號偵測後回傳支撐位列表。

三個候選清單共用同一個新端點 `GET /api/support?code=XXX`（5分鐘快取，避免同一股票被多張卡片重複打 gateway），前端點股票名稱彈出小視窗顯示支撐位詳情。

### 輸出格式

```json
{
  "ok": true,
  "code": "3008",
  "levels": [
    {
      "price": 5290.0,
      "strength": 4,
      "sources": ["波段低點×3次(8/12,8/14,8/18)", "MA60", "量能確認"]
    },
    {
      "price": 5070.0,
      "strength": 2,
      "sources": ["波段低點×2次", "整數關卡(近5000)"]
    }
  ]
}
```

- `strength`：1-5 分，命中訊號數量加權加總（含量能確認加分），前端用星星數顯示。
- `sources`：純文字明細陣列，說明這個支撐位是由哪些訊號組成。
- 資料不足時個別訊號直接跳過，不強算（見「錯誤處理」）。

## 6 種訊號偵測邏輯

所有偵測函式都吃同一份日K陣列（`[{date,open,high,low,close,volume}]`，舊到新，250天）。

### 1. 波段低點水平支撐
- 用 rolling window（前後各5天）找局部低點（local minima）。
- 把價位相近（±2%）的低點分到同一群，群組內出現 ≥2 次才算有效支撐。
- 群組內若有 K 線是長下影線／錘子線／十字星（見訊號5），且該日量比5日均量放大（見訊號6），強度加分。

### 2. 上升趨勢線支撐
- 取最近 2～3 個波段低點做線性回歸畫趨勢線。
- 檢查現價是否在趨勢線上方且趨勢線斜率為正。
- 若近期收盤明顯跌破趨勢線（收盤價低於線下方一定幅度）且量能配合，標記「趨勢線可能已失效」，不列為有效支撐。

### 3. 均線動態支撐
- 算 MA20/60/120/250。
- 只在「均線本身向上」且「現價在均線上方」時列為有效支撐。
- 檢查現價是否曾在均線附近出現止跌 K 線（見訊號5），有的話強度加分。

### 4. 整數關卡
- 依現價量級自動選單位（5/10/50/100），抓現價附近上下的整數價位。
- 若歷史上該價位附近有留下長下影線，強度加分。

### 5. K線止跌形態（輔助訊號，不獨立成支撐位）
- 偵測近10天內的錘子線／倒錘子線、看漲吞沒、早晨之星、十字星、長下影線密集區。
- 作為輔助訊號附加在最近的支撐位（訊號1/2/3/4其中之一）上。

### 6. 成交量確認（強度加權，不獨立成訊號）
- 對每個支撐位，檢查價格觸及當天的量是否 ≥5日均量。
- 符合則該支撐位 `strength` 加分。

## 回後買(5960)整合 + LaunchAgent

目前 `pullback-buy-screener` 是手動點「開始掃描」才產生結果，不會存檔，無法比照 swing/daytrade 被動讀檔顯示。

- 新增 `pullback-buy-screener/daily_scan.py`：直接呼叫既有的 `screener.scan()`（不透過 HTTP，避免要先啟動 Flask），把結果寫到 `/tmp/pullback_candidates.json`（格式比照 `swing_candidates.json`：`{date, results:[...]}`）。
- 新增 LaunchAgent `com.steven.pullback-daily-scan.plist`，週一到五 14:00（收盤後）執行 `daily_scan.py`。
- `command-center/sources.py` 新增 `pullback()` function，讀取 `/tmp/pullback_candidates.json`，比照 `swing()`/`daytrade()` 模式補上 sector、支撐位摘要。
- `command-center/templates/index.html` 的 SIGNALS 清單新增「回後買候選」卡片，樣式沿用 `renderSwing` 的 pill 排版。

## UI：支撐位彈窗

- `sources.py` 新增 `/api/support?code=XXX` 端點（5分鐘快取）。
- 前端把 swing／daytrade／pullback／均線警報 卡片裡的股票名稱從純連結改成「可點擊觸發彈窗」，原本點連結開 TradingView 的行為分開（股名文字點擊開彈窗，旁邊保留小圖示/既有連結點去 TradingView），避免點擊行為衝突。
- 彈窗顯示支撐位列表，每項顯示價位、星等（★數＝strength）、來源明細文字（`sources` 陣列逐行列出）。

## 錯誤處理 / 邊界情況

- 抓不到日K（gateway 掛掉、新股資料不足250天）→ `/api/support` 回傳 `{ok:false, error:...}`，前端彈窗顯示「資料不足，無法分析」，不讓整張卡片壞掉。
- 均線/波段低點需要足夠天數（如 MA250 至少要 250 根資料），資料不足時該項目直接跳過，不強算、不報錯中斷整體流程。
- pullback 每日掃描若當天執行失敗（`daily_scan.py` 例外），沿用前一天的 `/tmp/pullback_candidates.json`，command-center 卡片一樣能顯示（舊資料），並把錯誤寫進 log。

## 測試方式

- `support.py` 各偵測函式用真實歷史資料（挑幾檔已知有明顯支撐/趨勢的股票，如先前驗證過的 3008、3034）手動跑一次核對合理性，不建立正式 unit test framework（技術分析邏輯難定義「正確答案」，人工核對比較實際）。
- LaunchAgent 部署後跑 `launchctl kickstart` 驗證有正常寫出 `/tmp/pullback_candidates.json`。
- 全部串起來後用 `curl /api/support?code=3008` 確認格式正確，再用 Playwright 開 command-center 點擊彈窗確認 UI 正常運作、資料不足時的錯誤訊息也正常顯示。

## 待辦排序建議（供後續 writing-plans 使用）

1. `support.py` 6 種訊號偵測邏輯 + 手動核對
2. `sources.py` `/api/support` 端點 + 快取
3. 前端支撐位彈窗 UI
4. `pullback-buy-screener/daily_scan.py` + LaunchAgent
5. `sources.py` `pullback()` + command-center 新卡片
6. 全部串接後的整合驗證（curl + Playwright）
