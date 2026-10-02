# 常駐 Agent 編排系統 — Phase 3 實作計畫（並行 + idle-timeout + orchestrator 重試）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 daemon 同時跑多個任務、偵測「卡住但沒死」（閒置逾時）、並讓 orchestrator 的拆解／彙整也能重試。

**Architecture:** daemon 的 `main_loop` 改成啟動背景 worker thread（各自持有自己的 SQLite 連線），填滿並行上限；`claim_next` 的原子認領保證不會重複派工。worker 改用 `--output-format stream-json` 逐行讀取，超過 `idle_timeout` 沒有新輸出就殺掉整個 process group。

**Tech Stack:** Python 3（stdlib：`threading` / `subprocess` / `json`）、pytest。沿用既有 `claude -p` flags，另加 `--output-format stream-json --verbose`。

**Spec:** `docs/superpowers/specs/2026-10-01-agent-orchestrator-design.md`（§4.2 daemon、§4.3 worker、§9 並行）

## Global Constraints

- 任務狀態值僅限：`pending` / `running` / `blocked` / `done` / `failed`
- worker 一律用 `claude -p`；**並行模式**的輸出用 `--output-format stream-json --verbose`，其餘 flags 不變
- 一律不用 shell：`subprocess` 傳 list args；`Popen(start_new_session=True)` + process group 終止
- **並行上限預設 3**（可調）；`SQLite` 靠 WAL + `busy_timeout` 撐並行寫入
- **每個 worker thread 用自己的 DB 連線**（`sqlite3` 連線不可跨 thread 共用）
- **idle-timeout 預設 600 秒**（10 分鐘沒有輸出即判定卡住）；**總時長 timeout 預設 3600 秒**仍保留，兩層並存
- 母任務在「等確認／等子任務」期間一律 `blocked`
- 重試上限 **2 次**（含 orchestrator 的呼叫），退避基數 **30 秒**
- **idle-timeout 實作在 worker 層**（`invoke_claude` 內自行判定並殺掉），不走 DB 的 `heartbeat_at` 欄位 —— spec §4.2 描述的是 daemon 以心跳監控，效果相同（卡住的任務會被終止），但省去跨 thread 寫 DB 的複雜度。`heartbeat_at` 欄位保留未用。

## Review Focus

1. **並行上限失效** → 同時跑超過 `parallel` 個 worker（Task 1）
2. **兩個 thread 搶同一任務** → 任務被執行兩次（Task 1）
3. **stream-json 解析不了** → 不能讓它把整個 worker 打死，也不能誤判成功（Task 2）
4. **idle-timeout 誤殺正常任務** → 有在輸出卻被判定閒置（Task 2）
5. **orchestrator 重試造成重複拆解** → 母任務不能出現兩批子任務（Task 3）

---

### Task 1: 並行多 worker

**Files:**
- Modify: `agent-orchestrator/daemon.py`
- Test: `agent-orchestrator/tests/test_daemon.py`（追加）

**Interfaces:**
- Produces:
  - `daemon.MAX_PARALLEL = 3`
  - `daemon.start_ready(conn, db_path, claude_bin="claude", timeout=3600, parallel=3) -> list[str]`（回傳本輪啟動的 task id 清單）
  - `daemon._run_worker(db_path, task_id, claude_bin, timeout) -> None`（背景 thread 的進入點）
  - `daemon.main_loop(..., parallel=MAX_PARALLEL)`

- [ ] **Step 1: 寫失敗的測試（追加）**

