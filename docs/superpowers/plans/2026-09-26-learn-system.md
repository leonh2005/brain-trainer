# AI 學習系統（learn-system）實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建一套以概念為中心、可套用任何領域的 AI 學習系統，第一個領域為 Python。

**Architecture:** 單體 Flask 應用（port 5990）＋ SQLite 儲存 ＋ Claude Agent SDK 作為「大腦」。需要腦的工作（生成智識地圖、出題、批改、對話）走 Agent SDK；執行學習者程式碼由 Flask 自己的隔離執行器處理，不交給 Agent。前端為兩個頁面（領域清單／領域頁），以知識點為中心，對話收斂成右側可收合抽屜。

**Tech Stack:** Python 3、Flask、SQLite（stdlib `sqlite3`）、`claude-agent-sdk`、pytest

**Spec:** `docs/superpowers/specs/2026-09-26-learn-system-design.md`

## Global Constraints

- Port 固定 **5990**
- 所有專案檔案位於 `~/CCProject/learn-system/`
- **不得硬編碼任何 token**；需要密鑰時讀 `~/CCProject/.secrets/`
- **不得寫死 Python**：所有生成提示詞由「領域名稱 + 使用者勾選目標」驅動
- 學習者程式碼執行**逾時 5 秒**即終止
- 掌握度規則：最近 5 次作答中 ≥4 次 `correct` → `mastered`；`partial` 不計入 `correct`；未滿 5 次不進 `mastered`
- 測試一律用 pytest，**Claude 呼叫必須以假回應取代**（真呼叫既花錢又不穩定）
- 所有 DB 時間欄位為 ISO8601 字串
- 前端所有來自 DB 的字串插入 DOM 前必須轉義（`textContent`，非 `innerHTML`）

## Review Focus

以下五項是 spec 隱含、但容易在實作中被漏掉而讓人踩到的行為：

1. **學習者程式碼讀 stdin 而卡住** — 必須是「逾時終止」而非「永久卡住整個服務」。由 Task 3 的逾時測試涵蓋。
2. **Claude 回傳非 JSON 或畸形 JSON** — 必須拋出可辨識的錯誤讓上層顯示重試，而非 500 崩潰或把垃圾寫進 DB。由 Task 4 的解析測試涵蓋。
3. **AI 幻想出根本不存在的 bug** — `read` 題若原版與修正版行為一致，代表題目無效，必須作廢重出而非拿來考人。由 Task 6 的驗證測試涵蓋。
4. **領域名稱／概念名稱含 HTML 或引號** — 前端顯示時必須轉義，否則頁面被注入字串破壞。由 Task 9 的轉義測試涵蓋。
5. **地圖生成回傳 0 個概念** — 前端必須顯示「生成失敗／重試」而非永遠轉圈的空畫面。由 Task 5 的空狀態測試涵蓋。

---

### Task 1: 專案骨架與資料層

**Files:**
- Create: `learn-system/requirements.txt`
- Create: `learn-system/config.py`
- Create: `learn-system/db.py`
- Create: `learn-system/tests/__init__.py`
- Create: `learn-system/tests/test_db.py`
- Create: `learn-system/pytest.ini`

**Interfaces:**
- Consumes: 無
- Produces:
  - `config.DB_PATH: Path`、`config.BASE_DIR: Path`
  - `db.connect() -> sqlite3.Connection`
  - `db.init_db(conn) -> None`
  - `db.create_domain(conn, name: str, goals: list[str], verify_sources: bool, executable: bool = True) -> int`
  - `db.get_domain(conn, domain_id: int) -> dict | None`
  - `db.list_domains(conn) -> list[dict]`
  - `db.set_domain_status(conn, domain_id: int, status: str) -> None`
  - `db.set_domain_executable(conn, domain_id: int, executable: bool) -> None`
  - `db.set_domain_chat_session(conn, domain_id: int, session_id: str) -> None`
  - `db.create_concept(conn, domain_id: int, name: str, section: str, description: str | None = None, source_urls: list[str] | None = None) -> int`
  - `db.list_concepts(conn, domain_id: int) -> list[dict]`
  - `db.get_concept(conn, concept_id: int) -> dict | None`
  - `db.set_concept_description(conn, concept_id: int, description: str) -> None`
  - `db.set_concept_status(conn, concept_id: int, status: str) -> None`
  - `db.create_question(conn, concept_id: int, goal_type: str, prompt: str, payload: dict, reference_answer: str) -> int`
  - `db.get_question(conn, question_id: int) -> dict | None`
  - `db.list_questions(conn, concept_id: int) -> list[dict]`
  - `db.create_attempt(conn, question_id: int, answer: str, verdict: str, feedback: str, root_cause: str | None = None) -> int`
  - `db.list_attempts_for_concept(conn, concept_id: int, limit: int = 5) -> list[dict]`（**最新在前**）
  - `db.list_attempts_for_domain(conn, domain_id: int) -> list[dict]`（錯題本用）

- [ ] **Step 1: 建立專案目錄與依賴**

```bash
mkdir -p ~/CCProject/learn-system/tests ~/CCProject/learn-system/templates ~/CCProject/learn-system/static
```

`learn-system/requirements.txt`：

```
flask>=3.0
claude-agent-sdk>=0.1
pytest>=8.0
```

`learn-system/pytest.ini`：

```ini
[pytest]
testpaths = tests
```

建立 venv 並安裝（沿用 command-center 的做法，使用獨立 venv）：

```bash
cd ~/CCProject/learn-system && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

- [ ] **Step 2: 寫失敗的測試**

`learn-system/tests/test_db.py`：

```python
import pytest

from learn_system import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init_db(c)
    yield c
    c.close()


def test_create_and_get_domain(conn):
    did = db.create_domain(conn, "python", ["write", "exam"], verify_sources=False)
    d = db.get_domain(conn, did)
    assert d["name"] == "python"
    assert d["goals"] == ["write", "exam"]
    assert d["verify_sources"] == 0
    assert d["executable"] == 1
    assert d["status"] == "generating"


def test_list_domains(conn):
    db.create_domain(conn, "python", ["write"], False)
    db.create_domain(conn, "economics", ["principle"], False)
    names = [d["name"] for d in db.list_domains(conn)]
    assert names == ["python", "economics"]


