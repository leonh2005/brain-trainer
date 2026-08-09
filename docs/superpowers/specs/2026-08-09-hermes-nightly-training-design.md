# Hermes 夜間訓練管線 — Design Spec

日期：2026-08-09
狀態：已核准，待寫實作計畫

## 目標

讓 Hermes（本機 Ollama 常駐 agent，見 `project_hermes` 記憶）逐步學會接近 Claude Code 的回答方式與對 Steven 習性的理解。每晚自動把當天 Steven 交給 Claude Code 的任務，原樣重跑一次給 Hermes，比對差距、診斷原因、寫教材、驗證改善，並把教材長期累積進 Hermes 的 system prompt。

## 背景 / 限制

- Hermes 本地模型（qwen3 系列，8B/14B）在推理能力上與 Claude Sonnet 有數量級差距，訓練目標是「拉近可用性」而非追平。
- Hermes CLI 已內建工具能力（`web`／`browser`／`terminal`／`file` toolset 皆為啟用狀態），不需要另外接工具。
- **安全紅線**：Steven 明確要求——任何屬於「寫入／修改」性質的任務，一律不讓 Hermes 碰，避免弄亂真實檔案或設定。訓練範圍只限純問答／分析／規劃類任務。

## 架構總覽

每晚 01:00，LaunchAgent 觸發一次 headless Claude Code（`claude -p`）執行完整管線，全程無人值守：

```
LaunchAgent(01:00)
  → claude -p <nightly-training prompt>
      1. 擷取任務  (讀取當天 session transcript)
      2. 過濾      (排除寫入/修改類任務)
      3. 重跑比對  (逐條丟給 Hermes，取得初答)
      4. 診斷+教學 (差距大的寫教材，餵回驗證)
      5. 記憶灌注  (累加進 learnings.md，掛進 Hermes system prompt)
      6. 完整 log  (逐條記錄成當日 log 檔)
      7. 早報      (Telegram 推簡報)
```

## 元件細節

### 1. 任務擷取
- 來源：`~/.claude/projects/-Users-steven-CCProject/` 底下當天日期的 session transcript（`.jsonl`）
- 擷取單位：每個使用者回合（user turn）視為一個「任務」，連同 Claude 當時的最終文字回覆一併取出

### 2. 過濾規則（安全紅線）
排除任何任務，只要該任務的回合中出現以下情形之一：
- 呼叫過 `Edit` / `Write` / `NotebookEdit`
- 呼叫過帶有寫入／修改性質的 `Bash` 指令（如 `rm`、`mv`、`cp`、`git commit`、`git push`、`curl -X POST/PUT/DELETE`、`mkdir`、`touch`、`chmod`、`pip install`、`npm install`、`launchctl`、`kill` 等具破壞性/副作用的指令模式）
- 無法明確判斷是否有副作用的 Bash 指令 → **一律當作有副作用，排除**（寧可少教，不可教錯）

只有純讀取／分析／規劃／問答類任務（例如 Read、Grep、WebSearch、單純文字問答）才進入訓練集。

### 3. 重跑比對
- 用 Hermes 平常在 Telegram 使用的預設模型（`config.yaml` 的 `model.default`）逐條送出當天任務的原始提問
- 取得 Hermes 的初始回答

### 4. 診斷 + 教學
- 由執行當晚管線的 Claude（即「當時的我」）親自審查 Hermes 初答與自己當時回答的差距，無需額外評分機制
- 差距明顯的任務：撰寫一條繁中規則／教材，說明「為什麼我會那樣答、Hermes 少了什麼」
- 把教材連同原問題重新送給 Hermes，取得修正後回答，驗證是否改善

### 5. 記憶灌注
- 新學到的規則累加進 `~/.hermes/learnings.md`
- 該檔案掛在 `~/.hermes/config.yaml` 的 `agent.personalities` 底下（新增一個 personality block，例如 `steven_context`），確保每次 Hermes 對話都會帶入
- **已知限制（暫不處理）**：`learnings.md` 會隨時間增長，最終可能逼近 Hermes context 上限（65536 tokens）。屆時需另外設計壓縮／篩選機制，目前先不做，留待之後有實際需求再處理

### 6. 完整 Log
- 每晚一份 `~/CCProject/hermes-training/logs/YYYY-MM-DD.md`
- 逐條記錄：原始提問 / Hermes 初答 / 我寫的教材（若有）/ Hermes 修正後回答（若有）

### 7. 早報
- 管線跑完後，透過既有 Telegram bot 推送簡報：今晚比對了幾條任務、新學了幾條規則、差距最大的任務是哪個
- 若當天無符合條件的任務（例如全部涉及寫入操作被排除），仍推送「今晚 0 條可訓練任務」的簡報，而非靜默不推

## 錯誤處理
- Ollama／Hermes 未啟動：`hermes` CLI 本身會自動帶起 Ollama（已驗證行為），管線直接呼叫即可
- 當天 transcript 讀取失敗或不存在：該任務略過，log 記錄原因，早報照常發送並註明「今晚未執行/資料異常」

## 排除範圍（不做）
- 不做 RAG／向量資料庫（教材量目前小，system prompt 累加已足夠）
- 不做 Hermes 額外工具串接（已內建，不用重造）
- 不對寫入類任務做沙盒模擬執行（直接整批排除，比沙盒更安全簡單）
- 不做 `learnings.md` 自動壓縮（留待之後）

## 驗證方式
本專案為個人自動化腳本而非產品，驗證方式：
1. 白天先手動跑一次完整管線（用當天既有 transcript）確認流程無誤、log 格式正確、Telegram 早報正常送達
2. 確認 `learnings.md` 確實被 `config.yaml` 引用且 Hermes 下次對話能感知新教材
3. 開啟 LaunchAgent 後，觀察第一次自動夜間執行的 log 與早報是否正常