```python
def test_start_ready_respects_parallel_limit(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    launched = []
    monkeypatch.setattr(daemon, "_run_worker", lambda *a: launched.append(a[1]))
    for i in range(5):
        tq.add_task(conn, f"t{i}", "s", cwd=str(tmp_path))
    started = daemon.start_ready(conn, db, parallel=2)
    assert len(started) == 2
    assert len(tq.list_tasks(conn, status="running")) == 2


def test_start_ready_counts_existing_running(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    monkeypatch.setattr(daemon, "_run_worker", lambda *a: None)
    a = tq.add_task(conn, "a", "s", cwd=str(tmp_path))
    tq.set_status(conn, a, "running")  # 已經有一個在跑
    tq.add_task(conn, "b", "s", cwd=str(tmp_path))
    started = daemon.start_ready(conn, db, parallel=2)
    assert len(started) == 1  # 只剩一個名額


def test_start_ready_returns_empty_when_full(tmp_path, monkeypatch):
    conn = _conn(tmp_path)
    monkeypatch.setattr(daemon, "_run_worker", lambda *a: None)
    a = tq.add_task(conn, "a", "s", cwd=str(tmp_path))
    tq.set_status(conn, a, "running")
    assert daemon.start_ready(conn, str(tmp_path / "t.db"), parallel=1) == []


def test_worker_thread_marks_task_done(tmp_path):
    """背景執行真的會把任務跑完（不是只有啟動）。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(tmp_path, 'echo ok\nexit 0\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    daemon._run_worker(db, tid, fake, 10)
    assert tq.get_task(conn, tid)["status"] == "done"
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_daemon.py -v`
Expected: FAIL —`AttributeError: module 'daemon' has no attribute 'start_ready'`

- [ ] **Step 3: 實作（改 `daemon.py`）**

頂部補 `import threading`；新增 `MAX_PARALLEL` 與兩個函式；`main_loop` 改寫：

```python
MAX_PARALLEL = 3


def _run_worker(db_path, task_id, claude_bin, timeout):
    """背景 thread 的進入點：用自己的 DB 連線跑一個任務。"""
    conn = tq.connect(db_path)
    try:
        task = tq.get_task(conn, task_id)
        if not task:
            return
        if task.get("kind") == "orchestrator":
            orchestrator.decompose(conn, task, claude_bin=claude_bin, timeout=timeout)
        else:
            worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout)
    finally:
        conn.close()


def start_ready(conn, db_path, claude_bin="claude", timeout=3600, parallel=MAX_PARALLEL):
    """把可跑的任務填滿到 parallel 上限，背景執行。回傳本輪啟動的 task id 清單。"""
    running = len(tq.list_tasks(conn, status="running"))
    slots = max(0, parallel - running)
    started = []
    for _ in range(slots):
        task = tq.claim_next(conn)
        if not task:
            break
        threading.Thread(target=_run_worker,
                         args=(db_path, task["id"], claude_bin, timeout),
                         daemon=True).start()
        started.append(task["id"])
    return started


def main_loop(claude_bin="claude", timeout=3600, poll=5, db_path="queue.db",
              parallel=MAX_PARALLEL):
    conn = tq.connect(db_path)
    tq.init_db(conn)
    while True:
        reap_timed_out(conn, timeout)
        finalize_parents(conn, claude_bin=claude_bin, timeout=timeout)
        start_ready(conn, db_path, claude_bin=claude_bin, timeout=timeout, parallel=parallel)
        time.sleep(poll)
```

> `tick` 保留不變（同步跑一個），供測試與單任務模式使用。

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add daemon.py tests/test_daemon.py
git commit -m "feat: daemon 並行多 worker（上限預設 3）"
```

---

### Task 2: idle-timeout（stream-json 進展偵測）

**Files:**
- Modify: `agent-orchestrator/worker.py`
- Test: `agent-orchestrator/tests/test_worker.py`（追加）

**Interfaces:**
- Produces:
  - `worker.IDLE_TIMEOUT = 600`
  - `worker.invoke_claude(prompt, cwd, claude_bin="claude", timeout=3600, idle_timeout=None) -> tuple[int, str, str]`
    —— `idle_timeout` 給定時改用 `--output-format stream-json --verbose`，逐行讀取；超過閒置秒數沒有新行即殺掉並回 `code=-1`
  - `worker.run_task(..., idle_timeout=None)`

- [ ] **Step 1: 寫失敗的測試（追加）**

```python
def test_invoke_claude_streaming_returns_result(tmp_path):
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"完成了\",\"is_error\":false}'\n")
    code, out, err = worker.invoke_claude("do it", str(tmp_path),
                                          claude_bin=fake, timeout=10, idle_timeout=5)
    assert code == 0
    assert out == "完成了"


