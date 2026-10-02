# 常駐 Agent 編排系統 — Phase 2 實作計畫（orchestrator + 重試）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓使用者丟一句話，系統用 `claude -p` 自動拆成子任務、給人確認後派工，全部完成後彙整回報；並讓失敗的任務能自動重試。

**Architecture:** 母任務是 `kind='orchestrator'` 的任務。daemon 認領到它時不跑一般 worker，而是呼叫 `claude -p` 拆解 → 寫入子任務（`blocked`）→ 母任務轉 `blocked` 等確認。使用者 `confirm` 後子任務轉 `pending`。子任務全 `done` 時 daemon 觸發彙整。重試靠 `retries` + `next_attempt_at` 兩個欄位與指數退避。

**Tech Stack:** Python 3（stdlib）、pytest。沿用既有 `claude -p` flags。

**Spec:** `docs/superpowers/specs/2026-10-01-agent-orchestrator-design.md`（§4.4 orchestrator、§7 錯誤處理）

## Global Constraints

- 任務狀態值僅限：`pending` / `running` / `blocked` / `done` / `failed`
- worker 一律用 `claude -p`，flags 固定：`--permission-mode acceptEdits --allowedTools "Read,Write,Edit,Bash,Grep,Glob" --disallowedTools "Agent,Workflow" --output-format text`
- 一律不用 shell：`subprocess` 傳 list args
- 逾時預設 **3600 秒**；`claude` 的呼叫一律用 `Popen(start_new_session=True)` + process group 終止
- **母任務在「等人確認」與「等子任務」期間狀態是 `blocked`** —— 絕不能是 `running`，否則會被 `reap_timed_out` 誤殺
- 重試上限 **2 次**（即最多執行 3 次），退避基數 **30 秒**（指數：30 → 60）
- `cwd` 是工作起點、非權限邊界；worker 權限等同使用者本人
- DB 相容性：既有 `queue.db` 必須能就地升級（加欄位），不得要求使用者重建

## Review Focus

1. **拆解回傳的不是合法 JSON / 不是預期結構** → 母任務要標 `failed` 並帶清楚原因，不能讓子任務半生不熟（Task 2）
2. **`depends_on` 越界（指向不存在的子任務索引）或自我依賴** → 建立子任務時要擋掉或忽略，不能讓 `claim_next` 永遠挑不到（Task 2）
3. **確認一個不存在／不是 orchestrator 的任務** → 要報錯，不能靜默成功（Task 3）
4. **子任務有 `failed` 時母任務的結局** → 母任務標 `failed`，不做彙整（Task 4）
5. **重試不會無限迴圈** → `retries` 到上限就 `failed`；且 `next_attempt_at` 未到時 `claim_next` 不得挑它（Task 5）

---

### Task 1: Schema 擴充（kind / next_attempt_at + 就地升級）

**Files:**
- Modify: `agent-orchestrator/taskqueue.py`
- Test: `agent-orchestrator/tests/test_taskqueue.py`（追加）

**Interfaces:**
- Produces:
  - `init_db(conn)` 現在同時做 idempotent migration
  - `add_task(..., kind="task")` 多一個參數
  - 任務 dict 多出 `kind`（預設 `"task"`）與 `next_attempt_at`（預設 `None`）

- [ ] **Step 1: 寫失敗的測試（追加到 `tests/test_taskqueue.py`）**

```python
def test_new_task_has_kind_and_next_attempt(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "s")
    t = tq.get_task(conn, tid)
    assert t["kind"] == "task"
    assert t["next_attempt_at"] is None


def test_add_task_accepts_orchestrator_kind(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "plan", "big", kind="orchestrator")
    assert tq.get_task(conn, tid)["kind"] == "orchestrator"


def test_init_db_migrates_legacy_table(tmp_path):
    """既有的舊 DB（沒有 kind 欄位）能就地升級，不需重建。"""
    db = str(tmp_path / "legacy.db")
    conn = tq.connect(db)
    conn.execute(
        "CREATE TABLE tasks ("
        "id TEXT PRIMARY KEY, title TEXT, spec TEXT, status TEXT, "
        "depends_on TEXT, created_at REAL)"
    )
    conn.execute(
        "INSERT INTO tasks VALUES ('old1', 'legacy', 's', 'pending', '[]', 1.0)")
    tq.init_db(conn)  # 不該炸，且要補上欄位
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    assert "kind" in cols and "next_attempt_at" in cols
    assert tq.get_task(conn, "old1")["kind"] == "task"


def test_claim_next_skips_task_before_next_attempt(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "s")
    tq.set_status(conn, tid, "pending", next_attempt_at=time.time() + 3600)
    assert tq.claim_next(conn) is None  # 還沒到重試時間


def test_claim_next_takes_task_whose_next_attempt_passed(tmp_path):
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "t", "s")
    tq.set_status(conn, tid, "pending", next_attempt_at=time.time() - 1)
    assert tq.claim_next(conn)["id"] == tid
```

