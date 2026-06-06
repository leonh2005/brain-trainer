# 技能樹系統 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個 Starfield 風格的個人 RPG 技能追蹤系統，中央顯示角色，周圍環繞大類圓球，手動記錄 XP 累積升級。

**Architecture:** Flask (port 5500) + SQLite，3 個 HTML template。等級從 XP 即時計算，不存 DB。角色圖上半身取自照片，下半身用 SVG 補畫。

**Tech Stack:** Python 3, Flask, SQLite3, Pillow (圖片裁切), Jinja2, HTML/CSS/JS

---

## 檔案結構

```
skill-tree/
├── app.py                  Flask routes
├── db.py                   SQLite init + queries + level calc
├── seed.py                 初始大類與技能資料
├── skill_tree.db           (自動產生)
├── scripts/
│   └── prep_character.py   裁切角色上半身照片
├── static/
│   ├── style.css           Starfield UI 全域樣式
│   └── img/
│       └── character_upper.png  (由 prep_character.py 產生)
└── templates/
    ├── index.html          主畫面：角色 + 圓球
    ├── category.html       大類技能清單
    └── skill.html          技能詳情 + 記錄 XP
```

---

## Task 1: Project scaffold

**Files:**
- Create: `skill-tree/` (整個目錄)

- [ ] **Step 1: 建立目錄結構**

```bash
mkdir -p /Users/steven/CCProject/skill-tree/static/img
mkdir -p /Users/steven/CCProject/skill-tree/scripts
mkdir -p /Users/steven/CCProject/skill-tree/templates
mkdir -p /Users/steven/CCProject/skill-tree/tests
```

- [ ] **Step 2: 確認 Flask 和 Pillow 已安裝**

```bash
python3 -c "import flask, PIL; print('ok')"
```

Expected: `ok`。若失敗：`pip3 install flask pillow`

- [ ] **Step 3: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/
git commit -m "chore: 建立 skill-tree 專案目錄"
```

---

## Task 2: 資料庫層 (db.py)

**Files:**
- Create: `skill-tree/db.py`
- Create: `skill-tree/tests/test_db.py`

- [ ] **Step 1: 寫測試**

建立 `skill-tree/tests/test_db.py`：

```python
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import db

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    db.init_db()

def test_calc_level_starts_at_1():
    level, current, to_next = db.calc_level(0)
    assert level == 1
    assert current == 0
    assert to_next == 100

def test_calc_level_lv2_at_100xp():
    level, _, _ = db.calc_level(100)
    assert level == 2

def test_calc_level_max_20():
    level, _, _ = db.calc_level(999999)
    assert level == 20

def test_xp_accumulates():
    with db.get_db() as conn:
        conn.execute("INSERT INTO categories (name, emoji, color) VALUES ('Test', '🧪', '#fff')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S1')")
    db.log_xp(1, 50, "a")
    db.log_xp(1, 70, "b")
    assert db.get_skill_xp(1) == 120

def test_skill_detail_returns_level():
    with db.get_db() as conn:
        conn.execute("INSERT INTO categories (name, emoji, color) VALUES ('Test', '🧪', '#fff')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S1')")
    db.log_xp(1, 100, "got lv2")
    detail = db.get_skill_detail(1)
    assert detail['level'] == 2

def test_get_character_level_sum():
    with db.get_db() as conn:
        conn.execute("INSERT INTO categories (name, emoji, color) VALUES ('Test', '🧪', '#fff')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S1')")
        conn.execute("INSERT INTO skills (category_id, name) VALUES (1, 'S2')")
    db.log_xp(1, 100, "")  # skill 1 → lv2
    db.log_xp(2, 0, "")    # skill 2 → lv1
    char = db.get_character()
    assert char['total_level'] == 3  # 2 + 1
```

- [ ] **Step 2: 執行測試，確認 FAIL**

```bash
cd /Users/steven/CCProject/skill-tree
python3 -m pytest tests/test_db.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: 實作 db.py**

建立 `skill-tree/db.py`：

```python
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
```

- [ ] **Step 4: 執行測試，確認 PASS**

```bash
cd /Users/steven/CCProject/skill-tree
python3 -m pytest tests/test_db.py -v
```

Expected: 全部 PASS，5 個測試