def test_invoke_claude_streaming_marks_error(tmp_path):
    fake = _fake_claude(
        tmp_path,
        "echo '{\"type\":\"result\",\"result\":\"壞了\",\"is_error\":true}'\n")
    code, out, err = worker.invoke_claude("do it", str(tmp_path),
                                          claude_bin=fake, timeout=10, idle_timeout=5)
    assert code != 0


def test_invoke_claude_idle_timeout_kills(tmp_path):
    """一直不輸出（不吐 stream 行）→ 閒置逾時殺掉。"""
    fake = _fake_claude(tmp_path, "sleep 30\n")
    code, out, err = worker.invoke_claude("do it", str(tmp_path),
                                          claude_bin=fake, timeout=60, idle_timeout=2)
    assert code == -1
    assert "閒置" in err


def test_invoke_claude_long_output_before_idle_is_survived(tmp_path):
    """有持續輸出就不該被 idle 誤殺。"""
    fake = _fake_claude(
        tmp_path,
        "for i in 1 2 3 4; do "
        "echo '{\"type\":\"system\",\"subtype\":\"x\"}'; sleep 1; done\n"
        "echo '{\"type\":\"result\",\"result\":\"ok\",\"is_error\":false}'\n")
    code, out, err = worker.invoke_claude("do it", str(tmp_path),
                                          claude_bin=fake, timeout=60, idle_timeout=2)
    assert code == 0
    assert out == "ok"


def test_run_task_passes_idle_timeout(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "sleep 30\n")
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake,
                    timeout=60, idle_timeout=2)
    got = tq.get_task(conn, tid)
    assert got["error"] and "閒置" in got["error"]
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_worker.py -v`
Expected: FAIL（`invoke_claude` 不接受 `idle_timeout`）

- [ ] **Step 3: 實作（改 `worker.py`）**

```python
STREAM_ARGS = [*CLAUDE_ARGS[:-2], "--output-format", "stream-json", "--verbose"]
IDLE_TIMEOUT = 600


def invoke_claude(prompt, cwd, claude_bin="claude", timeout=3600, idle_timeout=None):
    """跑一次 claude -p；回傳 (returncode, stdout, stderr)。不碰 DB。

    idle_timeout 給定時，改用 stream-json 逐行讀取，超過該秒數沒有新輸出即殺掉整個
    process group（returncode -1）。code 127 代表執行檔起不來。
    """
    if idle_timeout is None:
        return _invoke_text(prompt, cwd, claude_bin, timeout)
    return _invoke_streaming(prompt, cwd, claude_bin, timeout, idle_timeout)