def test_concepts_roundtrip(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    c = db.get_concept(conn, cid)
    assert c["name"] == "閉包"
    assert c["section"] == "consensus"
    assert c["status"] == "untested"
    assert c["description"] is None
    db.set_concept_description(conn, cid, "說明文字")
    assert db.get_concept(conn, cid)["description"] == "說明文字"


def test_concept_source_urls_json(conn):
    did = db.create_domain(conn, "python", ["write"], True)
    cid = db.create_concept(conn, did, "GIL", "consensus", source_urls=["https://x"])
    assert db.get_concept(conn, cid)["source_urls"] == ["https://x"]


def test_question_and_attempt(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    qid = db.create_question(conn, cid, "write", "寫一個 counter", {"test_code": "pass"}, "參考解")
    q = db.get_question(conn, qid)
    assert q["payload"] == {"test_code": "pass"}
    assert q["goal_type"] == "write"
    db.create_attempt(conn, qid, "我的答案", "wrong", "錯在 late binding", "混淆變數作用域")
    attempts = db.list_attempts_for_concept(conn, cid)
    assert len(attempts) == 1
    assert attempts[0]["root_cause"] == "混淆變數作用域"


def test_list_attempts_newest_first_and_limit(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    qid = db.create_question(conn, cid, "exam", "q", {}, "a")
    for i in range(7):
        db.create_attempt(conn, qid, f"ans{i}", "correct", "ok")
    got = db.list_attempts_for_concept(conn, cid, limit=5)
    assert len(got) == 5
    assert got[0]["answer"] == "ans6"


def test_delete_domain_cascades(conn):
    did = db.create_domain(conn, "python", ["write"], False)
    cid = db.create_concept(conn, did, "閉包", "consensus")
    qid = db.create_question(conn, cid, "exam", "q", {}, "a")
    db.create_attempt(conn, qid, "x", "correct", "ok")
    conn.execute("DELETE FROM domains WHERE id = ?", (did,))
    conn.commit()
    assert db.list_concepts(conn, did) == []
    assert db.get_question(conn, qid) is None
```

注意：測試從 `learn_system` 套件匯入，所以 `learn-system/` 目錄內要有一個 `learn_system/` 子目錄放程式碼。建立 `learn-system/learn_system/__init__.py`（空檔）。

- [ ] **Step 3: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learn_system'`

- [ ] **Step 4: 實作 config.py 與 db.py**

`learn-system/config.py`：

```python
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "learn.db"
SECRETS_DIR = Path.home() / "CCProject" / ".secrets"
```

`learn-system/learn_system/db.py`：

```python
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    goals TEXT NOT NULL,
    verify_sources INTEGER NOT NULL DEFAULT 0,
    executable INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'generating',
    chat_session_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS concepts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    section TEXT NOT NULL,
    description TEXT,
    source_urls TEXT,
    status TEXT NOT NULL DEFAULT 'untested',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id INTEGER NOT NULL REFERENCES concepts(id) ON DELETE CASCADE,
    goal_type TEXT NOT NULL,
    prompt TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    reference_answer TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    answer TEXT NOT NULL,
    verdict TEXT NOT NULL,
    feedback TEXT,
    root_cause TEXT,
    created_at TEXT NOT NULL
);
"""


def _now():
    return datetime.now(timezone.utc).isoformat()


def connect(db_path=None):
    if db_path is None:
        from config import DB_PATH
        db_path = DB_PATH
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _row(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def create_domain(conn, name, goals, verify_sources, executable=True):
    cur = conn.execute(
        "INSERT INTO domains (name, goals, verify_sources, executable, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (name, json.dumps(goals), int(bool(verify_sources)), int(bool(executable)), _now()),
    )
    conn.commit()
    return cur.lastrowid


def _domain_out(row):
    if row is None:
        return None
    row["goals"] = json.loads(row["goals"])
    return row


def get_domain(conn, domain_id):
    return _domain_out(_row(conn.execute("SELECT * FROM domains WHERE id = ?", (domain_id,))))


def list_domains(conn):
    return [_domain_out(r) for r in _rows(conn.execute("SELECT * FROM domains ORDER BY id"))]


def set_domain_status(conn, domain_id, status):
    conn.execute("UPDATE domains SET status = ? WHERE id = ?", (status, domain_id))
    conn.commit()


def set_domain_executable(conn, domain_id, executable):
    conn.execute("UPDATE domains SET executable = ? WHERE id = ?", (int(bool(executable)), domain_id))
    conn.commit()


def set_domain_chat_session(conn, domain_id, session_id):
    conn.execute("UPDATE domains SET chat_session_id = ? WHERE id = ?", (session_id, domain_id))
    conn.commit()


def create_concept(conn, domain_id, name, section, description=None, source_urls=None):
    cur = conn.execute(
        "INSERT INTO concepts (domain_id, name, section, description, source_urls, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (domain_id, name, section, description,
         json.dumps(source_urls) if source_urls is not None else None, _now()),
    )
    conn.commit()
    return cur.lastrowid


def _concept_out(row):
    if row is None:
        return None
    if row["source_urls"] is not None:
        row["source_urls"] = json.loads(row["source_urls"])
    return row


def list_concepts(conn, domain_id):
    return [_concept_out(r) for r in _rows(
        conn.execute("SELECT * FROM concepts WHERE domain_id = ? ORDER BY id", (domain_id,)))]


def get_concept(conn, concept_id):
    return _concept_out(_row(conn.execute("SELECT * FROM concepts WHERE id = ?", (concept_id,))))


def set_concept_description(conn, concept_id, description):
    conn.execute("UPDATE concepts SET description = ? WHERE id = ?", (description, concept_id))
    conn.commit()


def set_concept_status(conn, concept_id, status):
    conn.execute("UPDATE concepts SET status = ? WHERE id = ?", (status, concept_id))
    conn.commit()


def create_question(conn, concept_id, goal_type, prompt, payload, reference_answer):
    cur = conn.execute(
        "INSERT INTO questions (concept_id, goal_type, prompt, payload, reference_answer, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (concept_id, goal_type, prompt, json.dumps(payload), reference_answer, _now()),
    )
    conn.commit()
    return cur.lastrowid


def _question_out(row):
    if row is None:
        return None
    row["payload"] = json.loads(row["payload"])
    return row


def get_question(conn, question_id):
    return _question_out(_row(conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,))))


def list_questions(conn, concept_id):
    return [_question_out(r) for r in _rows(
        conn.execute("SELECT * FROM questions WHERE concept_id = ? ORDER BY id", (concept_id,)))]


def create_attempt(conn, question_id, answer, verdict, feedback, root_cause=None):
    cur = conn.execute(
        "INSERT INTO attempts (question_id, answer, verdict, feedback, root_cause, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (question_id, answer, verdict, feedback, root_cause, _now()),
    )
    conn.commit()
    return cur.lastrowid


def list_attempts_for_concept(conn, concept_id, limit=5):
    return _rows(conn.execute(
        "SELECT a.* FROM attempts a JOIN questions q ON q.id = a.question_id"
        " WHERE q.concept_id = ? ORDER BY a.id DESC LIMIT ?", (concept_id, limit)))


def list_attempts_for_domain(conn, domain_id):
    return _rows(conn.execute(
        "SELECT a.*, q.concept_id, c.name AS concept_name FROM attempts a"
        " JOIN questions q ON q.id = a.question_id"
        " JOIN concepts c ON c.id = q.concept_id"
        " WHERE c.domain_id = ? ORDER BY a.id DESC", (domain_id,)))
```

- [ ] **Step 5: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_db.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 專案骨架與 SQLite 資料層"
```

---

### Task 2: 掌握度計算

**Files:**
- Create: `learn-system/learn_system/mastery.py`
- Create: `learn-system/tests/test_mastery.py`

**Interfaces:**
- Consumes: `db.list_attempts_for_concept()` 的輸出格式（`list[dict]`，最新在前，含 `verdict` 欄位）
- Produces: `mastery.compute_status(attempts: list[dict]) -> str`，回傳 `"untested" | "practicing" | "mastered"`

- [ ] **Step 1: 寫失敗的測試**

`learn-system/tests/test_mastery.py`：

```python
from learn_system.mastery import compute_status


def attempts(*verdicts):
    """最新在前"""
    return [{"verdict": v} for v in verdicts]


def test_no_attempts_is_untested():
    assert compute_status([]) == "untested"


def test_all_correct_but_fewer_than_five_is_practicing():
    assert compute_status(attempts(*["correct"] * 4)) == "practicing"


def test_five_correct_is_mastered():
    assert compute_status(attempts(*["correct"] * 5)) == "mastered"


def test_four_of_five_correct_is_mastered():
    assert compute_status(attempts("wrong", "correct", "correct", "correct", "correct")) == "mastered"


def test_three_of_five_correct_is_practicing():
    assert compute_status(attempts("wrong", "wrong", "correct", "correct", "correct")) == "practicing"


def test_partial_does_not_count_as_correct():
    assert compute_status(attempts("partial", "partial", "correct", "correct", "correct")) == "practicing"


def test_four_correct_plus_one_partial_is_mastered():
    assert compute_status(attempts("partial", "correct", "correct", "correct", "correct")) == "mastered"


def test_only_last_five_attempts_matter():
    # 最早那筆錯誤已被擠出窗口
    old_wrong = attempts("correct", "correct", "correct", "correct", "correct", "wrong")
    assert compute_status(old_wrong) == "mastered"


def test_mastered_can_regress():
    assert compute_status(attempts("wrong", "wrong", "correct", "correct", "correct", "correct", "correct")) == "practicing"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_mastery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learn_system.mastery'`

- [ ] **Step 3: 實作 mastery.py**

`learn-system/learn_system/mastery.py`：

```python
WINDOW = 5
REQUIRED_CORRECT = 4


def compute_status(attempts):
    """attempts 為最近作答，最新在前。"""
    if not attempts:
        return "untested"
    recent = attempts[:WINDOW]
    correct = sum(1 for a in recent if a["verdict"] == "correct")
    if len(recent) >= WINDOW and correct >= REQUIRED_CORRECT:
        return "mastered"
    return "practicing"
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_mastery.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 掌握度滑動窗口計算（5 取 4）"
```

---

### Task 3: 學習者程式碼執行器

**Files:**
- Create: `learn-system/learn_system/executor.py`
- Create: `learn-system/tests/test_executor.py`

**Interfaces:**
- Consumes: 無
- Produces: `executor.run_python(code: str, timeout: float = 5.0) -> dict`，回傳
  `{"ok": bool, "stdout": str, "stderr": str, "timed_out": bool}`
  （`ok` 為 True 僅當 process 正常結束且 returncode 為 0）

- [ ] **Step 1: 寫失敗的測試**

`learn-system/tests/test_executor.py`：

```python
from learn_system.executor import run_python


def test_successful_run():
    r = run_python("print('hello')")
    assert r["ok"] is True
    assert r["stdout"].strip() == "hello"
    assert r["timed_out"] is False


def test_runtime_error_is_not_ok():
    r = run_python("raise ValueError('boom')")
    assert r["ok"] is False
    assert "ValueError" in r["stderr"]


def test_syntax_error_is_not_ok():
    r = run_python("def broken(:")
    assert r["ok"] is False
    assert r["stderr"] != ""


def test_infinite_loop_times_out():
    r = run_python("while True: pass", timeout=0.5)
    assert r["timed_out"] is True
    assert r["ok"] is False


def test_reading_stdin_does_not_hang():
    # stdin 關閉，input() 會立刻 EOFError 而非永久卡住
    r = run_python("input()", timeout=0.5)
    assert r["timed_out"] is False
    assert r["ok"] is False


def test_assertion_failure_reported():
    r = run_python("assert 1 == 2, 'nope'")
    assert r["ok"] is False
    assert "nope" in r["stderr"]
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learn_system.executor'`

- [ ] **Step 3: 實作 executor.py**

`learn-system/learn_system/executor.py`：

```python
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TIMEOUT = 5.0


def run_python(code, timeout=DEFAULT_TIMEOUT):
    """在獨立暫存目錄執行學習者的程式碼，逾時即終止。"""
    with tempfile.TemporaryDirectory(prefix="learn-run-") as tmp:
        script = Path(tmp) / "solution.py"
        script.write_text(code, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                cwd=tmp,
            )
        except subprocess.TimeoutExpired as e:
            return {
                "ok": False,
                "stdout": (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
                "stderr": (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
                "timed_out": True,
            }
        return {
            "ok": proc.returncode == 0,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_executor.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 隔離的程式碼執行器（5 秒逾時）"
```

---

### Task 4: Claude 呼叫層與智識地圖生成

**Files:**
- Create: `learn-system/learn_system/tutor.py`
- Create: `learn-system/tests/test_tutor.py`

**Interfaces:**
- Consumes: 無
- Produces:
  - `tutor.TutorError(Exception)`
  - `tutor.generate_map(domain_name: str, goals: list[str], verify_sources: bool) -> dict`，回傳
    `{"executable": bool, "concepts": [{"name": str, "section": str, "description": str}]}`
  - `tutor._call_agent(prompt: str, allow_web: bool = False, session_id: str | None = None) -> tuple[str, str | None]`
    回傳 `(回應文字, session_id)`。**這是唯一與 SDK 接觸的接縫，測試一律 monkeypatch 它。**

- [ ] **Step 1: 寫失敗的測試**

`learn-system/tests/test_tutor.py`：

```python
import json

import pytest

from learn_system import tutor


def test_generate_map_parses_json(monkeypatch):
    payload = {
        "executable": True,
        "concepts": [
            {"name": "可變 vs 不可變", "section": "consensus", "description": "..."},
            {"name": "GIL", "section": "dispute", "description": "..."},
            {"name": "free-threading", "section": "frontier", "description": "..."},
        ],
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    result = tutor.generate_map("python", ["write", "exam"], verify_sources=False)
    assert result["executable"] is True
    assert len(result["concepts"]) == 3
    assert result["concepts"][0]["section"] == "consensus"


def test_generate_map_strips_markdown_fence(monkeypatch):
    payload = {"executable": True, "concepts": [{"name": "x", "section": "consensus", "description": "d"}]}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (fenced, None))
    assert tutor.generate_map("python", ["write"], False)["concepts"][0]["name"] == "x"


def test_generate_map_rejects_non_json(monkeypatch):
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: ("我覺得 Python 很有趣！", None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_map("python", ["write"], False)


def test_generate_map_rejects_empty_concepts(monkeypatch):
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps({"executable": True, "concepts": []}), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_map("python", ["write"], False)


def test_generate_map_rejects_bad_section(monkeypatch):
    payload = {"executable": True, "concepts": [{"name": "x", "section": "亂寫", "description": "d"}]}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_map("python", ["write"], False)


def test_verify_sources_passes_allow_web(monkeypatch):
    seen = {}
    payload = {"executable": True, "concepts": [{"name": "x", "section": "consensus", "description": "d"}]}

    def fake(prompt, allow_web=False, session_id=None):
        seen["allow_web"] = allow_web
        return json.dumps(payload), None

    monkeypatch.setattr(tutor, "_call_agent", fake)
    tutor.generate_map("python", ["write"], verify_sources=True)
    assert seen["allow_web"] is True
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_tutor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'learn_system.tutor'`

- [ ] **Step 3: 實作 tutor.py（本任務僅 generate_map 與 _call_agent）**

`learn-system/learn_system/tutor.py`：

```python
import asyncio
import json
import re

VALID_SECTIONS = {"consensus", "dispute", "frontier"}
AGENT_TIMEOUT = 180.0


class TutorError(Exception):
    """Agent 呼叫或回應解析失敗。"""


# 【執行後修訂 2026-09-26】能力邊界集中在 `_build_options(allow_web, session_id)`，
# 完整實作見 `learn_system/tutor.py`。**不要**退回以下寫法：
#     ClaudeAgentOptions(allowed_tools=..., permission_mode="bypassPermissions")
# 因為 `allowed_tools` 只是「免詢問核准清單」而非限制，`bypassPermissions` 會在
# 諮詢任何 callback 前放行所有工具——兩者相加等於完全沒有邊界（實測：Bash/Write/
# WebSearch 全部可達且免確認，且 `verify_sources=False` 並不會關掉網路工具）。
#
# 邊界必須同時滿足下列每一條，任一單獨用都名存實亡：
#   - `tools` 限定基礎工具集（Read/Grep，allow_web 時加 WebSearch/WebFetch）
#   - `permission_mode="dontAsk"` 使核准清單成為真正的白名單
#   - `disallowed_tools` 硬性封鎖 Bash/Write/Edit/NotebookEdit（allow_web=False 再加網路工具）
#   - `strict_mcp_config=True`——否則本機 MCP 工具會整批注入（實測 84 項，
#     含 `mcp__playwright__browser_run_code_unsafe`，等同 RCE）
#   - `cwd=PROJECT_DIR` **必須明設**；不設時子行程沿用啟動目錄，讀取邊界會隨
#     「從哪裡啟動」飄移（實測：從 repo 根啟動可讀到 `.secrets/telegram_token.txt`）
#   - `extra_args={"restricted": None}` 並以 `--settings` 只帶 apiKeyHelper：
#     `--restricted` 把檔案工具鎖在工作目錄內**且**忽略 user/project/local settings，
#     因為 settings 的 allow 規則與 `allowed_tools` 是**相加**的，會逕行放行專案外讀取
#   - 路徑限縮規則 `Read(//<專案>/**)` 實測**無效**，不要採用
#
# 另註：`setting_sources=[]`（SDK 隔離模式）同樣會停用 apiKeyHelper 而導致
# "Not logged in"，且沒有補救途徑，不可使用。
async def _call_agent_async(prompt, allow_web=False, session_id=None):
    from claude_agent_sdk import query, AssistantMessage, TextBlock

    options = _build_options(allow_web=allow_web, session_id=session_id)
    text = []
    observed_session = session_id
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text.append(block.text)
        sid = getattr(msg, "session_id", None)
        if sid:
            observed_session = sid
    return "".join(text), observed_session


def _call_agent(prompt, allow_web=False, session_id=None):
    """與 SDK 的唯一接縫，測試會 monkeypatch 這個函式。"""
    try:
        return asyncio.run(_call_agent_async(prompt, allow_web=allow_web, session_id=session_id))
    except Exception as e:
        raise TutorError(f"Agent 呼叫失敗：{e}") from e


def _extract_json(text):
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate.strip())
    except json.JSONDecodeError:
        # 嘗試抓出第一個完整物件
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise TutorError("Agent 回應不是合法 JSON")


MAP_PROMPT = """你是一位嚴謹的學科導師，專長是替學習者建立「智識地圖」。

學習領域：{domain}
學習者的目標：{goals}

請用該領域專家的視角，產出三類內容：
1. consensus（共識）：該領域專家普遍共享的 5 個核心思維模型。這是學習者必須先建立的底層框架。
2. dispute（分歧）：專家之間最激烈的 3 個爭議點。這標示出「已確定基礎」與「高價值探索區」的分界。
3. frontier（探索區）：2 個尚未有定論的前沿問題。

另外判斷一件事：這個領域的「動手實作」是否可以由程式碼執行來客觀驗證（例如程式語言可以，經濟學不行）。

只回傳 JSON，不要任何其他文字：
{{
  "executable": true 或 false,
  "concepts": [
    {{"name": "概念名稱（精簡，不超過 12 字）", "section": "consensus|dispute|frontier", "description": "一到兩句說明"}}
  ]
}}"""


def generate_map(domain_name, goals, verify_sources):
    prompt = MAP_PROMPT.format(domain=domain_name, goals="、".join(goals))
    if verify_sources:
        prompt += "\n\n請先查證權威來源（官方文件、經典教材）再作答，並在必要時修正你的敘述。"
    text, _ = _call_agent(prompt, allow_web=verify_sources)
    data = _extract_json(text)

    concepts = data.get("concepts") or []
    if not concepts:
        raise TutorError("Agent 未產出任何概念")
    for c in concepts:
        if c.get("section") not in VALID_SECTIONS:
            raise TutorError(f"未知的 section：{c.get('section')}")
        if not c.get("name"):
            raise TutorError("概念缺少名稱")

    return {"executable": bool(data.get("executable", True)), "concepts": concepts}
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_tutor.py -v`
Expected: 6 passed

- [ ] **Step 5: 驗證 SDK 真的可 import**

Run: `cd ~/CCProject/learn-system && ./venv/bin/python -c "import claude_agent_sdk; print('ok')"`
Expected: `ok`（若失敗，比對 command-center 的 venv 安裝了什麼版本並補上）

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): Claude 呼叫層與智識地圖生成"
```

---

### Task 5: Flask 骨架、領域建立與背景生成

**Files:**
- Create: `learn-system/app.py`
- Create: `learn-system/learn_system/__init__.py`（若尚未存在）
- Create: `learn-system/tests/test_api_domains.py`

**Interfaces:**
- Consumes: `db.*`、`tutor.generate_map`、`mastery.compute_status`
- Produces:
  - Flask app factory：`app.create_app(db_path=None) -> Flask`
  - `GET /api/health` → `{"ok": true}`
  - `GET /api/domains` → `{"domains": [{"id","name","goals","mastery_pct","concept_count","last_studied_at"}]}`
  - `POST /api/domains` body `{"name","goals","verify_sources"}` → `{"id": int}`（立刻回傳，生成在背景）
  - `GET /api/domains/<id>` → `{"domain": {...}, "concepts": [...], "progress": {...}}`
  - `POST /api/domains/<id>/override_executable` body `{"executable": bool}`

- [ ] **Step 1: 寫失敗的測試**

`learn-system/tests/test_api_domains.py`：

```python
import json

import pytest

from learn_system import tutor
from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 背景生成改為同步執行，讓測試可預期
    monkeypatch.setattr("app._spawn_map_generation", lambda app, domain_id: app._generate_map(domain_id))
    app = create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    return app.test_client()


def fake_map(executable=True, n=3):
    concepts = []
    for i in range(n):
        concepts.append({"name": f"c{i}", "section": "consensus", "description": "d"})
    return {"executable": executable, "concepts": concepts}


def test_health(client):
    assert client.get("/api/health").get_json() == {"ok": True}


def test_create_domain_returns_id(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map())
    r = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False})
    assert r.status_code == 200
    assert isinstance(r.get_json()["id"], int)


def test_created_domain_is_ready_with_concepts(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(n=3))
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    body = client.get(f"/api/domains/{did}").get_json()
    assert body["domain"]["status"] == "ready"
    assert body["domain"]["executable"] == 1
    assert len(body["concepts"]) == 3
    assert body["progress"]["mastery_pct"] == 0


def test_generation_failure_marks_failed(client, monkeypatch):
    def boom(*a, **k):
        raise tutor.TutorError("壞掉了")

    monkeypatch.setattr(tutor, "generate_map", boom)
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["status"] == "failed"


def test_empty_concepts_marks_failed(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {"executable": True, "concepts": []})
    did = client.post("/api/domains", json={"name": "x", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    body = client.get(f"/api/domains/{did}").get_json()
    assert body["domain"]["status"] == "failed"
    assert body["concepts"] == []


def test_list_domains_reports_mastery(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map())
    client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False})
    domains = client.get("/api/domains").get_json()["domains"]
    assert len(domains) == 1
    assert domains[0]["mastery_pct"] == 0
    assert domains[0]["concept_count"] == 3


def test_override_executable(client, monkeypatch):
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: fake_map(executable=False))
    did = client.post("/api/domains", json={"name": "econ", "goals": ["principle"], "verify_sources": False}).get_json()["id"]
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 0
    client.post(f"/api/domains/{did}/override_executable", json={"executable": True})
    assert client.get(f"/api/domains/{did}").get_json()["domain"]["executable"] == 1
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_api_domains.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: 實作 app.py**

`learn-system/app.py`：

```python
import threading

from flask import Flask, jsonify, render_template, request

from learn_system import db, mastery, tutor


def _mastery_pct(conn, domain_id):
    concepts = db.list_concepts(conn, domain_id)
    if not concepts:
        return 0
    mastered = sum(1 for c in concepts if c["status"] == "mastered")
    return round(mastered * 100 / len(concepts))


def _generate_map(domain_id):
    conn = db.connect()
    try:
        domain = db.get_domain(conn, domain_id)
        try:
            result = tutor.generate_map(domain["name"], domain["goals"], bool(domain["verify_sources"]))
        except tutor.TutorError:
            db.set_domain_status(conn, domain_id, "failed")
            return
        db.set_domain_executable(conn, domain_id, result["executable"])
        for c in result["concepts"]:
            db.create_concept(conn, domain_id, c["name"], c["section"], c.get("description"))
        db.set_domain_status(conn, domain_id, "ready")
    finally:
        conn.close()


def _spawn_map_generation(app, domain_id):
    threading.Thread(target=_generate_map, args=(domain_id,), daemon=True).start()


def create_app(db_path=None):
    app = Flask(__name__)
    conn = db.connect(db_path)
    db.init_db(conn)
    conn.close()

    @app.get("/api/health")
    def health():
        return jsonify(ok=True)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/domain/<int:domain_id>")
    def domain_page(domain_id):
        return render_template("domain.html", domain_id=domain_id)

    @app.get("/api/domains")
    def list_domains():
        conn = db.connect(db_path)
        try:
            out = []
            for d in db.list_domains(conn):
                out.append({
                    "id": d["id"],
                    "name": d["name"],
                    "goals": d["goals"],
                    "status": d["status"],
                    "mastery_pct": _mastery_pct(conn, d["id"]),
                    "concept_count": len(db.list_concepts(conn, d["id"])),
                    "created_at": d["created_at"],
                })
            return jsonify(domains=out)
        finally:
            conn.close()

    @app.post("/api/domains")
    def create_domain():
        body = request.get_json(force=True)
        name = (body.get("name") or "").strip()
        goals = body.get("goals") or []
        if not name:
            return jsonify(error="領域名稱不可為空"), 400
        if not goals:
            return jsonify(error="至少要勾選一個學習目標"), 400
        conn = db.connect(db_path)
        try:
            domain_id = db.create_domain(conn, name, goals, bool(body.get("verify_sources")))
        finally:
            conn.close()
        _spawn_map_generation(app, domain_id)
        return jsonify(id=domain_id)

    @app.get("/api/domains/<int:domain_id>")
    def get_domain(domain_id):
        conn = db.connect(db_path)
        try:
            domain = db.get_domain(conn, domain_id)
            if not domain:
                return jsonify(error="找不到領域"), 404
            concepts = db.list_concepts(conn, domain_id)
            return jsonify(domain=domain, concepts=concepts, progress={
                "mastery_pct": _mastery_pct(conn, domain_id),
                "counts": {
                    "untested": sum(1 for c in concepts if c["status"] == "untested"),
                    "practicing": sum(1 for c in concepts if c["status"] == "practicing"),
                    "mastered": sum(1 for c in concepts if c["status"] == "mastered"),
                },
            })
        finally:
            conn.close()

    @app.post("/api/domains/<int:domain_id>/override_executable")
    def override_executable(domain_id):
        body = request.get_json(force=True)
        conn = db.connect(db_path)
        try:
            db.set_domain_executable(conn, domain_id, body.get("executable", True))
        finally:
            conn.close()
        return jsonify(ok=True)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5990, debug=False)
```

- [ ] **Step 4: 建立最小模板讓頁面路由不 500**

`learn-system/templates/index.html`：先放 `<h1>學習系統</h1>`
`learn-system/templates/domain.html`：先放 `<h1>領域 {{ domain_id }}</h1>`
（Task 9、10 會替換成完整版）

- [ ] **Step 5: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_api_domains.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): Flask 骨架、領域建立與背景生成智識地圖"
```

---

### Task 6: 出題（含題目有效性驗證）

**Files:**
- Modify: `learn-system/learn_system/tutor.py`（新增 `generate_question`）
- Modify: `learn-system/app.py`（新增出題端點）
- Modify: `learn-system/tests/test_tutor.py`
- Create: `learn-system/tests/test_api_questions.py`

**Interfaces:**
- Consumes: `tutor._call_agent`、`tutor._extract_json`、`executor.run_python`、`db.*`
- Produces:
  - `tutor.generate_question(domain_name, concept_name, concept_description, goal_type, executable) -> dict`，回傳
    `{"prompt": str, "payload": dict, "reference_answer": str}`
  - `POST /api/concepts/<id>/questions` body `{"goal_type": str}` → `{"question": {id, goal_type, prompt, payload, reference_answer}}`

- [ ] **Step 1: 寫失敗的測試（tutor 層）**

追加到 `learn-system/tests/test_tutor.py`：

```python
def test_generate_write_question(monkeypatch):
    payload = {
        "prompt": "寫一個計數器",
        "payload": {"starter_code": "", "test_code": "from solution import counter\nassert counter()() == 1"},
        "reference_answer": "def counter(): ...",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "write", executable=True)
    assert q["payload"]["test_code"].startswith("from solution")


def test_generate_read_question_requires_fixed_code(monkeypatch):
    payload = {"prompt": "找 bug", "payload": {"code_snippet": "x=1"}, "reference_answer": "少了 fixed_code"}
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    with pytest.raises(tutor.TutorError):
        tutor.generate_question("python", "閉包", "說明", "read", executable=True)


def test_generate_read_question_ok_with_fixed_code(monkeypatch):
    payload = {
        "prompt": "找 bug",
        "payload": {"code_snippet": "print(1)", "fixed_code": "print(2)", "bug_description": "值錯了"},
        "reference_answer": "值錯了",
    }
    monkeypatch.setattr(tutor, "_call_agent", lambda *a, **k: (json.dumps(payload), None))
    q = tutor.generate_question("python", "閉包", "說明", "read", executable=True)
    assert q["payload"]["fixed_code"] == "print(2)"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_tutor.py -v`
Expected: FAIL — `AttributeError: module 'learn_system.tutor' has no attribute 'generate_question'`

- [ ] **Step 3: 實作 tutor.generate_question**

追加到 `learn-system/learn_system/tutor.py`：

```python
QUESTION_PROMPT = """你是嚴謹的學科導師，要替學習者出一題來檢驗他是否「真正理解」而非死記。

學習領域：{domain}
正在練的概念：{concept}
概念說明：{description}
題型：{goal_type}

題型定義：
- write：給規格讓學習者寫出程式。payload 需含 {{"starter_code": "...", "test_code": "..."}}，
  test_code 是會以 `from solution import ...` 匯入學習者程式碼的 pytest 斷言。
- read：給一段有 bug 的程式讓學習者找出問題並說明。payload 需含
  {{"code_snippet": "有 bug 的版本", "fixed_code": "修正後版本", "bug_description": "標準答案"}}。
  code_snippet 必須真的會出錯或輸出錯誤結果，fixed_code 必須真的能修正它。
- principle：給一段程式讓學習者預測輸出並解釋原因。payload 需含 {{"code_snippet": "..."}}。
- exam：模擬檢定考題。payload 為 {{}}。

只回傳 JSON，不要任何其他文字：
{{"prompt": "題目敘述", "payload": {{...}}, "reference_answer": "標準答案或參考解法"}}"""


def generate_question(domain_name, concept_name, concept_description, goal_type, executable):
    prompt = QUESTION_PROMPT.format(
        domain=domain_name, concept=concept_name,
        description=concept_description or "（尚未生成）", goal_type=goal_type,
    )
    text, _ = _call_agent(prompt)
    data = _extract_json(text)

    payload = data.get("payload") or {}
    if not data.get("prompt"):
        raise TutorError("題目缺少敘述")
    if goal_type == "write" and executable and not payload.get("test_code"):
        raise TutorError("write 題缺少 test_code")
    if goal_type == "read" and executable:
        if not payload.get("code_snippet") or not payload.get("fixed_code"):
            raise TutorError("read 題缺少 code_snippet 或 fixed_code")
    if goal_type == "principle" and executable and not payload.get("code_snippet"):
        raise TutorError("principle 題缺少 code_snippet")

    return {"prompt": data["prompt"], "payload": payload,
            "reference_answer": data.get("reference_answer", "")}
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_tutor.py -v`
Expected: 9 passed

- [ ] **Step 5: 寫失敗的測試（API 層與題目有效性驗證）**

`learn-system/tests/test_api_questions.py`：

```python
import pytest

from learn_system import db, tutor
from app import create_app


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("app._spawn_map_generation", lambda app, domain_id: app._generate_map(domain_id))
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "閉包", "section": "consensus", "description": "d"}],
    })
    app = create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def test_create_exam_question(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "什麼是閉包", "payload": {}, "reference_answer": "函式記住其定義環境"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "exam"})
    assert r.status_code == 200
    assert r.get_json()["question"]["prompt"] == "什麼是閉包"


def test_write_question_validates_by_running_tests(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "寫 counter",
        "payload": {"starter_code": "", "test_code": "assert 1 == 2, 'fail'"},
        "reference_answer": "x"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "write"})
    assert r.status_code == 422
    assert "題目無效" in r.get_json()["error"]


def test_read_question_rejects_fake_bug(ctx, monkeypatch):
    client, did, cid = ctx
    # 原版與修正版行為一致 → 假的 bug
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "找 bug",
        "payload": {"code_snippet": "print('x')", "fixed_code": "print('x')", "bug_description": "假的"},
        "reference_answer": "假的"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "read"})
    assert r.status_code == 422


def test_read_question_accepts_real_bug(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "找 bug",
        "payload": {"code_snippet": "raise ValueError('bug')", "fixed_code": "print('ok')",
                    "bug_description": "不該拋錯"},
        "reference_answer": "不該拋錯"})
    r = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "read"})
    assert r.status_code == 200


def test_question_is_persisted(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": {}, "reference_answer": "a"})
    qid = client.post(f"/api/concepts/{cid}/questions", json={"goal_type": "exam"}).get_json()["question"]["id"]
    conn = db.connect(client.application.config["DB_PATH"])
    assert db.get_question(conn, qid)["prompt"] == "q"
    conn.close()
```

**注意**：`create_app` 需把使用的 db_path 存進 `app.config["DB_PATH"]` 供測試讀取。

- [ ] **Step 6: 實作出題端點**

在 `app.py` 的 `create_app` 內新增（並在開頭 `app.config["DB_PATH"] = db_path`）：

```python
    def _write_question_ok(payload):
        """學習者程式碼為空時，測試必然失敗；改以參考解驗證題目本身可解。"""
        code = payload.get("starter_code") or payload.get("reference_answer") or ""
        return code

    @app.post("/api/concepts/<int:concept_id>/questions")
    def create_question(concept_id):
        body = request.get_json(force=True)
        goal_type = body.get("goal_type")
        if goal_type not in ("write", "read", "principle", "exam"):
            return jsonify(error="未知的題型"), 400

        conn = db.connect(db_path)
        try:
            concept = db.get_concept(conn, concept_id)
            if not concept:
                return jsonify(error="找不到概念"), 404
            domain = db.get_domain(conn, concept["domain_id"])
            executable = bool(domain["executable"])

            try:
                q = tutor.generate_question(domain["name"], concept["name"],
                                            concept["description"], goal_type, executable)
            except tutor.TutorError as e:
                return jsonify(error=str(e)), 502

            if executable and not _validate_question(q, goal_type):
                return jsonify(error="題目無效：執行驗證未通過，請重試"), 422

            qid = db.create_question(conn, concept_id, goal_type, q["prompt"], q["payload"], q["reference_answer"])
            saved = db.get_question(conn, qid)
            return jsonify(question=saved)
        finally:
            conn.close()
```

並在 `app.py` 模組層新增驗證函式：

```python
from learn_system.executor import run_python


def _validate_question(q, goal_type):
    """確認題目本身有效：write 題的參考解要能過測試，
    read 題的原版要真的失敗且修正版真的通過。"""
    payload = q["payload"]
    if goal_type == "write":
        code = q.get("reference_answer") or payload.get("starter_code") or ""
        if not code.strip():
            return False
        combined = code + "\n\n" + payload.get("test_code", "")
        return run_python(combined)["ok"]

    if goal_type == "read":
        original = run_python(payload["code_snippet"])
        fixed = run_python(payload["fixed_code"])
        return (not original["ok"]) and fixed["ok"]

    if goal_type == "principle":
        return bool(payload.get("code_snippet", "").strip())

    return True
```

**重要**：`read` 題的驗證邏輯是「原版失敗、修正版成功」。但若 bug 是「輸出錯誤值」而非拋錯，原版仍會 `ok=True`。此情況下驗證會誤判為無效題。實作時以「原版 stderr 非空」或「修正版與原版輸出不同」為補充條件：

```python
    if goal_type == "read":
        original = run_python(payload["code_snippet"])
        fixed = run_python(payload["fixed_code"])
        differs = (original["stdout"] != fixed["stdout"]) or (original["ok"] != fixed["ok"])
        return differs and fixed["ok"]
```

- [ ] **Step 7: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_api_questions.py -v`
Expected: 5 passed

- [ ] **Step 8: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 出題端點與題目有效性執行驗證"
```

---

### Task 7: 作答批改與掌握度更新

**Files:**
- Modify: `learn-system/learn_system/tutor.py`（新增 `grade_answer`）
- Modify: `learn-system/app.py`（新增作答端點）
- Create: `learn-system/tests/test_api_answers.py`

**Interfaces:**
- Consumes: `executor.run_python`、`mastery.compute_status`、`db.*`
- Produces:
  - `tutor.grade_answer(domain_name, concept_name, question, answer, executable) -> dict`，回傳
    `{"verdict": "correct"|"partial"|"wrong", "feedback": str, "root_cause": str | None}`
  - `POST /api/questions/<id>/answer` body `{"answer": str}` →
    `{"attempt": {...}, "concept_status": str}`

- [ ] **Step 1: 寫失敗的測試**

`learn-system/tests/test_api_answers.py`：

```python
import pytest

from learn_system import db, tutor
from app import create_app


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("app._spawn_map_generation", lambda app, domain_id: app._generate_map(domain_id))
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "閉包", "section": "consensus", "description": "d"}],
    })
    app = create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def make_question(client, cid, monkeypatch, goal_type, payload, reference):
    monkeypatch.setattr(tutor, "generate_question", lambda *a, **k: {
        "prompt": "q", "payload": payload, "reference_answer": reference})
    return client.post(f"/api/concepts/{cid}/questions", json={"goal_type": goal_type}).get_json()["question"]["id"]


def test_write_answer_graded_by_real_execution(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "assert add(1, 2) == 3"},
                        "def add(a, b): return a + b")
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "def add(a, b): return a + b"})
    assert r.get_json()["attempt"]["verdict"] == "correct"


def test_write_answer_wrong_when_test_fails(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "write",
                        {"starter_code": "", "test_code": "assert add(1, 2) == 3"},
                        "def add(a, b): return a + b")
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "def add(a, b): return a - b"})
    assert r.get_json()["attempt"]["verdict"] == "wrong"


def test_exam_answer_graded_by_claude(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "閉包會捕捉定義環境的變數")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "partial", "feedback": "少講了 late binding", "root_cause": "未掌握 late binding"})
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "就是函式包變數"})
    body = r.get_json()
    assert body["attempt"]["verdict"] == "partial"
    assert body["attempt"]["root_cause"] == "未掌握 late binding"


def test_mastery_updates_after_answers(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "correct", "feedback": "好", "root_cause": None})
    for _ in range(5):
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.get_json()["concept_status"] == "mastered"


def test_four_of_five_is_mastered(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    verdicts = iter(["correct", "correct", "correct", "correct", "wrong"])
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": next(verdicts), "feedback": "f", "root_cause": None})
    for _ in range(5):
        r = client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    assert r.get_json()["concept_status"] == "mastered"


def test_attempt_is_persisted(ctx, monkeypatch):
    client, did, cid = ctx
    qid = make_question(client, cid, monkeypatch, "exam", {}, "a")
    monkeypatch.setattr(tutor, "grade_answer", lambda *a, **k: {
        "verdict": "wrong", "feedback": "f", "root_cause": "rc"})
    client.post(f"/api/questions/{qid}/answer", json={"answer": "x"})
    conn = db.connect(client.application.config["DB_PATH"])
    attempts = db.list_attempts_for_concept(conn, cid)
    conn.close()
    assert len(attempts) == 1
    assert attempts[0]["verdict"] == "wrong"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_api_answers.py -v`
Expected: FAIL — 404（端點不存在）

- [ ] **Step 3: 實作 tutor.grade_answer**

追加到 `learn-system/learn_system/tutor.py`：

```python
GRADE_PROMPT = """你是嚴謹的學科導師，要批改學習者的作答。

學習領域：{domain}
概念：{concept}
題目：{prompt}
標準答案：{reference}
學習者的作答：
{answer}

請判斷這份作答是「真正理解」還是「死記硬背／似懂非懂」，並回傳 JSON：
{{"verdict": "correct|partial|wrong",
  "feedback": "針對這份作答的具體回饋",
  "root_cause": "若未完全正確，指出根本的錯誤認知；全對則為 null"}}

判斷原則：能推導、能解釋為什麼，才算 correct；只覆述結論或答對但理由錯誤算 partial。"""


def grade_answer(domain_name, concept_name, question, answer, executable):
    prompt = GRADE_PROMPT.format(
        domain=domain_name, concept=concept_name, prompt=question["prompt"],
        reference=question.get("reference_answer") or "（無）", answer=answer,
    )
    text, _ = _call_agent(prompt)
    data = _extract_json(text)
    verdict = data.get("verdict")
    if verdict not in ("correct", "partial", "wrong"):
        raise TutorError(f"未知的批改結果：{verdict}")
    return {"verdict": verdict, "feedback": data.get("feedback", ""),
            "root_cause": data.get("root_cause")}
```

- [ ] **Step 4: 實作作答端點**

在 `app.py` 的 `create_app` 內新增：

```python
    @app.post("/api/questions/<int:question_id>/answer")
    def answer_question(question_id):
        body = request.get_json(force=True)
        answer = body.get("answer", "")
        conn = db.connect(db_path)
        try:
            question = db.get_question(conn, question_id)
            if not question:
                return jsonify(error="找不到題目"), 404
            concept = db.get_concept(conn, question["concept_id"])
            domain = db.get_domain(conn, concept["domain_id"])
            executable = bool(domain["executable"])
            goal_type = question["goal_type"]

            if goal_type == "write" and executable:
                result = run_python(answer + "\n\n" + question["payload"].get("test_code", ""))
                if result["timed_out"]:
                    return jsonify(error="執行逾時（超過 5 秒），不計入對錯，請修改後重試"), 408
                verdict = "correct" if result["ok"] else "wrong"
                feedback = "測試通過" if result["ok"] else f"測試失敗：\n{result['stderr'][-800:]}"
                root_cause = None if result["ok"] else "程式未通過測試"
            else:
                try:
                    graded = tutor.grade_answer(domain["name"], concept["name"], question, answer, executable)
                except tutor.TutorError as e:
                    return jsonify(error=str(e)), 502
                verdict, feedback, root_cause = graded["verdict"], graded["feedback"], graded["root_cause"]

            db.create_attempt(conn, question_id, answer, verdict, feedback, root_cause)
            status = mastery.compute_status(db.list_attempts_for_concept(conn, concept["id"]))
            db.set_concept_status(conn, concept["id"], status)
            attempts = db.list_attempts_for_concept(conn, concept["id"], limit=1)
            return jsonify(attempt=attempts[0], concept_status=status)
        finally:
            conn.close()
```

- [ ] **Step 5: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_api_answers.py -v`
Expected: 6 passed

- [ ] **Step 6: 執行全部測試**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest -v`
Expected: 全數通過

- [ ] **Step 7: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 作答批改（write 題實跑測試）與掌握度更新"
```

---

### Task 8: 概念說明與對話抽屜

**Files:**
- Modify: `learn-system/learn_system/tutor.py`（新增 `explain_concept`、`chat`）
- Modify: `learn-system/app.py`（新增兩個端點）
- Create: `learn-system/tests/test_api_explain_chat.py`

**Interfaces:**
- Consumes: `tutor._call_agent`、`db.*`
- Produces:
  - `tutor.explain_concept(domain_name, concept_name, section, verify_sources) -> str`
  - `tutor.chat(domain_name, concept_name, message, session_id) -> tuple[str, str | None]`（回傳 `(回應, 新 session_id)`）
  - `POST /api/concepts/<id>/explain` → `{"description": str}`（已有則直接回傳快取）
  - `POST /api/domains/<id>/chat` body `{"message": str, "concept_id": int | null}` → `{"reply": str}`

- [ ] **Step 1: 寫失敗的測試**

`learn-system/tests/test_api_explain_chat.py`：

```python
import pytest

from learn_system import tutor
from app import create_app


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr("app._spawn_map_generation", lambda app, domain_id: app._generate_map(domain_id))
    monkeypatch.setattr(tutor, "generate_map", lambda *a, **k: {
        "executable": True,
        "concepts": [{"name": "閉包", "section": "consensus", "description": None}],
    })
    app = create_app(db_path=tmp_path / "t.db")
    app.config["TESTING"] = True
    client = app.test_client()
    did = client.post("/api/domains", json={"name": "python", "goals": ["write"], "verify_sources": False}).get_json()["id"]
    cid = client.get(f"/api/domains/{did}").get_json()["concepts"][0]["id"]
    return client, did, cid


def test_explain_generates_and_caches(ctx, monkeypatch):
    client, did, cid = ctx
    calls = []
    monkeypatch.setattr(tutor, "explain_concept", lambda *a, **k: calls.append(1) or "閉包是…")
    assert client.post(f"/api/concepts/{cid}/explain").get_json()["description"] == "閉包是…"
    assert client.post(f"/api/concepts/{cid}/explain").get_json()["description"] == "閉包是…"
    assert len(calls) == 1  # 第二次走快取


def test_explain_failure_returns_502(ctx, monkeypatch):
    client, did, cid = ctx

    def boom(*a, **k):
        raise tutor.TutorError("壞了")

    monkeypatch.setattr(tutor, "explain_concept", boom)
    assert client.post(f"/api/concepts/{cid}/explain").status_code == 502


def test_chat_stores_session(ctx, monkeypatch):
    client, did, cid = ctx
    monkeypatch.setattr(tutor, "chat", lambda *a, **k: ("嗨", "sess-1"))
    assert client.post(f"/api/domains/{did}/chat", json={"message": "hi", "concept_id": cid}).get_json()["reply"] == "嗨"
    seen = {}
    monkeypatch.setattr(tutor, "chat", lambda d, c, m, sid: seen.update(sid=sid) or ("again", "sess-1"))
    client.post(f"/api/domains/{did}/chat", json={"message": "again", "concept_id": cid})
    assert seen["sid"] == "sess-1"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_api_explain_chat.py -v`
Expected: FAIL — 404

- [ ] **Step 3: 實作 tutor.explain_concept 與 tutor.chat**

追加到 `learn-system/learn_system/tutor.py`：

```python
EXPLAIN_PROMPT = """你是嚴謹的學科導師。學習者正在學「{domain}」，現在點開了概念「{concept}」（分類：{section}）。

請產出一段說明，包含：
1. 這個概念在解決什麼問題（為什麼存在）
2. 一個具體、可檢驗的例子
3. 常見的誤解

直接寫說明文字，不要客套開場。控制在 300 字內。"""


def explain_concept(domain_name, concept_name, section, verify_sources):
    prompt = EXPLAIN_PROMPT.format(domain=domain_name, concept=concept_name, section=section)
    if verify_sources:
        prompt += "\n\n請先查證權威來源再作答，若有引用請附上來源網址。"
    text, _ = _call_agent(prompt, allow_web=verify_sources)
    if not text.strip():
        raise TutorError("說明生成失敗（空回應）")
    return text.strip()


CHAT_SYSTEM = """你是學習者「{domain}」領域的私人導師，目前正在練概念「{concept}」。
用對話幫他釐清疑惑、追問錯因。不要直接給答案，用提問引導他自己想通。
"""


def chat(domain_name, concept_name, message, session_id):
    prompt = CHAT_SYSTEM.format(domain=domain_name, concept=concept_name or "（未指定）") + "\n\n" + message
    text, new_session = _call_agent(prompt, session_id=session_id)
    return text.strip(), new_session
```

**【執行後修訂 2026-09-26】** session resume 已提前在 Task 4 完成——`_call_agent` 現已把 `session_id` 傳入 `_build_options`，後者設定 `resume=session_id`（`None` 時不帶 `--resume`）。**本任務不需要再改 `_call_agent_async` 或 `_call_agent`**，直接呼叫即可：

```python
def chat(domain_name, concept_name, message, session_id):
    prompt = CHAT_SYSTEM.format(domain=domain_name, concept=concept_name or "（未指定）") + "\n\n" + message
    text, new_session = _call_agent(prompt, session_id=session_id)
    return text.strip(), new_session
```

**切勿**另行建構 `ClaudeAgentOptions`——能力邊界集中於 `_build_options`，繞過它會失去唯讀限制（詳見 Task 4 Step 3 的執行後修訂說明）。
```

（`ClaudeAgentOptions` 的 resume 欄位名稱請以實際安裝版本的 API 為準；若不同，以 `./venv/bin/python -c "from claude_agent_sdk import ClaudeAgentOptions; print(ClaudeAgentOptions.__doc__)"` 確認後調整。）

- [ ] **Step 4: 實作兩個端點**

在 `app.py` 的 `create_app` 內新增：

```python
    @app.post("/api/concepts/<int:concept_id>/explain")
    def explain(concept_id):
        conn = db.connect(db_path)
        try:
            concept = db.get_concept(conn, concept_id)
            if not concept:
                return jsonify(error="找不到概念"), 404
            if concept["description"]:
                return jsonify(description=concept["description"])
            domain = db.get_domain(conn, concept["domain_id"])
            try:
                text = tutor.explain_concept(domain["name"], concept["name"],
                                             concept["section"], bool(domain["verify_sources"]))
            except tutor.TutorError as e:
                return jsonify(error=str(e)), 502
            db.set_concept_description(conn, concept_id, text)
            return jsonify(description=text)
        finally:
            conn.close()

    @app.post("/api/domains/<int:domain_id>/chat")
    def chat(domain_id):
        body = request.get_json(force=True)
        message = (body.get("message") or "").strip()
        if not message:
            return jsonify(error="訊息不可為空"), 400
        conn = db.connect(db_path)
        try:
            domain = db.get_domain(conn, domain_id)
            if not domain:
                return jsonify(error="找不到領域"), 404
            concept_name = None
            if body.get("concept_id"):
                concept = db.get_concept(conn, body["concept_id"])
                concept_name = concept["name"] if concept else None
            try:
                reply, new_session = tutor.chat(domain["name"], concept_name, message, domain["chat_session_id"])
            except tutor.TutorError as e:
                return jsonify(error=str(e)), 502
            if new_session and new_session != domain["chat_session_id"]:
                db.set_domain_chat_session(conn, domain_id, new_session)
            return jsonify(reply=reply)
        finally:
            conn.close()
```

- [ ] **Step 5: 執行全部測試**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest -v`
Expected: 全數通過

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 概念說明（快取）與對話抽屜端點"
```

---

### Task 9: 前端 — 首頁與領域清單

**Files:**
- Create: `learn-system/static/style.css`
- Create: `learn-system/static/index.js`
- Modify: `learn-system/templates/index.html`
- Create: `learn-system/tests/test_frontend_escape.py`

**Interfaces:**
- Consumes: `GET /api/domains`、`POST /api/domains`
- Produces: 可瀏覽的首頁；`static/escape.js` 提供 `escapeHtml(str)` 供 Task 10 共用

- [ ] **Step 1: 寫失敗的測試（轉義函式的行為固定下來）**

`learn-system/tests/test_frontend_escape.py`：

```python
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_escape_helper_exists():
    src = (STATIC / "escape.js").read_text(encoding="utf-8")
    assert "escapeHtml" in src


def test_index_js_does_not_use_innerhtml_with_data():
    """首頁 JS 不得用 innerHTML 直接插入伺服器資料。"""
    src = (STATIC / "index.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src


def test_templates_have_utf8_charset():
    for name in ("index.html", "domain.html"):
        html = (Path(__file__).resolve().parent.parent / "templates" / name).read_text(encoding="utf-8")
        assert re.search(r'<meta\s+charset="utf-8"', html, re.I), f"{name} 缺少 meta charset"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_frontend_escape.py -v`
Expected: FAIL — 找不到 `static/escape.js`

- [ ] **Step 3: 實作前端檔案**

`learn-system/static/escape.js`：

```javascript
// 所有插入 DOM 的伺服器資料一律走這裡，避免領域／概念名稱含 HTML 時破壞頁面
function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = String(s ?? '');
  return div.innerHTML;
}
```

`learn-system/static/style.css`（要點：兩欄 grid、狀態燈、抽屜；深色調、無框架）：

```css
:root {
  --bg: #0f1115; --panel: #171a21; --line: #262b36;
  --fg: #e6e9ef; --dim: #8b93a7; --accent: #6ea8fe;
  --ok: #3fb950; --warn: #d29922; --bad: #f85149;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font-family: -apple-system, "PingFang TC", sans-serif; }
header { padding: 16px 24px; border-bottom: 1px solid var(--line); display: flex;
  justify-content: space-between; align-items: center; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 16px; padding: 24px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; cursor: pointer; }
.card:hover { border-color: var(--accent); }
.bar { height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; margin-top: 10px; }
.bar > i { display: block; height: 100%; background: var(--accent); }
button { background: var(--accent); color: #06203f; border: 0; border-radius: 8px;
  padding: 8px 14px; font-weight: 600; cursor: pointer; }
dialog { background: var(--panel); color: var(--fg); border: 1px solid var(--line);
  border-radius: 12px; padding: 20px; width: min(420px, 90vw); }
label { display: block; margin: 10px 0 4px; color: var(--dim); font-size: 13px; }
input[type=text], textarea, select { width: 100%; background: #0c0e12; color: var(--fg);
  border: 1px solid var(--line); border-radius: 8px; padding: 8px; }
```

`learn-system/static/index.js`：

```javascript
async function loadDomains() {
  const res = await fetch('/api/domains');
  const { domains } = await res.json();
  const wrap = document.getElementById('cards');
  wrap.replaceChildren();
  if (!domains.length) {
    const p = document.createElement('p');
    p.textContent = '還沒有任何領域，點右上角新增。';
    wrap.append(p);
    return;
  }
  for (const d of domains) {
    const card = document.createElement('div');
    card.className = 'card';
    card.onclick = () => location.href = `/domain/${d.id}`;

    const h = document.createElement('h3');
    h.textContent = d.name;
    card.append(h);

    const meta = document.createElement('div');
    meta.className = 'meta';
    meta.textContent = `${d.concept_count} 個概念 · 掌握 ${d.mastery_pct}%`;
    card.append(meta);

    if (d.status !== 'ready') {
      const s = document.createElement('div');
      s.className = 'meta';
      s.textContent = d.status === 'generating' ? '生成中…' : '生成失敗';
      card.append(s);
    }

    const bar = document.createElement('div');
    bar.className = 'bar';
    const fill = document.createElement('i');
    fill.style.width = `${d.mastery_pct}%`;
    bar.append(fill);
    card.append(bar);
    wrap.append(card);
  }
}

document.getElementById('new-domain').onclick = () => {
  document.getElementById('new-dialog').showModal();
};

document.getElementById('create-form').onsubmit = async (e) => {
  e.preventDefault();
  const goals = [...document.querySelectorAll('input[name=goal]:checked')].map(i => i.value);
  if (!goals.length) { alert('至少要勾選一個目標'); return; }
  const res = await fetch('/api/domains', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: document.getElementById('name').value,
      goals,
      verify_sources: document.getElementById('verify').checked,
    }),
  });
  const body = await res.json();
  if (!res.ok) { alert(body.error); return; }
  location.href = `/domain/${body.id}`;
};

loadDomains();
setInterval(loadDomains, 5000);
```

`learn-system/templates/index.html`：

```html
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>學習系統</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header>
  <h1>學習系統</h1>
  <button id="new-domain">＋ 新領域</button>
</header>
<main class="cards" id="cards"></main>

<dialog id="new-dialog">
  <form id="create-form" method="dialog">
    <label>領域名稱</label>
    <input type="text" id="name" placeholder="python" required>

    <label>學習目標（可多選）</label>
    <label><input type="checkbox" name="goal" value="write"> 能自己寫出來</label>
    <label><input type="checkbox" name="goal" value="read"> 能看懂並改動別人的程式</label>
    <label><input type="checkbox" name="goal" value="principle"> 懂底層原理</label>
    <label><input type="checkbox" name="goal" value="exam"> 能通過檢定</label>

    <label><input type="checkbox" id="verify"> 需要查證外部來源</label>

    <div style="margin-top:16px"><button type="submit">建立</button></div>
  </form>
</dialog>

<script src="/static/escape.js"></script>
<script src="/static/index.js"></script>
</body>
</html>
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest tests/test_frontend_escape.py -v`
Expected: 3 passed

- [ ] **Step 5: 實測頁面**

啟動服務並用瀏覽器確認：

```bash
cd ~/CCProject/learn-system && ./venv/bin/python app.py
```

瀏覽 `http://127.0.0.1:5990/`，確認：清單可載入、新增對話框可開、勾選與建立能導向領域頁。確認後 Ctrl-C 關閉。

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 首頁領域清單與新增領域對話框"
```

---

### Task 10: 前端 — 領域頁（地圖、工作區、抽屜、進度）

**Files:**
- Create: `learn-system/static/domain.js`
- Modify: `learn-system/templates/domain.html`
- Modify: `learn-system/static/style.css`

**Interfaces:**
- Consumes: 所有先前端點
- Produces: 可完整操作一輪學習迴圈的頁面

- [ ] **Step 1: 建立領域頁模板與樣式**

`learn-system/templates/domain.html`：

```html
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>學習系統</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body data-domain-id="{{ domain_id }}">
<header>
  <div>
    <a href="/" class="dim">← 全部領域</a>
    <h1 id="domain-name"></h1>
  </div>
  <div class="meta" id="domain-meta"></div>
</header>

<main class="workspace">
  <section class="map">
    <div id="map-sections"></div>
    <div id="map-empty" hidden>
      <p>智識地圖還沒生成好，或生成失敗了。</p>
      <button id="retry-hint">重新載入</button>
    </div>
  </section>

  <section class="work">
    <div id="work-empty" class="dim">從左邊選一個概念開始。</div>
    <div id="work-body" hidden>
      <h2 id="concept-name"></h2>
      <div id="concept-desc" class="dim"></div>
      <div class="row">
        <select id="goal-type"></select>
        <button id="ask-question">出題</button>
      </div>
      <div id="question-area" hidden>
        <div id="question-prompt"></div>
        <pre id="question-code" hidden></pre>
        <textarea id="answer" rows="10" placeholder="在這裡作答（程式碼題直接寫程式）"></textarea>
        <div class="row">
          <button id="submit-answer">送出</button>
          <span id="answer-status" class="dim"></span>
        </div>
        <div id="verdict" hidden></div>
      </div>
    </div>
  </section>
</main>

<section class="progress">
  <h3>進度</h3>
  <div id="progress-body" class="dim"></div>
</section>

<button id="drawer-toggle">導師</button>
<aside id="drawer" hidden>
  <header><span>導師對話</span><button id="drawer-close">✕</button></header>
  <div id="chat-log"></div>
  <form id="chat-form">
    <input type="text" id="chat-input" placeholder="問問題…">
    <button type="submit">送出</button>
  </form>
</aside>

<script src="/static/escape.js"></script>
<script src="/static/domain.js"></script>
</body>
</html>
```

追加到 `learn-system/static/style.css`：

```css
.workspace { display: grid; grid-template-columns: 300px 1fr; gap: 0; min-height: 60vh; }
.map { border-right: 1px solid var(--line); padding: 16px; }
.work { padding: 20px; }
.sec-title { color: var(--dim); font-size: 12px; text-transform: uppercase;
  margin: 14px 0 6px; letter-spacing: .08em; }
.concept { padding: 6px 8px; border-radius: 6px; cursor: pointer; display: flex; gap: 8px; }
.concept:hover { background: var(--panel); }
.concept.active { background: var(--panel); border-left: 2px solid var(--accent); }
.dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px;
  background: var(--dim); flex: none; }
.dot.practicing { background: var(--warn); }
.dot.mastered { background: var(--ok); }
.row { display: flex; gap: 8px; align-items: center; margin: 12px 0; }
.progress { border-top: 1px solid var(--line); padding: 16px 24px; }
#verdict { border-radius: 8px; padding: 12px; margin-top: 12px; white-space: pre-wrap; }
#verdict.correct { background: #10261a; border: 1px solid var(--ok); }
#verdict.partial { background: #2a2110; border: 1px solid var(--warn); }
#verdict.wrong { background: #2a1214; border: 1px solid var(--bad); }
#drawer { position: fixed; top: 0; right: 0; width: min(420px, 92vw); height: 100vh;
  background: var(--panel); border-left: 1px solid var(--line);
  display: flex; flex-direction: column; }
