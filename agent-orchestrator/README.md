# agent-orchestrator

常駐 agent 編排系統 — Phase 1（佇列 + 單一 worker + 終端機入口）。

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

# 啟動 daemon（常駐，會依序執行 pending 任務）
python3 daemon.py
```

## 測試

```bash
python3 -m pytest -v
```

## Phase 1 的邊界

- **一次只跑一個任務**（序列）。並行與依賴鏈是 Phase 3。
- 逾時用「總時長」（預設 3600 秒），逾時即 `failed` 並終止。
- worker 能讀寫、能執行，**權限等同你本人**（含 Bash，不受目錄限制）。`--cwd` 只是「工作起點」，**不是權限邊界** —— 只丟你信任的任務描述，跟對待你現有的 headless 腳本一樣。