def _invoke_text(prompt, cwd, claude_bin, timeout):
    """--output-format text：一次收完（Phase 1/2 的既有行為）。"""
    try:
        proc = subprocess.Popen(
            [claude_bin, "-p", prompt, *CLAUDE_ARGS],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        return 127, "", f"無法啟動 {claude_bin}：{exc}"
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        return -1, "", f"逾時（超過 {timeout} 秒）"
    return proc.returncode, stdout or "", stderr or ""


def _invoke_streaming(prompt, cwd, claude_bin, timeout, idle_timeout):
    """stream-json：逐行讀，偵測進展；回傳最終 result 文字。"""
    try:
        proc = subprocess.Popen(
            [claude_bin, "-p", prompt, *STREAM_ARGS],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except OSError as exc:
        return 127, "", f"無法啟動 {claude_bin}：{exc}"

    state = {"last": time.time(), "result": None, "is_error": False}

    def _reader():
        for line in proc.stdout:
            state["last"] = time.time()
            try:
                ev = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(ev, dict) and ev.get("type") == "result":
                state["result"] = ev.get("result") or ""
                state["is_error"] = bool(ev.get("is_error"))

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    started = time.time()
    while proc.poll() is None:
        time.sleep(0.5)
        now = time.time()
        if now - state["last"] > idle_timeout:
            _kill_tree(proc)
            return -1, "", f"閒置逾時（超過 {idle_timeout} 秒沒有輸出）"
        if now - started > timeout:
            _kill_tree(proc)
            return -1, "", f"逾時（超過 {timeout} 秒）"

    reader.join(timeout=5)
    if state["result"] is None:
        return 1, "", "claude 結束但沒有 result 事件"
    if state["is_error"]:
        return 1, state["result"], "claude 回報 is_error"
    return 0, state["result"], ""


def run_task(conn, task, claude_bin="claude", timeout=3600, idle_timeout=None):
    ...
    code, out, err = invoke_claude(task["spec"], cwd, claude_bin, timeout,
                                   idle_timeout=idle_timeout)
    ...
```

`STREAM_ARGS` 用 `CLAUDE_ARGS[:-2]` 去掉 `--output-format text`。**確認 `CLAUDE_ARGS` 的末尾正好是 `["--output-format", "text"]`，`[:-2]` 才正確。**

**接上 daemon（否則並行時閒置偵測等於沒開）** —— 改 `daemon._run_worker`（Task 1 建的）把 `idle_timeout` 傳進去：

```python
        if task.get("kind") == "orchestrator":
            orchestrator.decompose(conn, task, claude_bin=claude_bin, timeout=timeout)
        else:
            worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout,
                            idle_timeout=worker.IDLE_TIMEOUT)
```

並在 `tests/test_daemon.py` 追加：

```python
def test_run_worker_uses_idle_timeout(tmp_path):
    """並行路徑要啟用閒置偵測（卡住的任務會被殺，不是等滿總時長）。"""
    conn = _conn(tmp_path)
    db = str(tmp_path / "t.db")
    fake = _fake_claude(tmp_path, "sleep 30\n")
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "running")
    import worker as w
    monkey = w.IDLE_TIMEOUT
    w.IDLE_TIMEOUT = 2  # 縮短以利測試
    try:
        daemon._run_worker(db, tid, fake, 60)
    finally:
        w.IDLE_TIMEOUT = monkey
    got = tq.get_task(conn, tid)
    assert got["error"] and "閒置" in got["error"]
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add worker.py tests/test_worker.py
git commit -m "feat: worker 支援 stream-json 與 idle-timeout"
```

---

### Task 3: orchestrator 的重試

**Files:**
- Modify: `agent-orchestrator/orchestrator.py`
- Test: `agent-orchestrator/tests/test_orchestrator.py`（追加）

**Interfaces:**
- Produces: `orchestrator.RETRY = 2`；`decompose` 與 `summarize` 在 `invoke_claude` 回 `code != 0` 時最多再試 `RETRY` 次（每次之間 `ORCH_RETRY_DELAY` 秒）

- [ ] **Step 1: 寫失敗的測試（追加）**

```python
def test_decompose_retries_then_succeeds(tmp_path):
    """第一次呼叫失敗、第二次成功 → 母任務正常拆解。"""
    conn = _conn(tmp_path)
    counter = tmp_path / "n"
    fake = _fake_claude(
        tmp_path,
        f'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}\n'
        f'if [ "$n" -lt 2 ]; then exit 1; fi\n'
        f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, pid)["status"] == "blocked"


