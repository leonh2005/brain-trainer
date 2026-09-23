# Jev context 壓縮 proxy — 實作計畫

> 自動排程任務產出（2026-09-24 05:00）。決策點集中在最後一節，需要 Steven 拍板。

## 目標

在 Claude Code 與上游 API 之間加一層，於每次送出請求前，把**已累積但不再相關的舊 tool 輸出**替換成一行移除通知，降低 input token。

實測數據（`result-*.json`）：對一段橫跨 170 個主題的真實對話，可壓縮掉 **88.8%** 的舊 tool 輸出字元（約 12.4 萬 tokens），判斷成本 $0.005。

## 現況

| 元件 | 位置 | 狀態 |
|---|---|---|
| `anonymize.mjs` | jev-compaction/ | 隱私處理（憑證遮蔽 15 條 + 偽名化），三輪 review 通過 |
| `compact_test.mjs` | jev-compaction/ | 離線驗證腳本（讀 transcript 模擬），已驗證壓縮有效性 |
| `thinking-proxy/proxy.py` | thinking-proxy/ | 現役 proxy（port 8787），Python，改寫 thinking 參數後轉發 |
| Claude Code 鏈路 | — | `Claude Code → 8787 → api.deepseek.com/anthropic` |

## 架構設計

### 為何是獨立 proxy 而非擴充 thinking-proxy

thinking-proxy 的職責是「思考開關」，與「context 壓縮」無關。混在一起會讓兩個功能的失敗互相牽連——壓縮若出錯，thinking 也跟著壞。獨立成一支，壞了可以直接從鏈路移除。

### 建議鏈路

```
Claude Code (ANTHROPIC_BASE_URL)
      │
      ▼
  jev-compaction proxy  :8788   ← 新增：壓縮舊 tool_result
      │
      ▼
  thinking-proxy        :8787   ← 現役，不動
      │
      ▼
  api.deepseek.com/anthropic
```

理由：壓縮應該在**最靠近來源**的位置做，這樣 thinking-proxy 收到的是已瘦身的請求。且 8788 掛掉時，只要把 `ANTHROPIC_BASE_URL` 改回 8787 即可完全繞過（fail-open 的操作面）。

### 與 thinking-proxy 的技術共用

沿用同一套骨架（`http.server` + `ThreadingHTTPServer` + SSE 轉發 + 「收到 `message_stop` 或完整 JSON 即結束」的判斷邏輯），但**不共用程式碼**——兩支 proxy 的轉發目標不同（8788 要轉給 8787 而非直接對外），硬抽共用反而讓兩邊都難改。

## 壓縮邏輯

### 候選選擇

在 `messages` 陣列中，`tool_result` 區塊只出現在 `role: "user"` 的訊息裡（Anthropic 格式）：

```json
{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_...","content":"..."}]}
```

規則（對齊 LiteLLM 的 typesafe guardrail）：

1. **永不觸碰** `system` 訊息
2. **永不觸碰** 最後一個 `role: "user"` 訊息（那是當下的提問）
3. **保護最近一輪**：從陣列尾端往前，第一組含 `tool_result` 的訊息整組保留
4. 其餘 `tool_result` 為候選，但**原始內容需 ≥ 200 字元**（太短不值得壓）
5. `tool_use_id` 與區塊結構必須保留（只換 `content` 欄位），否則上游會拒絕

### 判斷方式

對每批候選呼叫 Jev（`noul` 機率），問「這筆工具輸出是否仍是回答 current_task 所需的資訊」。

- `current_task` = 最後一個 user message 的文字內容
- 門檻 0.2（LiteLLM 預設值）；低於門檻者替換為：
  `[Tool result removed by TypeSafe compaction: judged no longer relevant to the current task]`
- 批次大小 25（`compact_test.mjs` 實測值）

### 效能設計（關鍵）

每次請求都呼叫 Jev 會增加延遲（每批 100–500ms）。三道防線：