#drawer header { display: flex; justify-content: space-between; align-items: center; }
#chat-log { flex: 1; overflow-y: auto; padding: 12px; }
.msg { margin: 8px 0; white-space: pre-wrap; }
.msg.me { color: var(--accent); }
#chat-form { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--line); }
#drawer-toggle { position: fixed; right: 16px; bottom: 16px; z-index: 10; }
pre { background: #0c0e12; border: 1px solid var(--line); border-radius: 8px;
  padding: 12px; overflow-x: auto; }
.dim { color: var(--dim); }
```

- [ ] **Step 2: 實作 domain.js**

`learn-system/static/domain.js`：

```javascript
const DOMAIN_ID = Number(document.body.dataset.domainId);
const GOAL_LABELS = { write: '能自己寫出來', read: '能讀懂並改錯', principle: '懂底層原理', exam: '檢定題型' };

let state = { domain: null, concepts: [], current: null, question: null };

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

async function refresh() {
  const data = await api(`/api/domains/${DOMAIN_ID}`);
  state.domain = data.domain;
  state.concepts = data.concepts;

  document.getElementById('domain-name').textContent = data.domain.name;
  document.getElementById('domain-meta').textContent =
    `目標 ${data.domain.goals.map(g => GOAL_LABELS[g] || g).join('／')} · 掌握 ${data.progress.mastery_pct}%`;

  renderMap();
  renderProgress(data.progress);
}