def test_decompose_gives_up_after_retries(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "exit 1\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, pid)
    assert got["status"] == "failed"
    assert "拆解失敗" in got["error"]


def test_decompose_retry_does_not_duplicate_children(tmp_path):
    """重試成功後只會有一批子任務，不會因為重試而累積。"""
    conn = _conn(tmp_path)
    counter = tmp_path / "n"
    fake = _fake_claude(
        tmp_path,
        f'n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}\n'
        f'if [ "$n" -lt 2 ]; then exit 1; fi\n'
        f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert len(orchestrator.children_of(conn, pid)) == 2  # GOOD_JSON 有 2 個
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_orchestrator.py -v`
Expected: FAIL（`test_decompose_retries_then_succeeds` 母任務會是 failed）

- [ ] **Step 3: 實作（改 `orchestrator.py`）**

```python
RETRY = 2
ORCH_RETRY_DELAY = 5  # 秒


def _invoke_with_retry(prompt, cwd, claude_bin, timeout, attempts=RETRY):
    """呼叫 claude，失敗（code != 0）時重試；回傳最後一次的 (code, out, err)。"""
    last = None
    for i in range(attempts + 1):
        last = worker.invoke_claude(prompt, cwd, claude_bin, timeout)
        if last[0] == 0:
            return last
        if i < attempts:
            time.sleep(ORCH_RETRY_DELAY)
    return last
```

`decompose` 與 `summarize` 內把 `worker.invoke_claude(...)` 換成 `_invoke_with_retry(...)`。

> 重試只在**呼叫失敗**時發生 —— 成功但解析不出 subtasks 不重試（模型已經答了，再問一次多半一樣）。

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator 拆解與彙整支援重試"
```

---

### Task 4: 並行整合與真實並行驗證

**Files:**
- Test: `agent-orchestrator/tests/test_end_to_end.py`（追加）
- Modify: `agent-orchestrator/README.md`

**Interfaces:**
- Consumes: 前面全部
- Produces: 無新介面

- [ ] **Step 1: 寫並行端到端測試**

```python
def test_parallel_run_finishes_all_tasks(tmp_path):
    """多個獨立任務並行跑完，且每個只跑一次。"""
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)
    db = str(tmp_path / "t.db")
    ran = tmp_path / "ran"
    fake = tmp_path / "fake_claude"
    fake.write_text(
        "#!/bin/bash\n"
        f'echo "$2" >> {ran}\n'
        "sleep 1\n"
        "echo ok\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    for i in range(3):
        tq.add_task(conn, f"t{i}", f"job-{i}", cwd=str(tmp_path))
    daemon.start_ready(conn, db, claude_bin=str(fake), timeout=30, parallel=3)
    for _ in range(60):  # 等背景 thread 收工
        if all(t["status"] == "done" for t in tq.list_tasks(conn)):
            break
        time.sleep(0.5)
    tasks = tq.list_tasks(conn)
    assert all(t["status"] == "done" for t in tasks)
    lines = ran.read_text().splitlines()
    assert sorted(lines) == ["job-0", "job-1", "job-2"]  # 每個只跑一次
```

> `tests/test_end_to_end.py` 需 `import time`。

- [ ] **Step 2: 執行測試**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 3: 真 claude 並行驗證**

```bash
D=/tmp/orch_p3; mkdir -p "$D"; cd ~/CCProject/agent-orchestrator
for i in 1 2 3; do
  python3 cli.py --db "$D/q.db" add "在 $D 建 f$i.txt 內容放 $i" --cwd "$D"
done
python3 -c "
import sys; sys.path.insert(0,'.')
import time, taskqueue as tq, daemon
conn = tq.connect('$D/q.db')
daemon.start_ready(conn, '$D/q.db', claude_bin='claude', timeout=600, parallel=3)
for _ in range(120):
    if all(t['status'] in ('done','failed') for t in tq.list_tasks(conn)): break
    time.sleep(2)
for t in tq.list_tasks(conn): print(t['id'], t['status'], t['title'])
"
ls "$D"
```

Expected：3 個任務皆 `done`，`f1.txt`／`f2.txt`／`f3.txt` 都產出。

- [ ] **Step 4: 更新 `README.md`**

把「Phase 1 的邊界」段改名為「Phase 2/3 的邊界」並更新成：

```markdown
- **可並行**：daemon 一次最多跑 3 個任務（`daemon.MAX_PARALLEL`）；獨立的任務自然並行，有依賴的排隊。
- **兩層逾時**：總時長 3600 秒；閒置超過 600 秒（沒有輸出）也會被判定卡住並終止。
- 失敗自動重試（上限 2 次）；拆解與彙整也各自重試。
- worker 能讀寫、能執行，**權限等同你本人**（含 Bash，不受目錄限制）。`--cwd` 只是「工作起點」，**不是權限邊界**。
```

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add README.md tests/test_end_to_end.py
git commit -m "feat: 並行整合測試與文件更新（Phase 3 完成）"
```

---

## Phase 3 完成定義

- `pytest` 全綠。
- daemon 能同時跑多個任務（不超過上限），每個任務只跑一次。
- 閒置逾時能把「卡住但沒死」的任務終止。
- orchestrator 的拆解／彙整在 API 失敗時會重試。

## 後續（不在本計畫）

- **Phase 4**：Telegram 與 command-center 入口（須先決定信任模型：誰能下指令 = 誰能在這台 Mac 上執行任意指令）。
