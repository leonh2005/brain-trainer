# 常駐 Agent 編排系統 — 設計文件

- **日期**：2026-10-01
- **狀態**：設計已確認，待實作計畫
- **作者**：Steven + Claude

## 1. 背景

目前有大量自動化服務（cron / LaunchAgent）與一支本地 LLM agent（Hermes，接 omlx 8005 / `ornith-1.5-9b-abliterated`）。痛點：

- **Hermes 慢** — 本地 9B 模型在 M4 / 16 GB 上吞吐有限（大 context 的 prefill 尤其慢，實測 62K token prefill 曾耗時 171 秒），做不動實際工作。
- **任務只能一個一個跑** — 現有模式是「一支 `claude -p` 定時觸發、做完就散」，沒有並行、沒有跨任務狀態。
- **缺編排層** — 沒有地方能「丟一個任務、讓多個 agent 分頭做、跨天記得進度」。

### 已評估並排除：Cumora

Cumora 是 agent 協作平台，但 **它的 Claude Code agent 被關在 restricted 沙箱**（無 Bash、無網路、碰不到主機，只能在其 workspace 內活動）。與本專案需求（worker 要操作本機工具、改專案、跑分析）不符。且自架需常駐 Postgres + Redis，對 16 GB 機器是負擔。故不採用。

## 2. 目標 / 非目標

### 目標

- 一個**通用 orchestrator**：丟自然語言任務 → 自動拆解為子任務 → 給使用者確認 → 派給多個 worker 並行／依序執行 → 彙整回報。
- worker 使用 **headless Claude Code**（`claude -p`），經 ccr → DeepSeek，**快且便宜**。
- 狀態持久（SQLite），**跨天**、重開機後續跑。
- 三個入口：**終端機、Telegram、command-center**。
- worker **能操作本機**（讀寫檔、執行指令），但框限在任務指定的工作目錄。

### 非目標（YAGNI）

- 不做 Cumora 式的「人 + agent 團隊聊天」介面。
- 不做沙箱隔離 —— worker 就是本機 Claude Code，信任等級等同現有 headless 腳本。
- 不做多使用者 / 權限系統。
- 不重寫現有排程服務；本系統與其並存，未來可視情況逐步吸收。

## 3. 架構

```
   Telegram  ─┐
              │
   command-   ├──►  佇列 + 看板（SQLite）  ──►  orchestrator  ──►  worker pool
   center     │        （單一後端）
   終端機    ─┘
```

- **單一後端**：三個入口寫進同一個 SQLite 佇列，共用同一套 daemon / worker。
- **輕量常駐**：daemon 常駐（記憶體 < 50 MB），worker 於任務到達才 spawn，idle 不佔資源 —— 這點刻意避開 M4 / 16 GB 的記憶體壓力。

## 4. 元件

### 4.1 佇列 + 看板（SQLite）

唯一真實來源。所有任務狀態存於此，持久 → 跨天。
建議位置：`~/CCProject/agent-orchestrator/queue.db`（DB 檔需列入 `.gitignore`）。

### 4.2 daemon（scheduler）

- 常駐迴圈，每 N 秒掃描佇列。
- 挑出 `status = pending` **且** `depends_on` 全部 `done` 的任務。
- 檢查並行上限（預設 3），未滿則 spawn worker。
- **心跳監控**：`status = running` 但 `heartbeat_at` 超過 timeout（預設 10 分鐘）→ 標 `failed` 並終止該 worker。

### 4.3 worker

- 執行方式：`claude -p "<task spec>"`，於任務指定的 `cwd` 執行，環境同現有 headless 腳本。
- 成功 → 寫回 `result`、`status = done`；失敗 → 寫 `error`、`status = failed`。
- 執行期間定期更新 `heartbeat_at`。

### 4.4 orchestrator

- 一種**特殊任務**：收到使用者的母任務 → 呼叫 `claude -p` 產出**子任務拆解**（JSON：每條含 `title` / `spec` / `cwd` / `depends_on`）→ 寫入佇列，狀態為 `blocked`（待確認）。
- 使用者確認後 → 子任務轉 `pending`，進入正常流程。
- 全部子任務完成 → orchestrator 彙整結果 → 通知使用者。

**母任務的生命週期**：

| 階段 | 母任務狀態 | 子任務狀態 |
|---|---|---|
| 建立、拆解中 | `running` | —（尚未產生） |
| 拆解完成、待確認 | `blocked` | `blocked` |
| 已確認、執行中 | `running` | `pending` → `running` → `done` / `failed` |
| 全部子任務結束 | `done`（彙整）或 `failed`（有子任務失敗） | — |

