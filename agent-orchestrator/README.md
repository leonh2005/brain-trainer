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

## 邊界

- **可並行**：daemon 一次最多跑 3 個任務（`daemon.MAX_PARALLEL`）；獨立的任務自然並行，有依賴的排隊。
- **兩層逾時**：總時長 3600 秒；閒置超過 600 秒（沒有輸出）也會被判定卡住並終止。
- 失敗自動重試（上限 2 次）；拆解與彙整也各自重試。
- worker 能讀寫、能執行，**權限等同你本人**（含 Bash，不受目錄限制）。`--cwd` 只是「工作起點」，**不是權限邊界** —— 只丟你信任的任務描述，跟對待你現有的 headless 腳本一樣。