- [ ] **Step 5: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/db.py skill-tree/tests/test_db.py
git commit -m "feat: skill-tree db 層 + 等級計算"
```

---

## Task 3: Flask 路由 (app.py)

**Files:**
- Create: `skill-tree/app.py`

- [ ] **Step 1: 建立 app.py**

```python
from flask import Flask, render_template, request, redirect, url_for, jsonify
from db import init_db, get_all_categories, get_skills_by_category, get_skill_detail, log_xp, get_character

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html",
                           categories=get_all_categories(),
                           character=get_character())

@app.route("/category/<int:cat_id>")
def category(cat_id):
    cats = get_all_categories()
    cat = next((c for c in cats if c["id"] == cat_id), None)
    if not cat:
        return "Not found", 404
    return render_template("category.html",
                           category=cat,
                           skills=get_skills_by_category(cat_id))

@app.route("/skill/<int:skill_id>")
def skill(skill_id):
    detail = get_skill_detail(skill_id)
    if not detail:
        return "Not found", 404
    return render_template("skill.html", skill=detail)

@app.route("/skill/<int:skill_id>/log", methods=["POST"])
def log_skill_xp(skill_id):
    hours = request.form.get("hours", "")
    xp_direct = request.form.get("xp", "")
    note = request.form.get("note", "")
    if xp_direct:
        xp = int(xp_direct)
    elif hours:
        xp = int(float(hours) * 60)
    else:
        xp = 0
    if xp > 0:
        log_xp(skill_id, xp, note)
    return redirect(url_for("skill", skill_id=skill_id))

@app.route("/api/character")
def api_character():
    return jsonify(get_character())

@app.route("/api/categories")
def api_categories():
    return jsonify(get_all_categories())

if __name__ == "__main__":
    init_db()
    app.run(port=5500, debug=True)
```

- [ ] **Step 2: 確認語法無誤**

```bash
cd /Users/steven/CCProject/skill-tree
python3 -c "import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/app.py
git commit -m "feat: skill-tree Flask routes"
```

---

## Task 4: 角色照片裁切

**Files:**
- Create: `skill-tree/scripts/prep_character.py`
- Create: `skill-tree/static/img/character_upper.png` (由腳本產生)

- [ ] **Step 1: 建立 prep_character.py**

```python
from PIL import Image
import os

SRC = "/Users/steven/Downloads/sddefault.jpg"
OUT = os.path.join(os.path.dirname(__file__), "../static/img/character_upper.png")

img = Image.open(SRC)
w, h = img.size  # 640x480

# 右側人物：從約 x=370 到 x=530，y=30 到 y=310（頭頂到腰部）
crop = img.crop((370, 30, 530, 310))
crop.save(OUT)
print(f"Saved {crop.size} → {OUT}")
```

- [ ] **Step 2: 執行腳本**

```bash
cd /Users/steven/CCProject/skill-tree
python3 scripts/prep_character.py
```

Expected: `Saved (160, 280) → .../character_upper.png`

- [ ] **Step 3: 用預覽確認裁切正確**

```bash
open /Users/steven/CCProject/skill-tree/static/img/character_upper.png
```

若人物位置偏差，調整 `prep_character.py` 中的 crop 座標後重新執行。

- [ ] **Step 4: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/scripts/prep_character.py skill-tree/static/img/character_upper.png
git commit -m "feat: 裁切角色上半身照片"
```

---

## Task 5: Starfield 樣式 (style.css)

**Files:**
- Create: `skill-tree/static/style.css`

- [ ] **Step 1: 建立 style.css**