function renderMap() {
  const wrap = document.getElementById('map-sections');
  wrap.replaceChildren();
  const empty = document.getElementById('map-empty');
  if (!state.concepts.length) { empty.hidden = false; return; }
  empty.hidden = true;

  const titles = { consensus: '共識（核心思維模型）', dispute: '分歧（爭議點）', frontier: '探索區' };
  for (const section of ['consensus', 'dispute', 'frontier']) {
    const items = state.concepts.filter(c => c.section === section);
    if (!items.length) continue;
    wrap.append(el('div', 'sec-title', titles[section]));
    for (const c of items) {
      const row = el('div', 'concept');
      if (state.current && state.current.id === c.id) row.classList.add('active');
      row.append(el('span', `dot ${c.status === 'untested' ? '' : c.status}`));
      row.append(el('span', null, c.name));
      row.onclick = () => selectConcept(c.id);
      wrap.append(row);
    }
  }
}

function renderProgress(progress) {
  const { counts } = progress;
  document.getElementById('progress-body').textContent =
    `未練 ${counts.untested} · 練習中 ${counts.practicing} · 已掌握 ${counts.mastered}`;
}

async function selectConcept(id) {
  state.current = state.concepts.find(c => c.id === id);
  state.question = null;
  renderMap();
  document.getElementById('work-empty').hidden = true;
  document.getElementById('work-body').hidden = false;
  document.getElementById('concept-name').textContent = state.current.name;
  document.getElementById('verdict').hidden = true;
  document.getElementById('question-area').hidden = true;

  const desc = document.getElementById('concept-desc');
  desc.textContent = '生成說明中…';
  try {
    const { description } = await api(`/api/concepts/${id}/explain`, { method: 'POST' });
    desc.textContent = description;
  } catch (e) {
    desc.textContent = `說明生成失敗：${e.message}`;
  }

  const sel = document.getElementById('goal-type');
  sel.replaceChildren();
  for (const g of state.domain.goals) {
    const o = el('option', null, GOAL_LABELS[g] || g);
    o.value = g;
    sel.append(o);
  }
}

