# 常駐 Agent 編排系統 — Phase 1 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做出最小可用的「任務佇列 + 單一 worker + 終端機入口」——能丟一個任務、跑完、看狀態。

**Architecture:** SQLite 是唯一真實來源；daemon 常駐輪詢、原子地認領可跑的任務、spawn 一個 worker 執行 `claude -p` 並寫回結果。Phase 1 序列執行（一次一個），並行留給 Phase 3。

**Tech Stack:** Python 3（只用 stdlib：`sqlite3` / `subprocess` / `argparse`）、pytest

**Spec:** `docs/superpowers/specs/2026-10-01-agent-orchestrator-design.md`

## Global Constraints

- 任務狀態值**僅限**：`pending` / `running` / `blocked` / `done` / `failed`
- worker 一律用 `claude -p`，flags 固定為：`--permission-mode acceptEdits --allowedTools "Read,Write,Edit,Bash,Grep,Glob" --disallowedTools "Agent,Workflow" --output-format text`（沿用既有 headless 腳本慣例）
- 一律**不用 shell** 執行外部指令 —— `subprocess` 傳 list args，避免注入
- 任務逾時預設 **3600 秒**，逾時一律標 `failed`
- 專案根目錄：`agent-orchestrator/`（在 `~/CCProject/` 下）
- DB 檔（`queue.db*`）與 log **不得進 git**
- Python 檔名 `taskqueue.py`（**不可**用 `queue.py` —— 與 stdlib `queue` 衝突）

## Review Focus

以下五類是 spec 隱含、但若不留神最容易咬人的情況。每條都在對應 task 有測試：

1. **`cwd` 路徑不存在** → 應立刻標 `failed` 並說明，不能讓 claude 在錯誤目錄亂跑（Task 2）
2. **任務 spec 含引號 / 換行 / `$` 等特殊字元** → 必須原樣傳遞，不能經 shell（Task 2）
3. **`claude` 執行檔不在 PATH** → 要清楚報 `failed`，不能靜默（Task 2）
4. **同一任務被跑兩次**（兩個 daemon，或 daemon 重啟）→ 靠原子認領 `claim_next` 避免（Task 1、Task 3）
5. **DB 被另一行程鎖住** → 靠 WAL + `busy_timeout` 撐過（Task 1）

---

### Task 1: 專案骨架與資料層

**Files:**
- Create: `agent-orchestrator/.gitignore`
- Create: `agent-orchestrator/pytest.ini`
- Create: `agent-orchestrator/taskqueue.py`
- Test: `agent-orchestrator/tests/test_taskqueue.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `connect(db_path: str) -> sqlite3.Connection`
  - `init_db(conn) -> None`
  - `add_task(conn, title: str, spec: str, cwd: str | None = None, depends_on: list[str] | None = None, source: str = "cli") -> str`
  - `get_task(conn, task_id: str) -> dict | None`（`depends_on` 已解析為 list）
  - `list_tasks(conn, status: str | None = None) -> list[dict]`
  - `set_status(conn, task_id: str, status: str, **fields) -> None`
  - `claim_next(conn) -> dict | None`（原子認領）

- [ ] **Step 1: 建立目錄與專案檔**

```bash
mkdir -p ~/CCProject/agent-orchestrator/tests
```

`agent-orchestrator/.gitignore`：

```
queue.db
queue.db-wal
queue.db-shm
*.log
__pycache__/
.pytest_cache/
venv/
```

`agent-orchestrator/pytest.ini`：

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 2: 寫失敗的測試**

`agent-orchestrator/tests/test_taskqueue.py`：

```python
import time

import pytest

import taskqueue as tq


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def test_init_creates_tasks_table(tmp_path):
    conn = _conn(tmp_path)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "tasks" in names


