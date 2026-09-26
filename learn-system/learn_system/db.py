import json
import sqlite3
from datetime import datetime, timezone

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


def list_attempts_for_domain(conn, domain_id, limit=None):
    sql = ("SELECT a.*, q.concept_id, c.name AS concept_name FROM attempts a"
           " JOIN questions q ON q.id = a.question_id"
           " JOIN concepts c ON c.id = q.concept_id"
           " WHERE c.domain_id = ? ORDER BY a.id DESC")
    params = [domain_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return _rows(conn.execute(sql, params))
