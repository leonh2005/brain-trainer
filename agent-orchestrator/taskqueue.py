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
    heartbeat_at REAL,
    kind           TEXT NOT NULL DEFAULT 'task',
    next_attempt_at REAL
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
    _migrate(conn)


def _migrate(conn):
    """就地把舊 DB 補上新欄位（idempotent）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "kind" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'task'")
    if "next_attempt_at" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN next_attempt_at REAL")


def add_task(conn, title, spec, cwd=None, depends_on=None, source="cli", kind="task",
             status="pending", parent_id=None):
    """建立任務。status 與 parent_id 可在插入時就指定 —— 拆解出的子任務必須一次
    寫成 blocked，否則並行的 daemon 會在「先 pending 再改」的窗口把它搶去執行。"""
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status}")
    task_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO tasks (id, title, spec, status, depends_on, cwd, source, kind,"
        " parent_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (task_id, title, spec, status, json.dumps(depends_on or []), cwd, source,
         kind, parent_id, time.time()),
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


def finish_if_running(conn, task_id, started_at, status, **fields):
    """只有當任務仍是「這次執行」（status='running' 且 started_at 相符）時才寫入。
    回傳是否寫入 —— False 表示它已被 reap 或其他流程改過，不該覆蓋。"""
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status: {status}")
    cols = ["status = ?"]
    vals = [status]
    for key, val in fields.items():
        cols.append(f"{key} = ?")
        vals.append(val)
    vals.extend([task_id, started_at])
    cur = conn.execute(
        f"UPDATE tasks SET {', '.join(cols)}"
        " WHERE id = ? AND status = 'running' AND started_at = ?",
        vals,
    )
    return cur.rowcount > 0


def claim_next(conn):
    """原子地認領一個可跑的任務並標 running；沒有則回 None。
    可跑 = status 為 pending 且 depends_on 全部 done。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'pending'"
            " AND (next_attempt_at IS NULL OR next_attempt_at <= ?)"
            " ORDER BY created_at",
            (time.time(),),
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