## 5. 資料模型

`tasks` 表：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `id` | TEXT PK | 任務 id |
| `parent_id` | TEXT | 拆解來源（orchestrator 母任務） |
| `title` | TEXT | 短標題 |
| `spec` | TEXT | 任務描述（自然語言，餵給 `claude -p`） |
| `status` | TEXT | `pending` / `running` / `blocked` / `done` / `failed` |
| `depends_on` | TEXT | JSON array（task ids） |
| `cwd` | TEXT | 工作目錄（權限框限） |
| `result` | TEXT | 產出摘要 / 路徑 |
| `error` | TEXT | 失敗原因 |
| `source` | TEXT | `telegram` / `cc` / `cli` |
| `retries` | INTEGER | 已重試次數 |
| `worker_pid` | INTEGER | 執行中的 worker PID |
| `created_at` | TIMESTAMP | |
| `started_at` | TIMESTAMP | |
| `finished_at` | TIMESTAMP | |
| `heartbeat_at` | TIMESTAMP | 卡死偵測用 |

## 6. 資料流（範例）

**研究型（可並行）**

母任務「研究 AI 眼鏡市場，整理成報告」→ orchestrator 拆成：

```
① 查市面產品        ┐
② 查市場規模/成長率  ├─ 互相獨立 → 3 個 worker 同時跑
③ 查主要玩家        ┘
④ 彙整成報告        ← depends_on: [①②③]
```

確認後 ①②③ 並行，完成後 ④ 自動接手，彙整 → 通知。

**開發型（有依賴鏈）**

「幫 command-center 卡片加排序功能」→

```
① 讀現有卡片邏輯 → ② 設計方案 → ③ 實作 → ④ 測試（鏈式，依序）
```

**關鍵**：daemon 只依 `depends_on` 判斷放行與否 —— 獨立任務自然並行，有依賴的自然排隊，不需另寫特例。

## 7. 錯誤處理

- **任務失敗** → 標 `failed` + 記錄 `error` + Telegram 通知，不吞掉。
- **worker 卡死** → 心跳超時即標 failed、終止 worker。**這條最重要** —— 既有服務常是「卡住但沒死」，沒有 timeout 的任務會永遠卡在 `running` 而無人察覺。
- **重試** → 暫時性錯誤（網路 / API）自動重試 N 次並指數退避；邏輯錯誤不重試，停下等人介入。

## 8. 入口

三個入口都呼叫**同一組後端 API**（新增任務、列任務、看 log、確認拆解），只是呈現方式不同。

- **終端機**：`orch add "任務描述"` / `orch ls` / `orch log <id>` / `orch confirm <id>`
- **Telegram**：bot 收訊息 → 丟佇列；orchestrator 的拆解、完成、失敗都推播，可直接回覆確認
- **command-center**：新增一頁看板（排隊 / 進行中 / 完成），附按鈕確認 orchestrator 的拆解

## 9. 並行與依賴

- 並行上限預設 3（可調），避免 M4 / 16 GB 記憶體壓力。
- 依賴以 DAG 表達；daemon 只放行「前置全部 done」的任務。

## 10. 分階段實作

| Phase | 內容 | 產出 |
|---|---|---|
| **1** | 佇列 + 1 個 worker + 終端機入口 | 最小可用：能丟任務、跑完、看狀態 |
| **2** | orchestrator（拆解 + 確認） | 「丟一句話 → 它拆給你看」 |
| **3** | 並行多 worker + 依賴鏈 | 真正的「多 agent 同時幹」 |
| **4** | Telegram + command-center 入口 | 手機 / 網頁都能丟 |

每階段都是往上疊，不打掉重來。

## 11. 測試策略

- **單元**：daemon 的「挑任務」邏輯（依賴判斷、並行上限）、心跳 timeout 判定。
- **整合**：一個真實任務端到端（用便宜的 echo 類任務驗證）。
- **手動**：各 Phase 用真實小任務驗證。

## 12. 風險 / 未解問題

- **orchestrator 拆解品質** — 讓 LLM 自己拆任務可能不穩（拆錯、漏步驟、重複）。以「人工確認後才派工」緩解。
- **worker 權限** — 能寫檔、能執行 → 必須框限 `cwd`；錯誤指令的風險等級同現有 headless 腳本。
- **成本** — DeepSeek 便宜但非零 → 未來可加 per-task token 上限。
- **與現有排程的關係** — 初期並存；是否把現有 cron 任務搬進來，待 Phase 4 後再評估。