document.getElementById('ask-question').onclick = async () => {
  const status = document.getElementById('answer-status');
  status.textContent = '出題中…';
  try {
    const { question } = await api(`/api/concepts/${state.current.id}/questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal_type: document.getElementById('goal-type').value }),
    });
    state.question = question;
    document.getElementById('question-area').hidden = false;
    document.getElementById('question-prompt').textContent = question.prompt;
    const code = document.getElementById('question-code');
    const snippet = question.payload.code_snippet || question.payload.starter_code;
    code.hidden = !snippet;
    code.textContent = snippet || '';
    document.getElementById('answer').value = question.payload.starter_code || '';
    document.getElementById('verdict').hidden = true;
    status.textContent = '';
  } catch (e) {
    status.textContent = `出題失敗：${e.message}`;
  }
};

document.getElementById('submit-answer').onclick = async () => {
  const status = document.getElementById('answer-status');
  status.textContent = '批改中…';
  try {
    const { attempt, concept_status } = await api(`/api/questions/${state.question.id}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: document.getElementById('answer').value }),
    });
    const box = document.getElementById('verdict');
    box.hidden = false;
    box.className = attempt.verdict;
    box.textContent = `${attempt.verdict === 'correct' ? '正確' : attempt.verdict === 'partial' ? '部分正確' : '錯誤'}\n\n${attempt.feedback}` +
      (attempt.root_cause ? `\n\n錯因：${attempt.root_cause}` : '');
    status.textContent = `概念狀態：${concept_status}`;
    await refresh();
    renderMap();
  } catch (e) {
    status.textContent = `批改失敗：${e.message}`;
  }
};

