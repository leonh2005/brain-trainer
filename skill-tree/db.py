import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "skill_tree.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            emoji      TEXT,
            color      TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS skills (
            id          INTEGER PRIMARY KEY,
            category_id INTEGER REFERENCES categories(id),
            parent_id   INTEGER REFERENCES skills(id),
            name        TEXT NOT NULL,
            description TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xp_logs (
            id        INTEGER PRIMARY KEY,
            skill_id  INTEGER REFERENCES skills(id),
            xp        INTEGER NOT NULL,
            note      TEXT,
            logged_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

def xp_for_next_level(n):
    return int(100 * (n ** 1.5))

def calc_level(total_xp):
    """Returns (level, current_xp_in_level, xp_to_next_level)"""
    level = 1
    accumulated = 0
    while level < 20:
        needed = xp_for_next_level(level)
        if accumulated + needed > total_xp:
            break
        accumulated += needed
        level += 1
    if level >= 20:
        return (20, 0, 0)
    current = total_xp - accumulated
    to_next = xp_for_next_level(level)
    return (level, current, to_next)

def get_skill_xp(skill_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(xp), 0) as total FROM xp_logs WHERE skill_id = ?",
            (skill_id,)
        ).fetchone()
        return row["total"]

def get_all_categories():
    with get_db() as conn:
        cats = conn.execute("SELECT * FROM categories ORDER BY id").fetchall()
    result = []
    for cat in cats:
        with get_db() as conn:
            skill_ids = conn.execute(
                "SELECT id FROM skills WHERE category_id = ?", (cat["id"],)
            ).fetchall()
        levels = [calc_level(get_skill_xp(s["id"]))[0] for s in skill_ids]
        avg = round(sum(levels) / len(levels), 1) if levels else 0
        result.append({
            "id": cat["id"],
            "name": cat["name"],
            "emoji": cat["emoji"],
            "color": cat["color"],
            "avg_level": avg,
            "skill_count": len(skill_ids),
        })
    return result

def get_skills_by_category(category_id):
    with get_db() as conn:
        skills = conn.execute(
            "SELECT * FROM skills WHERE category_id = ? ORDER BY parent_id NULLS FIRST, id",
            (category_id,)
        ).fetchall()
    result = []
    for s in skills:
        total_xp = get_skill_xp(s["id"])
        lv, current, to_next = calc_level(total_xp)
        result.append({
            "id": s["id"],
            "name": s["name"],
            "description": s["description"],
            "parent_id": s["parent_id"],
            "level": lv,
            "total_xp": total_xp,
            "current_xp": current,
            "xp_to_next": to_next,
            "progress_pct": round(current / to_next * 100, 1) if to_next > 0 else 100,
        })
    return result

def get_skill_detail(skill_id):
    with get_db() as conn:
        s = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if not s:
            return None
        cat = conn.execute("SELECT * FROM categories WHERE id = ?", (s["category_id"],)).fetchone()
        logs = conn.execute(
            "SELECT * FROM xp_logs WHERE skill_id = ? ORDER BY logged_at DESC LIMIT 50",
            (skill_id,)
        ).fetchall()
    total_xp = get_skill_xp(skill_id)
    lv, current, to_next = calc_level(total_xp)
    return {
        "id": s["id"],
        "name": s["name"],
        "description": s["description"],
        "category_id": s["category_id"],
        "category_name": cat["name"],
        "category_color": cat["color"],
        "level": lv,
        "total_xp": total_xp,
        "current_xp": current,
        "xp_to_next": to_next,
        "progress_pct": round(current / to_next * 100, 1) if to_next > 0 else 100,
        "logs": [dict(l) for l in logs],
    }

def update_category(cat_id, name, emoji, color):
    with get_db() as conn:
        conn.execute(
            "UPDATE categories SET name=?, emoji=?, color=? WHERE id=?",
            (name, emoji, color, cat_id)
        )

def add_category(name, emoji, color):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO categories (name, emoji, color) VALUES (?, ?, ?)",
            (name, emoji, color)
        )
        return cursor.lastrowid

def delete_category(cat_id):
    with get_db() as conn:
        # 刪除底下的 xp_logs 和 skills，再刪 category
        skill_ids = [r["id"] for r in conn.execute(
            "SELECT id FROM skills WHERE category_id=?", (cat_id,)
        ).fetchall()]
        for sid in skill_ids:
            conn.execute("DELETE FROM xp_logs WHERE skill_id=?", (sid,))
        conn.execute("DELETE FROM skills WHERE category_id=?", (cat_id,))
        conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))

def delete_skill(skill_id):
    with get_db() as conn:
        # 收集所有後代 ID（支援多層）
        to_delete = []
        queue = [skill_id]
        while queue:
            sid = queue.pop()
            to_delete.append(sid)
            children = [r["id"] for r in conn.execute(
                "SELECT id FROM skills WHERE parent_id=?", (sid,)
            ).fetchall()]
            queue.extend(children)
        for sid in to_delete:
            conn.execute("DELETE FROM xp_logs WHERE skill_id=?", (sid,))
            conn.execute("DELETE FROM skills WHERE id=?", (sid,))

def update_skill(skill_id, name, description):
    with get_db() as conn:
        conn.execute(
            "UPDATE skills SET name=?, description=? WHERE id=?",
            (name, description, skill_id)
        )

def add_skill(category_id, parent_id, name, description):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO skills (category_id, parent_id, name, description) VALUES (?, ?, ?, ?)",
            (category_id, parent_id, name, description)
        )
        return cursor.lastrowid

def log_xp(skill_id, xp, note):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO xp_logs (skill_id, xp, note) VALUES (?, ?, ?)",
            (skill_id, xp, note)
        )

def get_character():
    with get_db() as conn:
        skill_ids = conn.execute("SELECT id FROM skills").fetchall()
    total_level = sum(calc_level(get_skill_xp(s["id"]))[0] for s in skill_ids)
    return {
        "name": "STEVEN",
        "total_level": total_level,
        "skill_count": len(skill_ids),
    }
