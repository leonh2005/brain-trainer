# agent-orchestrator

常駐 agent 編排系統 — Phase 1–3（佇列 + worker + daemon + CLI + orchestrator 拆解／確認／彙整 + 並行 + 重試）。

設計文件：`../docs/superpowers/specs/2026-10-01-agent-orchestrator-design.md`

## 用法

```bash
# 丟一個任務（--cwd 指定它能動的目錄）
python3 cli.py add "任務描述" --cwd /path/to/工作目錄

# 列出任務
python3 cli.py ls
python3 cli.py ls --status done

# 看某個任務的狀態與產出
python3 cli.py log <task id>

# 啟動 daemon（常駐，最多同時跑 3 個 pending 任務）
python3 daemon.py

# 丟一個大任務，讓系統拆解（daemon 會把它拆成子任務）
python3 cli.py plan "研究 X 並整理成報告" --cwd /path/to/工作目錄

# daemon 拆完後 ls 看得到子任務；看過確認沒問題再放行
python3 cli.py confirm <母任務 id>
```

## 測試

```bash
python3 -m pytest -v
```

## Telegram

**推播已可用**：daemon 在任務結束時會推 ✅／❌ 給你。

**收訊息的 bot 需要一個獨立的 bot token。** 目前的 `telegram_token.txt`（`CcagentForsteven_bot`）已被既有服務以 **webhook** 佔用 —— 同一個 bot 不能同時用 `getUpdates`（會回 409）。要啟用：

1. 用 @BotFather 建一個新 bot
2. 把 token 存成 `~/CCProject/.secrets/orchestrator_token.txt`
3. 把 `notify.TOKEN_FILE` 指向它

指令（`/help` 看全部）：

```
/plan <絕對路徑> :: <大任務>     系統拆解，之後 /confirm
/add  <絕對路徑> :: <任務>      直接排隊
/ls    /log <id>    /confirm <id>
```

## 邊界

- **可並行**：daemon 一次最多跑 3 個任務（`daemon.MAX_PARALLEL`）；獨立的任務自然並行，有依賴的排隊。
- **兩層逾時**：總時長 3600 秒；閒置超過 1800 秒（沒有輸出）也會被判定卡住並終止（拉長是為了不誤殺長時間的單一 tool call）。
- 失敗自動重試（上限 2 次）；拆解與彙整也各自重試。
- worker 能讀寫、能執行，**權限等同你本人**（含 Bash，不受目錄限制）。`--cwd` 只是「工作起點」，**不是權限邊界** —— 只丟你信任的任務描述，跟對待你現有的 headless 腳本一樣。