> 這個測試檔開頭已有 `import time`（見 Phase 1）。若沒有就補上。

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_taskqueue.py -v`
Expected: FAIL（`kind` KeyError / `add_task` 不接受 kind / `claim_next` 撈到不該撈的）

- [ ] **Step 3: 實作**

`taskqueue.py` 的 `SCHEMA` 內 `tasks` 表加兩欄（放在 `heartbeat_at` 之後）：

```sql
    heartbeat_at REAL,
    kind           TEXT NOT NULL DEFAULT 'task',
    next_attempt_at REAL
```

`connect` 之後補上 migration 與 `init_db` 改寫：

```python
def init_db(conn):
    conn.executescript(SCHEMA)
    _migrate(conn)


def _migrate(conn):
    """就地把舊 DB 補上新欄位（idempotent）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "kind" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'task'")
    if "next_attempt_at" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_attempt_at REAL")
```

`add_task` 多一個 `kind` 參數：

```python
def add_task(conn, title, spec, cwd=None, depends_on=None, source="cli", kind="task"):
    task_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO tasks (id, title, spec, status, depends_on, cwd, source, kind, created_at)"
        " VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
        (task_id, title, spec, json.dumps(depends_on or []), cwd, source, kind, time.time()),
    )
    return task_id
```

`claim_next` 的查詢加上 `next_attempt_at` 條件：

```python
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending'"
            " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY created_at",
            (time.time(),),
        ).fetchall()
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_taskqueue.py -v`
Expected: PASS（13 passed）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add taskqueue.py tests/test_taskqueue.py
git commit -m "feat: schema 擴充 kind/next_attempt_at 與就地升級"
```

---

### Task 2: 抽出 claude 呼叫 + orchestrator 拆解

**Files:**
- Modify: `agent-orchestrator/worker.py`（抽出 `invoke_claude`）
- Create: `agent-orchestrator/orchestrator.py`
- Test: `agent-orchestrator/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `taskqueue.*`
- Produces:
  - `worker.invoke_claude(prompt: str, cwd: str, claude_bin: str, timeout: int) -> tuple[int, str, str]`（回傳 `returncode, stdout, stderr`；不碰 DB）
  - `orchestrator.parse_subtasks(text: str) -> list[dict]`（丟 `ValueError` 當格式不對）
  - `orchestrator.decompose(conn, parent: dict, claude_bin="claude", timeout=600) -> list[str]`（回傳子任務 id 清單）

- [ ] **Step 1: 寫失敗的測試**

`agent-orchestrator/tests/test_orchestrator.py`：

```python
import stat

import orchestrator
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


GOOD_JSON = (
    '{"subtasks": ['
    '{"title": "查產品", "spec": "查市面上產品", "cwd": "/tmp", "depends_on": []},'
    '{"title": "彙整", "spec": "寫成報告", "cwd": "/tmp", "depends_on": [0]}'
    ']}'
)


def test_parse_subtasks_plain_json():
    subs = orchestrator.parse_subtasks(GOOD_JSON)
    assert len(subs) == 2
    assert subs[0]["title"] == "查產品"
    assert subs[1]["depends_on"] == [0]


def test_parse_subtasks_strips_code_fence():
    fenced = "```json\n" + GOOD_JSON + "\n```"
    assert len(orchestrator.parse_subtasks(fenced)) == 2


def test_parse_subtasks_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks("我很樂意幫忙，但先讓我說明一下...")


def test_parse_subtasks_rejects_out_of_range_dep():
    bad = '{"subtasks": [{"title": "a", "spec": "s", "depends_on": [5]}]}'
    import pytest
    with pytest.raises(ValueError):
        orchestrator.parse_subtasks(bad)


def test_decompose_creates_blocked_children_and_blocks_parent(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "研究並報告", cwd=str(tmp_path), kind="orchestrator")
    child_ids = orchestrator.decompose(conn, tq.get_task(conn, pid),
                                       claude_bin=fake, timeout=10)
    assert len(child_ids) == 2
    assert tq.get_task(conn, pid)["status"] == "blocked"
    c0 = tq.get_task(conn, child_ids[0])
    assert c0["status"] == "blocked"
    assert c0["parent_id"] == pid
    # 第二個子任務的 depends_on 已映射成真實 id，不是索引
    c1 = tq.get_task(conn, child_ids[1])
    assert c1["depends_on"] == [child_ids[0]]


def test_decompose_marks_parent_failed_on_bad_output(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo 'not json at all'\n")
    pid = tq.add_task(conn, "大任務", "研究並報告", cwd=str(tmp_path), kind="orchestrator")
    orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, pid)
    assert got["status"] == "failed"
    assert got["error"]
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_orchestrator.py -v`
Expected: FAIL —`ModuleNotFoundError: No module named 'orchestrator'`

- [ ] **Step 3a: 在 `worker.py` 抽出 `invoke_claude`**

把 `run_task` 中「起行程、收輸出、逾時殺 process group」的部分抽成純函式；`run_task` 改為呼叫它。**`run_task` 對外的行為與既有測試不變。**

```python
def invoke_claude(prompt, cwd, claude_bin="claude", timeout=3600):
    """跑一次 claude -p；回傳 (returncode, stdout, stderr)。不碰 DB。
    returncode 為 -1 代表逾時（已終止整個 process group）。"""
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
```

`run_task` 的主體改寫成：

```python
def run_task(conn, task, claude_bin="claude", timeout=3600):
    task_id = task["id"]
    cwd = task.get("cwd")
    if not cwd or not os.path.isdir(cwd):
        _fail(conn, task, f"工作目錄不存在或未指定：{cwd}", retryable=False)
        return False, ""

    tq.set_status(conn, task_id, "running", worker_pid=os.getpid())
    code, out, err = invoke_claude(task["spec"], cwd, claude_bin, timeout)

    if code == -1:
        _fail(conn, task, err, retryable=True)
        return False, ""
    if code == 127:
        _fail(conn, task, err, retryable=False)
        return False, ""
    out = out.strip()
    if code == 0:
        tq.set_status(conn, task_id, "done", result=out, error=None, finished_at=time.time())
        return True, out
    _fail(conn, task, err.strip() or f"exit code {code}", retryable=True)
    return False, out
```

> `_fail` 在 Task 5 才完整定義；**本 Task** 先給最小版本（直接標 failed），Task 5 再擴充成含重試：
> ```python
> def _fail(conn, task, error, retryable):
>     tq.set_status(conn, task["id"], "failed", error=error[:2000], finished_at=time.time())
> ```
>
> **`worker_pid` 語意變更**：Phase 1 記的是 claude 子行程的 pid，抽出 `invoke_claude` 後改記「處理這個任務的行程 pid」(`os.getpid()`)。對診斷仍有用；「殺掉卡住的 claude」留給 Phase 3 的 idle-timeout 處理。

`worker.py` 頂部補 `import os`（若尚未有）。

- [ ] **Step 3b: 建立 `orchestrator.py`**

```python
"""把一個大任務拆解成子任務。"""
import json
import re
import time

import taskqueue as tq
import worker

PLAN_PROMPT = """你是任務規劃器。把下面這個任務拆解成 2 到 5 個子任務。