1. **內容 hash 快取**：`sha256(內容) → 機率`。同一個 tool_result 內容不變，第二次之後直接命中快取。實際使用中，被壓縮過的內容會在下一次請求被替換掉，所以快取命中率主要來自「未達門檻而保留」的內容。
2. **只評最近的 N 筆**：超過視窗的極舊內容不再評分（它們多半早已被壓縮過）
3. **fail-open**：Jev 逾時（>3s）或錯誤 → 直接送原請求，不阻塞

### 隱私處理

送出給 Jev 前，**每個候選**與 **current_task** 都必須走 `applyPrivacy`：

- 憑證遮蔽（15 條 regex）
- 偽名化：`/Users/steven/...` → `<PATH_n>`、`localhost:port` → `<HOST_n>`、專案名 → `<PROJECT_n>`
- 只送前 600 字元（判斷相關性足夠，實測驗證過）

**Jev 只回傳機率、不生成文字**，所以偽名化的對照表不需要還原——這是整個方案能成立的基礎。

## 實作步驟

1. `privacy.py` — 移植 `anonymize.mjs` 的邏輯（憑證遮蔽 + 偽名化 + 專案名字典）
2. `jev_client.py` — 呼叫 `api.typesafe.ai/v1/systemone`，含逾時、重試、fail-open
3. `compactor.py` — 候選選擇 + 批次判斷 + 替換（純函式，好測試）
4. `proxy.py` — HTTP 骨架（從 thinking-proxy 改）
5. `test_compactor.py` — 單元測試（不呼叫真 API）

## 測試計畫

| 層級 | 內容 |
|---|---|
| 單元 | 候選選擇正確（保護最近一輪、跳過 system、跳過短內容） |
| 單元 | 替換後結構合法（tool_use_id 保留、content 為字串） |
| 單元 | privacy 模組輸出無殘留（路徑/localhost/專案名皆為 0） |
| 整合 | 用真實 transcript 重放，比對壓縮前後 token 數 |
| 端到端 | 啟動 proxy，用假的 messages 請求驗證回應正常轉發 |
| 人工 | **不由我執行**：接上真實鏈路後，Steven 自己確認對話正常 |

## 風險

| 風險 | 影響 | 緩解 |
|---|---|---|
| 壓縮掉其實還需要的內容 | 回答品質下降 | 門檻保守（0.2）、只壓 ≥200 字元、fail-open |
| Jev 服務中斷 | 請求變慢或失敗 | 逾時 3s + 直接送原請求 |
| proxy 本身掛掉 | **Claude Code 完全不能用** | 8788 獨立，改回 8787 即繞過 |
| 隱私 | 對話內容離開本機 | 偽名化 + 憑證遮蔽（決策點見下） |

---

## 實作結果（2026-09-24 05:0x 完成）

| 檔案 | 行數 | 內容 |
|---|---|---|
| `privacy.py` | 178 | 憑證遮蔽 15 條 + 偽名化（移植自 anonymize.mjs） |
| `jev_client.py` | 126 | TypeSafe API client（純標準庫 urllib） |
| `compactor.py` | 230 | 候選選擇 + 批次判斷 + 替換 |
| `proxy.py` | 205 | HTTP 骨架，監聽 8788，轉發至 8787 |
| `test_compactor.py` | 218 | 24 個單元測試 |

### 驗證結果

| 項目 | 結果 |
|---|---|
| 單元測試 | **24/24 通過**（候選選擇、結構替換、隱私無殘留、fail-open） |
| 真實對話整合 | 2342 則訊息、120 筆候選 → **移除 104 筆、省 82.2%**（136,705 字元）、耗時 1.9s |
| 結構完整性 | **0 個異常變更**、971 個 `tool_use_id` 完全保留、訊息與區塊數不變 |
| 端到端 | proxy 啟動正常、`/health` 回應正確、轉發鏈路通 |
| 隱私 | 送出樣本無 `/Users/steven`、`localhost`、`CCProject` 殘留 |