```css
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg: #07080f;
  --bg2: #0d1020;
  --border: rgba(80, 120, 200, 0.25);
  --text: #b8cce0;
  --text-dim: #4a6080;
  --accent: #3af;
}

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Share Tech Mono', 'Courier New', monospace;
  min-height: 100vh;
  overflow-x: hidden;
}

/* Grid background */
body::before {
  content: '';
  position: fixed;
  inset: 0;
  background-image:
    linear-gradient(rgba(40,80,160,0.06) 1px, transparent 1px),
    linear-gradient(90deg, rgba(40,80,160,0.06) 1px, transparent 1px);
  background-size: 40px 40px;
  pointer-events: none;
  z-index: 0;
}

.screen { position: relative; z-index: 1; }

/* ─── Header ─── */
.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 32px;
  border-bottom: 1px solid var(--border);
  text-transform: uppercase;
  font-size: 11px;
  letter-spacing: 3px;
  color: var(--text-dim);
}
.header .title { color: var(--text); font-size: 13px; }

/* ─── Character Arena ─── */
.character-arena {
  position: relative;
  width: 600px;
  height: 600px;
  margin: 20px auto;
}

.orbit-ring {
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  width: 480px; height: 480px;
  border-radius: 50%;
  border: 1px solid var(--border);
  box-shadow: 0 0 30px rgba(40,100,255,0.08) inset;
}

/* ─── Character figure ─── */
.character-container {
  position: absolute;
  top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  user-select: none;
}

.character-name {
  font-size: 15px;
  letter-spacing: 4px;
  color: var(--accent);
  text-shadow: 0 0 12px rgba(34,170,255,0.6);
  margin-bottom: 2px;
}

.character-level {
  font-size: 10px;
  letter-spacing: 3px;
  color: var(--text-dim);
  margin-bottom: 10px;
}

.character-figure {
  position: relative;
  display: inline-block;
}

.char-upper {
  display: block;
  width: 140px;
  height: auto;
  filter: brightness(0.9) contrast(1.1);
}

.char-lower {
  display: block;
  width: 120px;
  margin: 0 auto;
}

/* Scan line overlay */
.character-figure::after {
  content: '';
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 3px,
    rgba(0,0,0,0.08) 3px,
    rgba(0,0,0,0.08) 4px
  );
  pointer-events: none;
}

/* Glow outline around figure */
.character-container::before {
  content: '';
  position: absolute;
  top: 28px; left: 50%;
  transform: translateX(-50%);
  width: 150px;
  height: calc(100% - 28px);
  border: 1px solid rgba(34,170,255,0.15);
  box-shadow: 0 0 20px rgba(34,170,255,0.1);
  pointer-events: none;
}

/* ─── Orbs ─── */
.orb {
  position: absolute;
  top: 50%; left: 50%;
  width: 88px; height: 88px;
  margin: -44px;
  --r: 230px;
  transform:
    rotate(var(--angle))
    translateY(calc(-1 * var(--r)))
    rotate(calc(-1 * var(--angle)));
  text-decoration: none;
  color: inherit;
}

.orb-inner {
  width: 100%; height: 100%;
  border-radius: 50%;
  background: var(--bg2);
  border: 1px solid color-mix(in srgb, var(--color) 40%, transparent);
  box-shadow:
    0 0 16px color-mix(in srgb, var(--color) 20%, transparent),
    inset 0 0 20px color-mix(in srgb, var(--color) 08%, transparent);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  transition: box-shadow 0.2s, transform 0.2s;
  cursor: pointer;
}

.orb-inner:hover {
  box-shadow:
    0 0 28px color-mix(in srgb, var(--color) 50%, transparent),
    inset 0 0 20px color-mix(in srgb, var(--color) 15%, transparent);
  transform: scale(1.08);
}

.orb-emoji { font-size: 22px; }
.orb-name  { font-size: 9px; letter-spacing: 1.5px; color: var(--text-dim); text-transform: uppercase; }
.orb-level { font-size: 10px; color: var(--color); }

/* ─── Footer ─── */
.footer {
  display: flex;
  justify-content: center;
  gap: 48px;
  padding: 12px;
  border-top: 1px solid var(--border);
  font-size: 10px;
  letter-spacing: 2px;
  color: var(--text-dim);
  text-transform: uppercase;
}

/* ─── Category page ─── */
.page-header {
  padding: 24px 32px 16px;
  border-bottom: 1px solid var(--border);
}

.back-link {
  font-size: 10px;
  letter-spacing: 2px;
  color: var(--text-dim);
  text-decoration: none;
  text-transform: uppercase;
}
.back-link:hover { color: var(--accent); }

.page-title {
  font-size: 20px;
  letter-spacing: 4px;
  text-transform: uppercase;
  margin-top: 8px;
  color: var(--accent);
  text-shadow: 0 0 12px rgba(34,170,255,0.4);
}

.skills-list {
  padding: 24px 32px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-width: 700px;
}

.skill-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  padding: 14px 18px;
  text-decoration: none;
  color: inherit;
  display: block;
  transition: border-color 0.15s;
}
.skill-card:hover { border-color: var(--accent); }
.skill-card.child { margin-left: 24px; border-left: 2px solid var(--border); }

.skill-card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 8px;
}

.skill-card-name { font-size: 13px; letter-spacing: 1px; }
.skill-card-level { font-size: 11px; color: var(--accent); }

.xp-bar-bg {
  height: 3px;
  background: rgba(255,255,255,0.06);
  width: 100%;
}

.xp-bar-fill {
  height: 100%;
  background: var(--bar-color, var(--accent));
  box-shadow: 0 0 6px var(--bar-color, var(--accent));
}

/* ─── Skill detail page ─── */
.skill-detail {
  padding: 32px;
  max-width: 600px;
}

.skill-level-big {
  font-size: 48px;
  color: var(--accent);
  text-shadow: 0 0 20px rgba(34,170,255,0.5);
  letter-spacing: 4px;
}

.skill-xp-bar-wrap { margin: 16px 0; }
.skill-xp-label { font-size: 10px; color: var(--text-dim); margin-bottom: 6px; letter-spacing: 2px; }

.xp-form {
  background: var(--bg2);
  border: 1px solid var(--border);
  padding: 20px;
  margin: 24px 0;
}

.xp-form h3 { font-size: 11px; letter-spacing: 3px; color: var(--text-dim); margin-bottom: 16px; text-transform: uppercase; }

.form-row { display: flex; gap: 12px; margin-bottom: 12px; align-items: center; }
.form-row label { font-size: 10px; color: var(--text-dim); width: 60px; letter-spacing: 1px; text-transform: uppercase; }

input[type=text], input[type=number] {
  background: var(--bg);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 6px 10px;
  font-family: inherit;
  font-size: 12px;
  flex: 1;
  outline: none;
}
input:focus { border-color: var(--accent); }

.btn {
  background: transparent;
  border: 1px solid var(--accent);
  color: var(--accent);
  padding: 8px 24px;
  font-family: inherit;
  font-size: 11px;
  letter-spacing: 2px;
  text-transform: uppercase;
  cursor: pointer;
  transition: background 0.15s;
}
.btn:hover { background: rgba(34,170,255,0.1); }

.log-list { margin-top: 24px; }
.log-list h3 { font-size: 10px; letter-spacing: 3px; color: var(--text-dim); margin-bottom: 12px; text-transform: uppercase; }

.log-entry {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 11px;
}
.log-xp { color: var(--accent); }
.log-note { color: var(--text-dim); flex: 1; padding: 0 16px; }
.log-date { color: var(--text-dim); font-size: 10px; }
```

