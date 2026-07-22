# Rabbit Hole Hay 軟纖補貨監控 — 設計

日期：2026-07-22

## 目標

盯 **Rabbit Hole Hay（兔子洞）軟纖提摩西**在台灣通路的補貨狀態，當品項從「缺貨 → 有貨」時透過 Telegram 即時通知 Steven。**只通知，絕不自動下單**（見 [[feedback_no_auto_purchase]]）。

取代先前失效的蝦皮單一店家監控（蝦皮擋自動化）。改盯真正在賣 RHH 軟纖、且無反爬的專門店。

## 監控範圍

- **品項**：只篩 RHH **軟纖（Soft）**提摩西，任何尺寸/包裝都算；中纖/粗纖/其他草不推。
- **平台（3 家專門店，已實測可爬且有明確庫存訊號）**：
  | 店家 | 網域 | 平台 | 庫存判斷 |
  |------|------|------|---------|
  | 魏啥麻寵物文創（官方代理） | weyyngbuy.com | 自訂 | `instock`/`缺貨` 標記 |
  | 豬寶窩窩 | piggybabiesbnb.com | WooCommerce | `instock`/`outofstock` class（最乾淨） |
  | 牧草園 | mucaoyun.com | 自訂 | `售完/缺貨/補貨/已售完/soldout` |
- **不納入**：蝦皮（擋自動化）、露天/momo/PChome（實測不賣 RHH，只有撞名的「兔草窩」）。

> **範圍決定（2026-07-22）**：首版只做**豬寶窩窩 + 魏啥麻**（都能可靠靜態解析）。**牧草園延後**——其變體分級是 JS 渲染，靜態抓不到各級庫存，需 headless（playwright），不讓最難的站卡住整個功能。架構保留易於新增 adapter。

## 架構

`~/CCProject/scripts/hay_monitor.py`（仿 `steamdeck_monitor.py` 架構）

- **3 個來源 adapter**：`fetch_weyyngbuy()`、`fetch_piggybabies()`、`fetch_mucaoyun()`
  - 各自 requests 抓頁面 + 解析，回傳 `list[dict]`：`{shop, title, variant, price, in_stock, url}`
  - 只保留 title/variant 含「軟纖 / soft」的品項
- **狀態** `scripts/hay_seen.json`：記錄上次「有貨」的品項 key（`shop|url|variant`）集合。
  - 首次執行建 baseline 不推。
  - 之後只在「本次有貨、上次不在有貨集合」時推播（缺→有 的轉換）。
  - 一直有貨的品項不重複推。
- **推播**：Telegram（chat_id 7556217543），訊息含 店家 / 品項 / 價格 / 連結。命中才推，全缺貨不推。
- **面板輸出** `scripts/hay_current.json`：`{updated, items:[{shop,title,price,in_stock,url}]}`，給 command-center 讀。
- **log**：`~/CCProject/logs/hay_monitor.log`

## command-center 整合

原 Steam Deck 卡片改為軟纖：
- `templates/index.html` L293 卡片設定：`{id:'hay', ic:'🌾', title:'RHH 軟纖補貨', span:'wide', url:'/api/hay', ...}`；`renderSteamdeck` → `renderHay`（顯示各店有貨/缺貨 + 連結）
- `app.py`：`/api/steamdeck` → `/api/hay` → `sources.hay()`
- `sources.py`：`steamdeck()` → `hay()`，讀 `hay_current.json`
- 移除舊 `steamdeck_current.json`

## 排程

crontab：`0 8,10,12,14,16,18,20,22 * * *`（每天 8 次）用專用 venv（requests + bs4；不需 playwright，3 家都能靜態抓）。改 crontab 用 python subprocess（見 [[lesson_rtk_pipe_mangling]]）。

## 測試（TDD）

- 存 3 家頁面 HTML 為 fixture，單元測試各 adapter 的「軟纖篩選 + 有貨/缺貨判斷」。
- 測狀態邏輯：baseline 不推、缺→有 推一次、持續有貨不重複推、有→缺不推。
- 各 adapter 對「抓取失敗」要容錯（單一站掛掉不影響其他站，log 記錄）。

## 非目標

- 不自動下單 / 加購物車 / 結帳。
- 不監控大型市集（RHH 不在那賣）。
- 不做價格歷史圖表（只做有貨通知）。

## 相關

[[project_steamdeck_monitor]]（架構範本，git HEAD 仍有原碼）、[[project_shopee_hay]]（前身，蝦皮已棄）、[[project_command_center]]（面板）、[[feedback_no_auto_purchase]]、[[lesson_rtk_pipe_mangling]]