任務：{spec}

可用的工作目錄：{cwd}

規則：
- 每個子任務都要有完整、可獨立執行的描述（不要寫「同上」這種）
- 若某個子任務需要其他子任務先完成，用 depends_on 標明（填子任務索引，從 0 開始）
- 彼此獨立的子任務不要互相依賴，讓它們能並行

只輸出 JSON，不要任何其他文字或解說：
{{"subtasks": [{{"title": "短標題", "spec": "完整描述", "cwd": "{cwd}", "depends_on": []}}]}}
"""


def parse_subtasks(text):
    """從 claude 的輸出取出 subtasks 清單；格式不對就丟 ValueError。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        data = json.loads(cleaned)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"拆解結果不是合法 JSON：{exc}") from exc
    subs = data.get("subtasks") if isinstance(data, dict) else None
    if not isinstance(subs, list) or not subs:
        raise ValueError("拆解結果缺少有效的 subtasks 陣列")

    n = len(subs)
    for sub in subs:
        deps = sub.get("depends_on") or []
        if not isinstance(deps, list):
            raise ValueError(f"depends_on 不是陣列：{deps!r}")
        for d in deps:
            if not isinstance(d, int) or d < 0 or d >= n:
                raise ValueError(f"depends_on 索引越界：{d!r}")
    return subs