def test_connection_enables_wal_and_busy_timeout(tmp_path):
    # 讓多個行程同時碰 DB 時不會立刻 database is locked
    conn = _conn(tmp_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_add_and_get_task(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "標題", "做某事", cwd="/tmp")
    t = tq.get_task(conn, tid)
    assert t["title"] == "標題"
    assert t["spec"] == "做某事"
    assert t["status"] == "pending"
    assert t["depends_on"] == []
    assert t["retries"] == 0


def test_get_missing_returns_none(tmp_path):
    conn = _conn(tmp_path)
    assert tq.get_task(conn, "nope") is None


def test_list_tasks_filters_by_status(tmp_path):
    conn = _conn(tmp_path)
    a = tq.add_task(conn, "a", "sa")
    tq.add_task(conn, "b", "sb")
    tq.set_status(conn, a, "done")
    assert [t["id"] for t in tq.list_tasks(conn, status="done")] == [a]
    assert len(tq.list_tasks(conn)) == 2


def test_set_status_rejects_invalid(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "x", "y")
    with pytest.raises(ValueError):
        tq.set_status(conn, tid, "bogus")


def test_claim_next_respects_dependencies(tmp_path):
    conn = _conn(tmp_path)
    a = tq.add_task(conn, "A", "sa")
    b = tq.add_task(conn, "B", "sb", depends_on=[a])
    # A 沒依賴 → 先被撿到
    assert tq.claim_next(conn)["id"] == a
    # A 現在是 running（尚未 done）→ B 還不能跑
    assert tq.claim_next(conn) is None
    tq.set_status(conn, a, "done")
    assert tq.claim_next(conn)["id"] == b


def test_claim_next_marks_running_atomically(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "A", "sa")
    tq.claim_next(conn)
    t = tq.get_task(conn, tid)
    assert t["status"] == "running"
    assert t["started_at"] is not None
```

- [ ] **Step 3: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_taskqueue.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'taskqueue'`

- [ ] **Step 4: 實作 `taskqueue.py`**

```python
"""任務佇列的資料層 —— SQLite 是唯一真實來源。"""
import json
import sqlite3
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    parent_id    TEXT,
    title        TEXT NOT NULL,
    spec         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    depends_on   TEXT NOT NULL DEFAULT '[]',
    cwd          TEXT,
    result       TEXT,
    error        TEXT,
    source       TEXT NOT NULL DEFAULT 'cli',
    retries      INTEGER NOT NULL DEFAULT 0,
    worker_pid   INTEGER,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
    heartbeat_at REAL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

VALID_STATUS = {"pending", "running", "blocked", "done", "failed"}


def connect(db_path):
    # isolation_level=None → autocommit，交易由我們自己 BEGIN/COMMIT 控制
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)


def add_task(conn, title, spec, cwd=None, depends_on=None, source="cli"):
    task_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO tasks (id, title, spec, status, depends_on, cwd, source, created_at)"
        " VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)",
        (task_id, title, spec, json.dumps(depends_on or []), cwd, source, time.time()),
    )
    return task_id


def get_task(conn, task_id):
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_tasks(conn, status=None):
    if status:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
    return [_row_to_dict(r) for r in rows]


def set_status(conn, task_id, status, **fields):
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status}")
    cols = ["status = ?"]
    vals = [status]
    for key, val in fields.items():
        cols.append(f"{key} = ?")
        vals.append(val)
    vals.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(cols)} WHERE id = ?", vals)


def claim_next(conn):
    """原子地認領一個可跑的任務並標 running；沒有則回 None。
    可跑 = status 為 pending 且 depends_on 全部 done。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at"
        ).fetchall()
        for row in rows:
            deps = json.loads(row["depends_on"])
            if _all_done(conn, deps):
                conn.execute(
                    "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ?",
                    (time.time(), row["id"]),
                )
                conn.execute("COMMIT")
                return get_task(conn, row["id"])
        conn.execute("COMMIT")
        return None
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _all_done(conn, deps):
    if not deps:
        return True
    placeholders = ",".join("?" * len(deps))
    n = conn.execute(
        f"SELECT COUNT(*) FROM tasks WHERE id IN ({placeholders}) AND status = 'done'",
        deps,
    ).fetchone()[0]
    return n == len(deps)


def _row_to_dict(row):
    d = dict(row)
    d["depends_on"] = json.loads(d["depends_on"])
    return d
```

- [ ] **Step 5: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_taskqueue.py -v`
Expected: PASS（8 passed）

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add .gitignore pytest.ini taskqueue.py tests/test_taskqueue.py
git commit -m "feat: 任務佇列資料層（SQLite + 原子認領）"
```

---

### Task 2: worker — 執行單一任務

**Files:**
- Create: `agent-orchestrator/worker.py`
- Test: `agent-orchestrator/tests/test_worker.py`

**Interfaces:**
- Consumes: `taskqueue.get_task` / `set_status`
- Produces: `run_task(conn, task: dict, claude_bin: str = "claude", timeout: int = 3600) -> tuple[bool, str]`

- [ ] **Step 1: 寫失敗的測試**

`agent-orchestrator/tests/test_worker.py`：

```python
import os
import stat

import taskqueue as tq
import worker


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def _fake_claude(tmp_path, body):
    """做一個假的 claude 執行檔，內容為 body。"""
    path = tmp_path / "fake_claude"
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_run_task_success_writes_result(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo "done-ok"\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec here", cwd=str(tmp_path))
    ok, out = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "done"
    assert "done-ok" in got["result"]


def test_run_task_nonzero_exit_marks_failed(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo "boom" >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "boom" in got["error"]


def test_run_task_missing_cwd_fails_cleanly(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'exit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd="/nonexistent_dir_xyz")
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert not ok
    assert "工作目錄" in tq.get_task(conn, tid)["error"]


def test_run_task_missing_binary_fails_cleanly(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid),
                           claude_bin="/no/such/claude", timeout=10)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert got["error"]


def test_run_task_passes_spec_verbatim(tmp_path):
    """spec 含引號、$、換行 → 必須原樣到 claude 手上（不經 shell）。"""
    conn = _conn(tmp_path)
    out_file = tmp_path / "argv.txt"
    fake = _fake_claude(tmp_path, f'printf "%s" "$2" > {out_file}\n')
    weird = 'has "quotes" and $HOME and\nnewline'
    tid = tq.add_task(conn, "t", weird, cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert ok, "假 claude 應正常結束"
    assert out_file.read_text() == weird


def test_run_task_timeout_marks_failed(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'sleep 30\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=1)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "逾時" in got["error"] or "timeout" in got["error"]
```

> 註：`test_run_task_passes_spec_verbatim` 的假 claude 讀 `$2`，因為參數順序是
> `[claude_bin, "-p", spec, ...]`。若實作把 `-p` 換成別的形式，這個測試會抓到。

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_worker.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 3: 實作 `worker.py`**

```python
"""執行單一任務：spawn claude -p，把結果寫回 DB。"""
import os
import subprocess
import time

import taskqueue as tq

CLAUDE_ARGS = [
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Read,Write,Edit,Bash,Grep,Glob",
    "--disallowedTools", "Agent,Workflow",
    "--output-format", "text",
]


def run_task(conn, task, claude_bin="claude", timeout=3600):
    """執行一個任務；回傳 (ok, output)。無論成敗都會把狀態寫回 DB。"""
    task_id = task["id"]
    cwd = task.get("cwd") or "."

    if not os.path.isdir(cwd):
        tq.set_status(conn, task_id, "failed",
                      error=f"工作目錄不存在：{cwd}", finished_at=time.time())
        return False, ""

    try:
        proc = subprocess.run(
            [claude_bin, "-p", task["spec"], *CLAUDE_ARGS],
            cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        tq.set_status(conn, task_id, "failed",
                      error=f"逾時（超過 {timeout} 秒）", finished_at=time.time())
        return False, ""
    except FileNotFoundError:
        tq.set_status(conn, task_id, "failed",
                      error=f"找不到執行檔：{claude_bin}", finished_at=time.time())
        return False, ""

    out = (proc.stdout or "").strip()
    if proc.returncode == 0:
        tq.set_status(conn, task_id, "done", result=out,
                      error=None, finished_at=time.time())
        return True, out

    err = (proc.stderr or "").strip() or f"exit code {proc.returncode}"
    tq.set_status(conn, task_id, "failed", error=err[:2000], finished_at=time.time())
    return False, out
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_worker.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add worker.py tests/test_worker.py
git commit -m "feat: worker 執行 claude -p 並寫回結果"
```

---

### Task 3: daemon — 常駐排程迴圈

**Files:**
- Create: `agent-orchestrator/daemon.py`
- Test: `agent-orchestrator/tests/test_daemon.py`

**Interfaces:**
- Consumes: `taskqueue.claim_next` / `list_tasks` / `set_status`；`worker.run_task`
- Produces:
  - `reap_timed_out(conn, timeout: int) -> int`
  - `tick(conn, claude_bin="claude", timeout=3600) -> int`
  - `main_loop(claude_bin="claude", timeout=3600, poll=5, db_path="queue.db") -> None`

- [ ] **Step 1: 寫失敗的測試**

`agent-orchestrator/tests/test_daemon.py`：

```python
import stat
import time

import daemon
import taskqueue as tq


def _conn(tmp_path):
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    return conn


def _fake_claude(tmp_path, body):
    path = tmp_path / "fake_claude"
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_reap_timed_out_marks_stale_running(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    # 模擬：daemon 上次執行到一半就掛了，留下 running 且 started_at 很久以前
    tq.set_status(conn, tid, "running", started_at=time.time() - 99999)
    n = daemon.reap_timed_out(conn, timeout=10)
    assert n == 1
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "逾時" in got["error"]


def test_reap_leaves_fresh_running_alone(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running", started_at=time.time())
    assert daemon.reap_timed_out(conn, timeout=3600) == 0
    assert tq.get_task(conn, tid)["status"] == "running"


def test_tick_runs_one_task(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo ok\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    assert daemon.tick(conn, claude_bin=fake, timeout=10) == 1
    assert tq.get_task(conn, tid)["status"] == "done"


def test_tick_returns_zero_when_nothing_ready(tmp_path):
    conn = _conn(tmp_path)
    assert daemon.tick(conn, claude_bin="claude", timeout=10) == 0


def test_tick_does_not_rerun_done_task(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo ok\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    daemon.tick(conn, claude_bin=fake, timeout=10)
    # 再跑一輪不應重跑已完成的任務
    assert daemon.tick(conn, claude_bin=fake, timeout=10) == 0
    assert tq.get_task(conn, tid)["status"] == "done"
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_daemon.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'daemon'`

- [ ] **Step 3: 實作 `daemon.py`**

```python
"""常駐排程：收割逾時任務、認領可跑的任務、交給 worker。"""
import time

import taskqueue as tq
import worker


def reap_timed_out(conn, timeout):
    """把 running 但 started_at 超過 timeout 的任務標 failed。回傳收割數。"""
    now = time.time()
    n = 0
    for task in tq.list_tasks(conn, status="running"):
        started = task["started_at"]
        if started is not None and now - started > timeout:
            tq.set_status(conn, task["id"], "failed",
                          error=f"逾時（超過 {timeout} 秒，由 daemon 收割）",
                          finished_at=now)
            n += 1
    return n


def tick(conn, claude_bin="claude", timeout=3600):
    """跑一輪：先收割逾時，再認領並執行一個任務。回傳本輪執行的任務數（0 或 1）。"""
    reap_timed_out(conn, timeout)
    task = tq.claim_next(conn)
    if not task:
        return 0
    worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout)
    return 1


def main_loop(claude_bin="claude", timeout=3600, poll=5, db_path="queue.db"):
    conn = tq.connect(db_path)
    tq.init_db(conn)
    while True:
        if tick(conn, claude_bin=claude_bin, timeout=timeout) == 0:
            time.sleep(poll)


if __name__ == "__main__":
    main_loop()
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_daemon.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add daemon.py tests/test_daemon.py
git commit -m "feat: daemon 常駐排程與逾時收割"
```

---

### Task 4: 終端機入口

**Files:**
- Create: `agent-orchestrator/cli.py`
- Test: `agent-orchestrator/tests/test_cli.py`

**Interfaces:**
- Consumes: `taskqueue.add_task` / `list_tasks` / `get_task`
- Produces:
  - `cmd_add(args) -> None`
  - `cmd_ls(args) -> None`
  - `cmd_log(args) -> None`
  - `build_parser() -> argparse.ArgumentParser`
  - 可執行：`python3 cli.py add "標題" [--spec ...] [--cwd ...]` / `ls` / `log <id>`

- [ ] **Step 1: 寫失敗的測試**

`agent-orchestrator/tests/test_cli.py`：

```python
import argparse
import subprocess
import sys
from pathlib import Path

import cli
import taskqueue as tq

HERE = Path(__file__).resolve().parent.parent


def test_cmd_add_creates_task(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    ns = argparse.Namespace(db=db, title="我的任務", spec="做點事", cwd=None)
    cli.cmd_add(ns)
    task_id = capsys.readouterr().out.strip()
    conn = tq.connect(db)
    assert tq.get_task(conn, task_id)["title"] == "我的任務"


def test_cmd_add_defaults_spec_to_title(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    cli.cmd_add(argparse.Namespace(db=db, title="只有標題", spec=None, cwd=None))
    conn = tq.connect(db)
    assert tq.list_tasks(conn)[0]["spec"] == "只有標題"


def test_cmd_ls_lists_tasks(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    cli.cmd_add(argparse.Namespace(db=db, title="A", spec="sa", cwd=None))
    capsys.readouterr()
    cli.cmd_ls(argparse.Namespace(db=db, status=None))
    assert "A" in capsys.readouterr().out


def test_cmd_log_shows_status_and_result(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    conn = tq.connect(db)
    tq.init_db(conn)
    tid = tq.add_task(conn, "A", "sa")
    tq.set_status(conn, tid, "done", result="產出內容")
    cli.cmd_log(argparse.Namespace(db=db, id=tid))
    out = capsys.readouterr().out
    assert "done" in out and "產出內容" in out


def test_cli_runs_as_script(tmp_path):
    """從命令列真的叫得動（端到端的最小驗證）。"""
    db = str(tmp_path / "t.db")
    proc = subprocess.run(
        [sys.executable, str(HERE / "cli.py"), "--db", db, "add", "CLI 任務"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    task_id = proc.stdout.strip()
    ls = subprocess.run(
        [sys.executable, str(HERE / "cli.py"), "--db", db, "ls"],
        capture_output=True, text=True,
    )
    assert task_id in ls.stdout
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_cli.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'cli'`

- [ ] **Step 3: 實作 `cli.py`**

```python
"""終端機入口：orch add / ls / log。"""
import argparse

import taskqueue as tq

DEFAULT_DB = "queue.db"


def cmd_add(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    task_id = tq.add_task(conn, args.title, args.spec or args.title, cwd=args.cwd)
    print(task_id)


def cmd_ls(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    for t in tq.list_tasks(conn, status=args.status):
        print(f"{t['id']}  {t['status']:8s}  {t['title']}")


def cmd_log(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    t = tq.get_task(conn, args.id)
    if not t:
        print(f"找不到任務：{args.id}")
        return
    print(f"id     : {t['id']}")
    print(f"title  : {t['title']}")
    print(f"status : {t['status']}")
    if t["result"]:
        print("--- result ---")
        print(t["result"])
    if t["error"]:
        print("--- error ---")
        print(t["error"])


def build_parser():
    p = argparse.ArgumentParser(prog="orch", description="任務佇列")
    p.add_argument("--db", default=DEFAULT_DB, help="queue.db 路徑")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增任務")
    a.add_argument("title")
    a.add_argument("--spec", help="完整任務描述（預設同 title）")
    a.add_argument("--cwd", help="工作目錄")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("ls", help="列出任務")
    l.add_argument("--status", help="只看某個狀態")
    l.set_defaults(func=cmd_ls)

    g = sub.add_parser("log", help="看單一任務")
    g.add_argument("id")
    g.set_defaults(func=cmd_log)
    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_cli.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add cli.py tests/test_cli.py
git commit -m "feat: 終端機入口 add/ls/log"
```

---

### Task 5: 端到端驗證與使用說明

**Files:**
- Test: `agent-orchestrator/tests/test_end_to_end.py`
- Create: `agent-orchestrator/README.md`

**Interfaces:**
- Consumes: 全部前面的模組
- Produces: 無新介面；交付「能實際用」的驗證與文件

- [ ] **Step 1: 寫端到端測試**

`agent-orchestrator/tests/test_end_to_end.py`：

```python
import stat

import daemon
import taskqueue as tq


def test_full_flow_add_run_inspect(tmp_path):
    """丟任務 → daemon 跑 → 狀態與產出可查。"""
    db = str(tmp_path / "t.db")
    conn = tq.connect(db)
    tq.init_db(conn)

    fake = tmp_path / "fake_claude"
    fake.write_text('#!/bin/bash\necho "任務完成：$(pwd)"\nexit 0\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    workdir = tmp_path / "work"
    workdir.mkdir()
    tid = tq.add_task(conn, "端到端", "做一件事", cwd=str(workdir))

    assert daemon.tick(conn, claude_bin=str(fake), timeout=10) == 1

    got = tq.get_task(conn, tid)
    assert got["status"] == "done"
    assert "任務完成" in got["result"]
    assert str(workdir) in got["result"]  # 證實 claude 在對的 cwd 執行
```

- [ ] **Step 2: 執行全部測試**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -v`
Expected: PASS（全部，含前面的 taskqueue/worker/daemon/cli）

- [ ] **Step 3: 手動跑一次真實流程（用真 claude）**

```bash
cd ~/CCProject/agent-orchestrator
mkdir -p /tmp/orch_demo && cd /tmp/orch_demo
python3 ~/CCProject/agent-orchestrator/cli.py --db /tmp/orch_demo/queue.db add \
  "在這個目錄建一個 hello.txt，內容放 'hi'，然後說你做了什麼" --cwd /tmp/orch_demo
```

記下印出的 task id，然後**另開一個終端機**啟 daemon：

```bash
cd /tmp/orch_demo
python3 ~/CCProject/agent-orchestrator/daemon.py
```

等任務變 `done` 後（`python3 ~/CCProject/agent-orchestrator/cli.py --db /tmp/orch_demo/queue.db ls`），確認：

```bash
python3 ~/CCProject/agent-orchestrator/cli.py --db /tmp/orch_demo/queue.db log <task id>
cat /tmp/orch_demo/hello.txt   # 應印出 hi
```

Expected：狀態 `done`、`hello.txt` 內容為 `hi`。

- [ ] **Step 4: 寫 `README.md`**

```markdown
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
- worker 能讀寫、能執行，**權限邊界就是 `--cwd` 指的目錄** —— 丟任務前確認那個目錄是對的。
```

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add README.md tests/test_end_to_end.py
git commit -m "test: 端到端驗證與使用說明（Phase 1 完成）"
```

---

## Phase 1 完成定義

- `pytest` 全綠。
- 能用 `cli.py add` 丟任務、`daemon.py` 跑、`cli.py log` 看到 `done` 與產出。
- 真 claude 的手動驗證（Task 5 Step 3）通過。

## 後續（不在本計畫）

- **重試**（spec §7）：暫時性錯誤自動重試 N 次、指數退避 —— 非 Phase 1 必需，建議與 Phase 2 一起做
- **Phase 2**：orchestrator（用 `claude -p` 拆解任務 + 人確認）
- **Phase 3**：並行多 worker + 依賴鏈 + idle-timeout（真實進展偵測）
- **Phase 4**：Telegram 與 command-center 入口