- [ ] **Step 2: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/static/style.css
git commit -m "feat: Starfield UI CSS"
```

---

## Task 6: 主畫面 (index.html)

**Files:**
- Create: `skill-tree/templates/index.html`

- [ ] **Step 1: 建立 index.html**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>STEVEN — SKILL REGISTRY</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div class="screen">

  <div class="header">
    <span class="title">◈ SKILL REGISTRY</span>
    <span>{{ character.skill_count }} SKILLS TRACKED</span>
  </div>

  <div class="character-arena">
    <div class="orbit-ring"></div>

    <!-- 角色 -->
    <div class="character-container">
      <div class="character-name">{{ character.name }}</div>
      <div class="character-level">LEVEL {{ character.total_level }}</div>
      <div class="character-figure">
        <img class="char-upper" src="/static/img/character_upper.png" alt="character">
        <svg class="char-lower" viewBox="0 0 120 130" xmlns="http://www.w3.org/2000/svg">
          <!-- 腰帶 -->
          <rect x="22" y="0" width="76" height="10" rx="1" fill="#2a2a3e"/>
          <!-- 左褲管 -->
          <rect x="24" y="8" width="30" height="88" rx="2" fill="#1c1c2e"/>
          <!-- 右褲管 -->
          <rect x="66" y="8" width="30" height="88" rx="2" fill="#1c1c2e"/>
          <!-- 皺褶陰影 -->
          <rect x="52" y="10" width="4" height="85" fill="#15152a"/>
          <!-- 左鞋 -->
          <ellipse cx="39" cy="102" rx="24" ry="8" fill="#0f0f18"/>
          <ellipse cx="44" cy="100" rx="18" ry="5" fill="#191926"/>
          <!-- 右鞋 -->
          <ellipse cx="81" cy="102" rx="24" ry="8" fill="#0f0f18"/>
          <ellipse cx="86" cy="100" rx="18" ry="5" fill="#191926"/>
        </svg>
      </div>
    </div>

    <!-- 大類圓球 -->
    {% for cat in categories %}
    {% set angle = loop.index0 * (360 / categories|length) %}
    <a class="orb" href="/category/{{ cat.id }}"
       style="--angle: {{ angle }}deg; --color: {{ cat.color }}">
      <div class="orb-inner">
        <div class="orb-emoji">{{ cat.emoji }}</div>
        <div class="orb-name">{{ cat.name }}</div>
        <div class="orb-level">LV {{ cat.avg_level | int }}</div>
      </div>
    </a>
    {% endfor %}
  </div>

  <div class="footer">
    <span>SKILLS {{ character.skill_count }}</span>
    <span>TOTAL LEVEL {{ character.total_level }}</span>
  </div>

</div>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/templates/index.html
git commit -m "feat: 主畫面 index.html"
```