### 實作中發現並修正的兩件事

1. **fail-open 的統計 bug**：Jev 呼叫失敗早退時沒設 `chars_after`，導致統計誤報「省了 100%」。已修正為 `chars_after = chars_before`。
2. **timeout 3 秒太短**：實測 25 筆一批會逾時（SDK 預設是 10 秒）。已改為 10 秒。

### Code review 修正（第二輪）

首輪 review 判定 WARNING（0 CRITICAL、2 HIGH）。**HIGH 兩項都是真的，且第一項直接違反核心設計要求**：

| 問題 | 為什麼嚴重 | 修正 |
|---|---|---|
| **fail-open 破口**：`int(Content-Length)` 在 try 之外 | Content-Length 非數字（畸形請求）會拋 ValueError 逃出 handler，連線被關、**請求完全不轉發** | 包進 try，失敗記 log 後照常轉發；已用 `Content-Length: abc` 實測驗證 |
| **快取 race**：`_Cache.put` 的淘汰是 check-then-delete-then-set | 多執行緒同時淘汰 → KeyError → 整次壓縮靜默作廢 | 加 `threading.Lock`，並補上 8 執行緒並發測試 |

同時處理的 MEDIUM：
- **回應 framing**：原本剝掉 Content-Length 又沒補 chunked，長度只能靠關閉連線界定 → 明確送出 `Connection: close`
- **O(n²) 累積**：每讀 8KB 就對整包 `json.loads` 一次 → 改為只保留尾段比對，並用上游的 Content-Length 判斷讀足
- **上游 timeout 硬編 600s** → 改為環境變數 `JEV_COMPACTION_UPSTREAM_TIMEOUT`
- **測試缺口**：proxy 層原本零測試 → 新增 `test_proxy.py`（9 個測試，涵蓋 fail-open 全路徑與並發）

測試總數：**33 個**（24 → 33）。

### 效能觀察

壓縮 120 筆候選耗時 1.9 秒（5 批次）。這延遲只發生在**有候選的請求**上，且快取會吸收重複內容。實際使用時多數請求候選數遠低於上限（120），延遲應明顯較低。**仍待真實使用驗證。**

---

## ⚠️ 待確認決策點（需 Steven 拍板）

### 1. 要不要接上真實鏈路？

我可以把 proxy 寫完並測試好，但**不會**動 `ANTHROPIC_BASE_URL`——那會影響正在運作的 Claude Code。要接上時由你自己改，或明確告訴我再改。

### 2. 走哪個 provider？

| 選項 | 保留政策 | 成本 |
|---|---|---|
| A. 直連 TypeSafe（現有 key） | 自家政策「合理必要期間」，ZDR 需 enterprise | 按 token，極低 |
| B. 經 Vercel AI Gateway | **TypeSafe 承諾「不超過產生輸出所需時間」** | ZDR 免費但需 Vercel Pro（$20/月） |

B 的隱私保障明顯較強，但要多一個帳號與月費。**預設我會先實作 A**（用現有 key 可立即運作），並把 B 做成可切換。

### 3. 偽名化要不要預設開啟？

偽名化保護隱私，但**可能輕微影響判斷品質**（實測一致率 98.6%，5 筆差異中有 1 筆從 p=0.35 掉到 0.18）。預設我**開啟**（隱私優先）。

### 4. 門檻值

沿用 LiteLLM 預設 0.2。實測在此資料上表現合理（無關組集中在 0.05、相關組中位數 0.3），但這是別人的資料調出來的，你自己的對話可能需要校準。

### 5. 要不要處理 assistant 訊息裡的舊內容？

目前只壓 `tool_result`（LiteLLM 也只做這個）。assistant 的長篇回覆同樣佔 context，但壓縮它們風險更高（可能刪掉關鍵推理）。**預設不處理**。