document.getElementById('drawer-toggle').onclick = () => {
  document.getElementById('drawer').hidden = false;
};
document.getElementById('drawer-close').onclick = () => {
  document.getElementById('drawer').hidden = true;
};

document.getElementById('chat-form').onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  const log = document.getElementById('chat-log');
  log.append(el('div', 'msg me', `你：${text}`));
  input.value = '';
  try {
    const { reply } = await api(`/api/domains/${DOMAIN_ID}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, concept_id: state.current ? state.current.id : null }),
    });
    log.append(el('div', 'msg', `導師：${reply}`));
  } catch (err) {
    log.append(el('div', 'msg', `錯誤：${err.message}`));
  }
  log.scrollTop = log.scrollHeight;
};

refresh();
setInterval(() => { if (!state.domain || state.domain.status !== 'ready') refresh(); }, 3000);
```

- [ ] **Step 3: 執行全部測試**

Run: `cd ~/CCProject/learn-system && ./venv/bin/pytest -v`
Expected: 全數通過

- [ ] **Step 4: 用瀏覽器實際走一遍學習迴圈**

啟動服務，用 Playwright 或手動確認：
1. 首頁建立 `python` 領域 → 自動導向領域頁
2. 等生成完成 → 左欄出現三個分區與概念
3. 點一個概念 → 右欄出現說明
4. 選題型 → 出題 → 作答 → 送出 → 看到批改結果與顏色
5. 底部進度數字有更新
6. 點右下「導師」→ 抽屜滑出 → 問一句 → 得到回應
7. 回到首頁 → 掌握度百分比正確

把實際截圖或操作結果記下來（若某步失敗，先修再繼續）。

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject && git add learn-system/ && git commit -m "feat(learn-system): 領域頁 — 智識地圖、工作區、對話抽屜、進度"
```

---

### Task 11: 常駐服務與端到端驗證

**Files:**
- Create: `learn-system/run.sh`
- Create: `~/Library/LaunchAgents/com.steven.learn-system.plist`
- Create: `learn-system/README.md`

**Interfaces:**
- Consumes: `app.create_app()`、`config.DB_PATH`
- Produces: 開機自動常駐於 5990 的服務

- [ ] **Step 1: 建立啟動腳本**

`learn-system/run.sh`：

```bash
#!/bin/bash
cd "$(dirname "$0")" || exit 1
exec ./venv/bin/python -c "
from app import create_app
create_app().run(host='127.0.0.1', port=5990, debug=False, threaded=True)
"
```

```bash
chmod +x ~/CCProject/learn-system/run.sh
```

- [ ] **Step 2: 建立 LaunchAgent**

`~/Library/LaunchAgents/com.steven.learn-system.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.steven.learn-system</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/steven/CCProject/learn-system/run.sh</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/steven/CCProject/learn-system</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/Users/steven/CCProject/learn-system/logs/out.log</string>
  <key>StandardErrorPath</key><string>/Users/steven/CCProject/learn-system/logs/err.log</string>
</dict>
</plist>
```

- [ ] **Step 3: 載入並驗證服務**

```bash
mkdir -p ~/CCProject/learn-system/logs
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.steven.learn-system.plist
sleep 2
curl -s http://127.0.0.1:5990/api/health
```

Expected: `{"ok":true}`

若失敗，檢查 `logs/err.log`。**不要**用 `nohup ... &` 手動啟動（會與 LaunchAgent 衝突，見 CLAUDE.md）。

- [ ] **Step 4: 寫 README**

`learn-system/README.md`：

```markdown
# learn-system

以「概念」為中心的 AI 學習系統，可套用任何領域。

設計文件：`../docs/superpowers/specs/2026-09-26-learn-system-design.md`
實作計畫：`../docs/superpowers/plans/2026-09-26-learn-system.md`

## 啟動

```bash
./run.sh          # 前景執行
```

常駐由 LaunchAgent `com.steven.learn-system` 管理，port 5990。

```bash
launchctl kickstart -k gui/$(id -u)/com.steven.learn-system   # 重啟
curl http://127.0.0.1:5990/api/health                          # 健康檢查
```

## 測試

```bash
./venv/bin/pytest -v
```
```

- [ ] **Step 5: 真實端到端驗證（非 mock）**

用真實 Claude 端到端跑一次（這是唯一一次真的花錢的驗證，值得）：

1. 開 `http://127.0.0.1:5990/`
2. 建立 `python` 領域，勾「能自己寫出來」「能通過檢定」
3. 等智識地圖生成完成，確認概念數合理（應為 10 個左右：5 共識 + 3 分歧 + 2 探索）
4. 點「閉包」→ 說明生成
5. 出「能自己寫出來」的題 → 故意寫錯的答案送出 → 確認判為 wrong 且有錯因
6. 再出同題型 → 寫對的答案 → 確認判為 correct
7. 開抽屜問「閉包跟 late binding 的關係」→ 確認回應合理

記錄結果。任何一步失敗就修到過。

- [ ] **Step 6: 同步到 command-center（專案慣例）**

依 `feedback_command_center_sync.md`，新常駐服務要加進 command-center（5950）的卡片。比照其他服務的卡片格式，新增 learn-system 卡片（名稱、port 5990、健康檢查路徑 `/api/health`）。

- [ ] **Step 7: Commit**

```bash
cd ~/CCProject && git add learn-system/ ~/Library/LaunchAgents/com.steven.learn-system.plist command-center/ && git commit -m "feat(learn-system): LaunchAgent 常駐、README 與 command-center 卡片"
```

---

## 自我審查結果

**Spec 覆蓋檢查**

| Spec 章節 | 對應任務 |
|---|---|
| §2.1 四種目標 | Task 6（出題）、Task 7（批改） |
| §2.2 形態（概念為中心＋抽屜） | Task 9、10 |
| §2.3 語料庫可選 | Task 4（`allow_web`）、Task 8（`verify_sources`） |
| §2.4 通用性 | Task 4 的 `MAP_PROMPT`／Task 6 的 `QUESTION_PROMPT` 皆參數化 |
| §2.5 executable 開關 | Task 5（`override_executable`）、Task 6/7 分支 |
| §3 架構 | Task 5 |
| §4 資料模型 | Task 1 |
| §5 網頁 | Task 9、10 |
| §6 學習迴圈 | Task 10 |
| §7 題型與批改（含題目有效性） | Task 6、7 |
| §8 掌握度 | Task 2 |
| §9 安全與錯誤處理 | Task 3、5、7 |
| §10 測試策略 | 每個任務的 Step 1 |
| §11 範圍外 | 未實作，符合預期 |

**已知需在實作時確認的 SDK 細節**

- `ClaudeAgentOptions` 的 resume 欄位名稱（Task 8 已註明查法）
- `claude-agent-sdk` 版本（Task 4 Step 5 已註明比對 command-center）