---

## Task 7: 大類技能清單 (category.html)

**Files:**
- Create: `skill-tree/templates/category.html`

- [ ] **Step 1: 建立 category.html**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>{{ category.name }} — SKILL REGISTRY</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div class="screen">

  <div class="page-header">
    <a class="back-link" href="/">← BACK TO REGISTRY</a>
    <div class="page-title" style="color: {{ category.color }}; text-shadow: 0 0 12px {{ category.color }}66;">
      {{ category.emoji }} {{ category.name }}
    </div>
  </div>

  <div class="skills-list">
    {% for skill in skills %}
    <a class="skill-card {% if skill.parent_id %}child{% endif %}"
       href="/skill/{{ skill.id }}"
       style="--bar-color: {{ category.color }}">
      <div class="skill-card-header">
        <span class="skill-card-name">{{ skill.name }}</span>
        <span class="skill-card-level" style="color: {{ category.color }}">LV {{ skill.level }}</span>
      </div>
      <div class="xp-bar-bg">
        <div class="xp-bar-fill"
             style="width: {{ skill.progress_pct }}%; --bar-color: {{ category.color }}"></div>
      </div>
    </a>
    {% else %}
    <div style="color: var(--text-dim); font-size: 11px; letter-spacing: 2px;">NO SKILLS YET</div>
    {% endfor %}
  </div>

</div>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/templates/category.html
git commit -m "feat: 大類技能清單頁"
```

---

## Task 8: 技能詳情頁 (skill.html)

**Files:**
- Create: `skill-tree/templates/skill.html`

- [ ] **Step 1: 建立 skill.html**

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>{{ skill.name }} — SKILL REGISTRY</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div class="screen">

  <div class="page-header">
    <a class="back-link" href="/category/{{ skill.category_id }}">← {{ skill.category_name }}</a>
    <div class="page-title">{{ skill.name }}</div>
  </div>

  <div class="skill-detail">

    <!-- 等級顯示 -->
    <div class="skill-level-big"
         style="color: {{ skill.category_color }}; text-shadow: 0 0 20px {{ skill.category_color }}66;">
      LV {{ skill.level }}
    </div>

    <!-- XP 進度條 -->
    <div class="skill-xp-bar-wrap">
      <div class="skill-xp-label">
        {% if skill.level < 20 %}
          XP {{ skill.current_xp }} / {{ skill.xp_to_next }}  ·  TOTAL {{ skill.total_xp }}
        {% else %}
          MAX LEVEL  ·  TOTAL {{ skill.total_xp }} XP
        {% endif %}
      </div>
      <div class="xp-bar-bg" style="height: 5px;">
        <div class="xp-bar-fill"
             style="width: {{ skill.progress_pct }}%; --bar-color: {{ skill.category_color }}; height: 100%;"></div>
      </div>
    </div>

    <!-- 記錄 XP 表單 -->
    {% if skill.level < 20 %}
    <form class="xp-form" method="POST" action="/skill/{{ skill.id }}/log">
      <h3>+ Record XP</h3>
      <div class="form-row">
        <label>Hours</label>
        <input type="number" name="hours" placeholder="0.5" step="0.25" min="0">
        <span style="font-size:10px; color:var(--text-dim); padding:0 8px;">OR</span>
        <label>XP</label>
        <input type="number" name="xp" placeholder="0" min="0">
      </div>
      <div class="form-row">
        <label>Note</label>
        <input type="text" name="note" placeholder="今天做了什麼...">
      </div>
      <button class="btn" type="submit">SUBMIT</button>
    </form>
    {% endif %}

    <!-- 歷史記錄 -->
    <div class="log-list">
      <h3>XP Log</h3>
      {% for entry in skill.logs %}
      <div class="log-entry">
        <span class="log-xp">+{{ entry.xp }} XP</span>
        <span class="log-note">{{ entry.note or '—' }}</span>
        <span class="log-date">{{ entry.logged_at[:10] }}</span>
      </div>
      {% else %}
      <div style="color: var(--text-dim); font-size: 11px;">NO RECORDS YET</div>
      {% endfor %}
    </div>

  </div>

</div>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/templates/skill.html
git commit -m "feat: 技能詳情頁 + XP 記錄表單"
```