def decompose(conn, parent, claude_bin="claude", timeout=600):
    """呼叫 claude 拆解 parent，建立 blocked 子任務，parent 轉 blocked。回傳子任務 id 清單。"""
    cwd = parent.get("cwd") or "."
    prompt = PLAN_PROMPT.format(spec=parent["spec"], cwd=cwd)
    code, out, err = worker.invoke_claude(prompt, cwd, claude_bin, timeout)

    if code != 0:
        tq.set_status(conn, parent["id"], "failed",
                      error=(err.strip() or f"拆解失敗 exit={code}")[:2000],
                      finished_at=time.time())
        return []

    try:
        subs = parse_subtasks(out)
    except ValueError as exc:
        tq.set_status(conn, parent["id"], "failed",
                      error=f"拆解結果無法解析：{exc}"[:2000], finished_at=time.time())
        return []

    id_by_index = {}
    for i, sub in enumerate(subs):
        tid = tq.add_task(
            conn,
            title=sub.get("title") or f"子任務 {i + 1}",
            spec=sub.get("spec") or sub.get("title") or "",
            cwd=sub.get("cwd") or cwd,
            source=parent.get("source", "cli"),
        )
        tq.set_status(conn, tid, "blocked", parent_id=parent["id"])
        id_by_index[i] = tid

    for i, sub in enumerate(subs):  # 依賴要等 id 都建好才能映射
        deps = [id_by_index[d] for d in (sub.get("depends_on") or [])]
        tq.set_status(conn, id_by_index[i], "blocked", depends_on=json.dumps(deps))

    tq.set_status(conn, parent["id"], "blocked", finished_at=None)
    return list(id_by_index.values())
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS（既有 31 + 新增全部；`test_worker.py` 與 `test_end_to_end.py` 不受 `invoke_claude` 重構影響）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add worker.py orchestrator.py tests/test_orchestrator.py tests/test_worker.py
git commit -m "feat: 抽出 invoke_claude 並實作 orchestrator 拆解"
```

---

### Task 3: orchestrator 確認與彙整

**Files:**
- Modify: `agent-orchestrator/orchestrator.py`
- Test: `agent-orchestrator/tests/test_orchestrator.py`（追加）

**Interfaces:**
- Produces:
  - `orchestrator.confirm(conn, parent_id: str) -> int`（回傳放行的子任務數；非 orchestrator 或找不到 → `ValueError`）
  - `orchestrator.summarize(conn, parent: dict, claude_bin="claude", timeout=600) -> bool`
  - `orchestrator.children_of(conn, parent_id: str) -> list[dict]`

- [ ] **Step 1: 寫失敗的測試（追加）**

```python
def test_confirm_releases_children(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, f"echo '{GOOD_JSON}'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    child_ids = orchestrator.decompose(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    n = orchestrator.confirm(conn, pid)
    assert n == 2
    assert tq.get_task(conn, child_ids[0])["status"] == "pending"
    assert tq.get_task(conn, pid)["status"] == "blocked"  # 母任務仍在等子任務


def test_confirm_rejects_non_orchestrator(tmp_path):
    import pytest
    conn = _conn(tmp_path)
    tid = tq.add_task(conn, "普通任務", "x")
    with pytest.raises(ValueError):
        orchestrator.confirm(conn, tid)


def test_confirm_rejects_missing(tmp_path):
    import pytest
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        orchestrator.confirm(conn, "nope")


def test_summarize_writes_parent_result(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '總結：一切完成'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    tq.set_status(conn, pid, "blocked")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "done", result="子任務一做好了", parent_id=pid)

    ok = orchestrator.summarize(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert ok
    got = tq.get_task(conn, pid)
    assert got["status"] == "done"
    assert "總結" in got["result"]


def test_summarize_fails_parent_when_child_failed(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '不該被呼叫'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "failed", error="壞了", parent_id=pid)
    orchestrator.summarize(conn, tq.get_task(conn, pid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, pid)["status"] == "failed"
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_orchestrator.py -v`
Expected: FAIL —`AttributeError: module 'orchestrator' has no attribute 'confirm'`

- [ ] **Step 3: 實作（追加到 `orchestrator.py`）**

```python
def children_of(conn, parent_id):
    return [t for t in tq.list_tasks(conn) if t.get("parent_id") == parent_id]


def confirm(conn, parent_id):
    """把某個 orchestrator 任務的 blocked 子任務放行為 pending。回傳放行數。"""
    parent = tq.get_task(conn, parent_id)
    if not parent:
        raise ValueError(f"找不到任務：{parent_id}")
    if parent.get("kind") != "orchestrator":
        raise ValueError(f"任務 {parent_id} 不是 orchestrator 任務")
    n = 0
    for child in children_of(conn, parent_id):
        if child["status"] == "blocked":
            tq.set_status(conn, child["id"], "pending")
            n += 1
    return n


SUMMARIZE_PROMPT = """以下是一個大任務拆解後、各子任務的執行結果。請用繁體中文寫一段簡短總結（5 行內）：完成了什麼、有沒有要注意的地方。

原始任務：{spec}

{parts}
"""


def summarize(conn, parent, claude_bin="claude", timeout=600):
    """彙整子任務結果寫回母任務。任何子任務 failed → 母任務 failed。回傳是否成功。"""
    children = children_of(conn, parent["id"])
    failed = [c for c in children if c["status"] == "failed"]
    if failed:
        names = "、".join(c["title"] for c in failed)
        tq.set_status(conn, parent["id"], "failed",
                      error=f"有子任務失敗：{names}"[:2000], finished_at=time.time())
        return False

    parts = "\n\n".join(
        f"### {c['title']}\n{(c.get('result') or '(無產出)')[:2000]}" for c in children
    )
    prompt = SUMMARIZE_PROMPT.format(spec=parent["spec"], parts=parts)
    cwd = parent.get("cwd") or "."
    code, out, err = worker.invoke_claude(prompt, cwd, claude_bin, timeout)
    if code != 0:
        tq.set_status(conn, parent["id"], "failed",
                      error=(err.strip() or f"彙整失敗 exit={code}")[:2000],
                      finished_at=time.time())
        return False

    tq.set_status(conn, parent["id"], "done", result=out.strip(),
                  error=None, finished_at=time.time())
    return True
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator 確認與彙整"
```

---

### Task 4: daemon 整合（kind 分派 + 自動彙整）

**Files:**
- Modify: `agent-orchestrator/daemon.py`
- Test: `agent-orchestrator/tests/test_daemon.py`（追加）

**Interfaces:**
- Consumes: `orchestrator.decompose` / `confirm` / `summarize` / `children_of`
- Produces:
  - `daemon.tick(conn, claude_bin="claude", timeout=3600) -> int` 行為擴充：認領到 `kind='orchestrator'` 且無子任務者 → 走拆解；每輪先嘗試自動彙整已完成的母任務

- [ ] **Step 1: 寫失敗的測試（追加）**

```python
def test_tick_decomposes_orchestrator_task(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo \'{"subtasks": [{"title": "a", "spec": "sa", "cwd": "%s", "depends_on": []}]}\'\n' % tmp_path)
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    assert daemon.tick(conn, claude_bin=fake, timeout=10) == 1
    assert tq.get_task(conn, pid)["status"] == "blocked"


def test_tick_summarizes_when_children_done(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "echo '彙整完成'\n")
    pid = tq.add_task(conn, "大任務", "x", cwd=str(tmp_path), kind="orchestrator")
    c1 = tq.add_task(conn, "子1", "s1", cwd=str(tmp_path))
    tq.set_status(conn, c1, "done", result="子一完成", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    daemon.tick(conn, claude_bin=fake, timeout=10)  # 沒有 pending 任務，但要觸發彙整
    assert tq.get_task(conn, pid)["status"] == "done"
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_daemon.py -v`
Expected: FAIL（`tick` 未呼叫 orchestrator，母任務停在 blocked）

- [ ] **Step 3: 實作（改寫 `daemon.py` 的 `tick`）**

```python
import orchestrator


def finalize_parents(conn, claude_bin="claude", timeout=3600):
    """把『子任務全部結束』的 blocked orchestrator 母任務收尾（彙整或標失敗）。回傳處理數。"""
    done = 0
    for parent in tq.list_tasks(conn, status="blocked"):
        if parent.get("kind") != "orchestrator":
            continue
        children = orchestrator.children_of(conn, parent["id"])
        if not children:
            continue  # 還沒拆解（或已拆解但被 confirm 前的狀態由 tick 認領處理）
        if all(c["status"] in ("done", "failed") for c in children):
            orchestrator.summarize(conn, parent, claude_bin=claude_bin, timeout=timeout)
            done += 1
    return done


def tick(conn, claude_bin="claude", timeout=3600):
    """跑一輪：收割逾時 → 收尾已完成的母任務 → 認領並執行一個任務。"""
    reap_timed_out(conn, timeout)
    finalize_parents(conn, claude_bin=claude_bin, timeout=timeout)

    task = tq.claim_next(conn)
    if not task:
        return 0
    if task.get("kind") == "orchestrator":
        orchestrator.decompose(conn, task, claude_bin=claude_bin, timeout=timeout)
    else:
        worker.run_task(conn, task, claude_bin=claude_bin, timeout=timeout)
    return 1
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add daemon.py tests/test_daemon.py
git commit -m "feat: daemon 支援 orchestrator 拆解與自動彙整"
```

---

### Task 5: 重試與指數退避

**Files:**
- Modify: `agent-orchestrator/worker.py`
- Test: `agent-orchestrator/tests/test_worker.py`（追加）

**Interfaces:**
- Produces: `worker._fail(conn, task, error, retryable) -> None` 的完整版；常數 `MAX_RETRIES = 2`、`BACKOFF_BASE = 30`

- [ ] **Step 1: 寫失敗的測試（追加 4 個，並修改 2 個 Phase 1 的）**

**Phase 1 的兩個測試要改 —— 這是刻意的行為變更**（逾時與非 0 結束現在會先退回 `pending` 重試，不再直接 `failed`）。把它們改成「已達重試上限才 `failed`」：

```python
def test_run_task_nonzero_exit_marks_failed(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo "boom" >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "pending", retries=worker.MAX_RETRIES)
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "boom" in got["error"]


def test_run_task_timeout_marks_failed(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'sleep 30\n')
    tid = tq.add_task(conn, "t", "spec", cwd=str(tmp_path))
    tq.set_status(conn, tid, "pending", retries=worker.MAX_RETRIES)
    ok, _ = worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=1)
    assert not ok
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "逾時" in got["error"] or "timeout" in got["error"]
```

**追加的 4 個**：

```python
def test_run_task_retries_on_nonzero_exit(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo boom >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, tid)
    assert got["status"] == "pending"          # 退回佇列等重試
    assert got["retries"] == 1
    assert got["next_attempt_at"] > time.time()


def test_run_task_gives_up_after_max_retries(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, 'echo boom >&2\nexit 3\n')
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    tq.set_status(conn, tid, "pending", retries=worker.MAX_RETRIES)
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    got = tq.get_task(conn, tid)
    assert got["status"] == "failed"
    assert "boom" in got["error"]


def test_run_task_does_not_retry_missing_cwd(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "exit 0\n")
    tid = tq.add_task(conn, "t", "s", cwd="/nonexistent_dir_xyz")
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=10)
    assert tq.get_task(conn, tid)["status"] == "failed"   # 環境問題不重試


def test_run_task_retries_on_timeout(tmp_path):
    conn = _conn(tmp_path)
    fake = _fake_claude(tmp_path, "sleep 30\n")
    tid = tq.add_task(conn, "t", "s", cwd=str(tmp_path))
    worker.run_task(conn, tq.get_task(conn, tid), claude_bin=fake, timeout=1)
    assert tq.get_task(conn, tid)["status"] == "pending"
```

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_worker.py -v`
Expected: FAIL（目前一律標 `failed`，不會退回 `pending`）

- [ ] **Step 3: 實作（取代 Task 2 的臨時 `_fail`）**

```python
MAX_RETRIES = 2
BACKOFF_BASE = 30  # 秒；退避為 BACKOFF_BASE * 2**retries


def _fail(conn, task, error, retryable):
    """失敗收尾：可重試且未達上限 → 退回 pending 並排下次時間；否則標 failed。"""
    error = (error or "未知錯誤")[:2000]
    if retryable and task["retries"] < MAX_RETRIES:
        delay = BACKOFF_BASE * (2 ** task["retries"])
        tq.set_status(conn, task["id"], "pending",
                      retries=task["retries"] + 1,
                      next_attempt_at=time.time() + delay,
                      error=error, started_at=None, worker_pid=None)
    else:
        tq.set_status(conn, task["id"], "failed", error=error, finished_at=time.time())
```

- [ ] **Step 4: 執行測試，確認通過**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS（含 Step 1 改過的兩個 Phase 1 測試）

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject/agent-orchestrator
git add worker.py tests/test_worker.py
git commit -m "feat: 任務失敗自動重試與指數退避"
```

---

### Task 6: CLI（plan / confirm）+ 端到端

**Files:**
- Modify: `agent-orchestrator/cli.py`
- Test: `agent-orchestrator/tests/test_cli.py`（追加）、`agent-orchestrator/tests/test_end_to_end.py`（追加）
- Modify: `agent-orchestrator/README.md`

**Interfaces:**
- Produces: `cli.cmd_plan(args)`、`cli.cmd_confirm(args)`；子命令 `orch plan "..."` / `orch confirm <id>`

- [ ] **Step 1: 寫失敗的測試（追加）**

`tests/test_cli.py`：

```python
def test_cmd_plan_creates_orchestrator_task(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    work = tmp_path / "w"
    work.mkdir()
    cli.cmd_plan(argparse.Namespace(db=db, spec="研究並報告", cwd=str(work)))
    tid = capsys.readouterr().out.strip()
    conn = tq.connect(db)
    got = tq.get_task(conn, tid)
    assert got["kind"] == "orchestrator"
    assert got["cwd"] == str(work)


def test_cmd_confirm_releases_children(tmp_path, capsys):
    db = str(tmp_path / "t.db")
    conn = tq.connect(db)
    tq.init_db(conn)
    pid = tq.add_task(conn, "母", "x", kind="orchestrator")
    c1 = tq.add_task(conn, "子", "s")
    tq.set_status(conn, c1, "blocked", parent_id=pid)
    tq.set_status(conn, pid, "blocked")
    cli.cmd_confirm(argparse.Namespace(db=db, id=pid))
    assert tq.get_task(conn, c1)["status"] == "pending"
```

`tests/test_end_to_end.py`：

```python
def test_orchestrator_end_to_end(tmp_path):
    """plan → 拆解 → confirm → 子任務執行 → 自動彙整。"""
    conn = tq.connect(str(tmp_path / "t.db"))
    tq.init_db(conn)

    children_json = (
        '{"subtasks": ['
        '{"title": "甲", "spec": "做甲", "cwd": "%s", "depends_on": []},'
        '{"title": "乙", "spec": "做乙", "cwd": "%s", "depends_on": [0]}'
        ']}' % (tmp_path, tmp_path)
    )
    fake = tmp_path / "fake_claude"
    fake.write_text(
        "#!/bin/bash\n"
        "case \"$2\" in\n"
        f"  *規劃器*) echo '{children_json}' ;;\n"
        "  *總結*) echo '總結完成' ;;\n"
        "  *) echo '子任務完成' ;;\n"
        "esac\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    pid = tq.add_task(conn, "大任務", "規劃器請拆解", cwd=str(tmp_path), kind="orchestrator")
    daemon.tick(conn, claude_bin=str(fake), timeout=10)          # 拆解
    assert tq.get_task(conn, pid)["status"] == "blocked"
    orchestrator.confirm(conn, pid)                               # 放行
    for _ in range(4):                                            # 跑子任務直到沒有 pending
        daemon.tick(conn, claude_bin=str(fake), timeout=10)
    assert tq.get_task(conn, pid)["status"] == "done"
    assert "總結" in tq.get_task(conn, pid)["result"]
```

> `tests/test_end_to_end.py` 需 `import orchestrator`。

- [ ] **Step 2: 執行測試，確認失敗**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest tests/test_cli.py tests/test_end_to_end.py -v`
Expected: FAIL —`module 'cli' has no attribute 'cmd_plan'`

- [ ] **Step 3: 實作（`cli.py`）**

```python
def cmd_plan(args):
    conn = tq.connect(args.db)
    tq.init_db(conn)
    cwd = os.path.abspath(args.cwd) if args.cwd else None
    task_id = tq.add_task(conn, args.spec[:60], args.spec, cwd=cwd, kind="orchestrator")
    print(task_id)


def cmd_confirm(args):
    import orchestrator
    conn = tq.connect(args.db)
    tq.init_db(conn)
    try:
        n = orchestrator.confirm(conn, args.id)
    except ValueError as exc:
        print(f"無法確認：{exc}")
        return
    print(f"已放行 {n} 個子任務")
```

`build_parser()` 內加：

```python
    pl = sub.add_parser("plan", help="丟一個大任務，讓系統拆解")
    pl.add_argument("spec")
    pl.add_argument("--cwd", help="工作目錄")
    pl.set_defaults(func=cmd_plan)

    cf = sub.add_parser("confirm", help="確認拆解、放行子任務")
    cf.add_argument("id")
    cf.set_defaults(func=cmd_confirm)
```

- [ ] **Step 4: 執行全部測試**

Run: `cd ~/CCProject/agent-orchestrator && python3 -m pytest -q`
Expected: PASS

- [ ] **Step 5: 用真 claude 手動驗證一次並把輸出記進 commit**

```bash
D=/tmp/orch_p2; mkdir -p "$D"; cd ~/CCProject/agent-orchestrator
TID=$(python3 cli.py --db "$D/q.db" plan "在這個目錄寫兩個檔 a.txt 內容 A、b.txt 內容 B，然後回報" --cwd "$D")
python3 -c "
import sys; sys.path.insert(0,'.')
import taskqueue as tq, daemon
conn = tq.connect('$D/q.db'); daemon.tick(conn, claude_bin='claude', timeout=600)
print('parent:', tq.get_task(conn,'$TID')['status'])
"
python3 cli.py --db "$D/q.db" confirm "$TID"
python3 -c "
import sys; sys.path.insert(0,'.')
import taskqueue as tq, daemon
conn = tq.connect('$D/q.db')
for _ in range(10): daemon.tick(conn, claude_bin='claude', timeout=600)
"
python3 cli.py --db "$D/q.db" log "$TID"
```

Expected：母任務 `done`、有彙整文字；`ls "$D"` 看得到 a.txt / b.txt。

- [ ] **Step 6: 更新 `README.md` 並 Commit**

README 的「用法」段加上：

```bash
# 丟一個大任務，讓系統拆解（daemon 會把它拆成子任務）
python3 cli.py plan "研究 X 並整理成報告" --cwd /path/to/工作目錄

# 看它拆成什麼（daemon 拆完後 ls 看得到子任務），確認後放行
python3 cli.py confirm <母任務 id>
```

```bash
cd ~/CCProject/agent-orchestrator
git add cli.py README.md tests/test_cli.py tests/test_end_to_end.py
git commit -m "feat: CLI plan/confirm 與 orchestrator 端到端

手動驗證（真 claude）：
<貼上 Step 5 的實際輸出摘要>"
```

---

## Phase 2 完成定義

- `pytest` 全綠。
- `cli.py plan` → daemon 拆解 → `cli.py confirm` → 子任務執行 → 自動彙整，全流程可用。
- 失敗任務會自動重試（最多 2 次，指數退避），逾時與非 0 結束會重試、環境問題不重試。

## 後續（不在本計畫）

- **Phase 3**：並行多 worker、依賴鏈的並行度控制、idle-timeout（真實進展偵測）
- **Phase 4**：Telegram 與 command-center 入口（含信任模型決定）