---

## Task 9: Seed 初始資料

**Files:**
- Create: `skill-tree/seed.py`

- [ ] **Step 1: 建立 seed.py**

```python
from db import get_db, init_db

def seed():
    init_db()
    with get_db() as conn:
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 0:
            print("Already seeded, skipping.")
            return

        conn.executemany(
            "INSERT INTO categories (name, emoji, color) VALUES (?, ?, ?)",
            [
                ("程式開發", "💻", "#00ff88"),
                ("投資交易", "📈", "#ffaa00"),
                ("身體健康", "🏋️", "#ff4466"),
                ("語言",     "🌐", "#44aaff"),
                ("其他",     "🎯", "#cc88ff"),
            ]
        )

        conn.executemany(
            "INSERT INTO skills (category_id, parent_id, name, description) VALUES (?, ?, ?, ?)",
            [
                (1, None, "Python",     "Python 程式語言"),
                (1, None, "Flask",      "Flask 後端框架"),
                (1, None, "JavaScript", "前端 JS"),
                (2, None, "技術分析",   "K線、均線等技術指標"),
                (2, None, "選股策略",   "多空篩選方法"),
                (3, None, "重訓",       "肌力訓練"),
                (3, None, "有氧",       "跑步、騎車等"),
                (4, None, "英文",       "英語能力"),
                (4, None, "日文",       "日語能力"),
                (5, None, "烹飪",       "料理技術"),
            ]
        )
        print("Seeded successfully.")

if __name__ == "__main__":
    seed()
```

- [ ] **Step 2: 執行 seed**

```bash
cd /Users/steven/CCProject/skill-tree
python3 seed.py
```

Expected: `Seeded successfully.`

- [ ] **Step 3: Commit**

```bash
cd /Users/steven/CCProject
git add skill-tree/seed.py
git commit -m "feat: seed 初始大類與技能"
```

---

## Task 10: 啟動與驗證

**Files:** 無新增

- [ ] **Step 1: 啟動服務**

```bash
cd /Users/steven/CCProject/skill-tree
python3 app.py
```

Expected:
```
 * Running on http://127.0.0.1:5500
 * Debug mode: on
```

- [ ] **Step 2: 驗證 API**

另開 terminal：

```bash
curl -s http://localhost:5500/api/character | python3 -m json.tool
```

Expected: JSON 含 `"name": "STEVEN"`, `"skill_count": 10`

```bash
curl -s http://localhost:5500/api/categories | python3 -m json.tool | head -20
```

Expected: JSON 陣列含 5 個大類

- [ ] **Step 3: 瀏覽器開啟確認**

```bash
open http://localhost:5500
```

確認：
- 深黑背景格線
- 中央人物圖（白襯衫）
- 5 個彩色圓球環繞
- 點圓球可進入技能清單
- 點技能可記錄 XP

- [ ] **Step 4: 測試記錄 XP**

在任一技能頁面填入「0.5 小時」，送出，確認回到詳情頁後：
- XP 增加 30（0.5h × 60）
- 進度條更新
- 歷史記錄出現新條目

- [ ] **Step 5: Final commit**

```bash
cd /Users/steven/CCProject
git add -A
git commit -m "feat: skill-tree 系統完成初版"
```
