# 技能樹系統 設計文件

**日期：** 2026-06-06  
**使用者：** Steven（個人專用）  
**狀態：** 已確認，待實作

---

## 概覽

一個 RPG 風格的個人技能追蹤系統。主畫面以 Starfield 美術風格呈現：深黑背景、全息投影感 UI、中央放 Steven 的角色圖，四周靜態分佈數個彩色大類圓球。手動記錄練習時間換算為 XP，累積升級。

---

## 架構

```
Flask (port 5500)
├── 前端
│   ├── index.html       主畫面（角色 + 圓球）
│   ├── category.html    大類技能清單
│   └── skill.html       技能詳情 + 記錄 XP
│
├── API
│   ├── GET  /api/character        角色總覽（等級、總 XP）
│   ├── GET  /api/categories       所有大類
│   ├── GET  /api/skills/:cat_id   某大類的技能樹
│   └── POST /api/log              手動記錄 XP
│
└── SQLite (skill_tree.db)
```

---

## 資料模型

```sql
-- 大類（對應圓球）
CREATE TABLE categories (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  emoji      TEXT,
  color      TEXT,          -- hex，用於圓球主色
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 技能（可巢狀，parent_id = NULL 為頂層）
CREATE TABLE skills (
  id          INTEGER PRIMARY KEY,
  category_id INTEGER REFERENCES categories(id),
  parent_id   INTEGER REFERENCES skills(id),
  name        TEXT NOT NULL,
  description TEXT,
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- XP 記錄（每次手動輸入）
CREATE TABLE xp_logs (
  id         INTEGER PRIMARY KEY,
  skill_id   INTEGER REFERENCES skills(id),
  xp         INTEGER NOT NULL,
  note       TEXT,
  logged_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 等級計算（Computed，不存 DB）

- **XP 換算：** 1 小時 = 60 XP（可在設定中調整）
- **升級門檻：** LvN → LvN+1 需要 `100 × N^1.5` XP
  - Lv1→2：100 XP
  - Lv5→6：559 XP
  - Lv10→11：3,162 XP
  - Lv19→20：8,280 XP
- **最高等級：** Lv 20
- **大類等級：** 該大類所有技能等級平均值
- **角色等級：** 所有技能等級加總

---

## 初始大類

| emoji | 名稱 | 顏色 |
|-------|------|------|
| 💻 | 程式開發 | #00ff88 |
| 📈 | 投資交易 | #ffaa00 |
| 🏋️ | 身體健康 | #ff4466 |
| 🌐 | 語言 | #44aaff |
| 🎯 | 其他 | #cc88ff |

---

## 視覺設計

### 整體風格
- **主題：** Starfield 科幻 UI — 深黑底（#0a0a0f）、細邊框發光、掃描線、全息投影感
- **字型：** 等寬字型（Courier New 或 monospace），大寫標題

### 主畫面（index.html）
- 背景：深黑 + 細格線
- 中央：角色圖（上半身取自 `/Users/steven/Downloads/sddefault.jpg` 右側人物裁切，下半身補畫深色長褲 + 皮鞋）
- 角色周圍套發光輪廓 + 掃描線動畫
- 角色上方：`STEVEN  LV.XX`
- 5 個大類圓球靜態分佈在角色周圍，顯示 emoji + 名稱 + 進度環

### 技能分類頁（category.html）
- 標題列：大類名稱 + 主色主題
- 技能卡片：名稱、LvX、XP 進度條
- 子技能縮排顯示（樹狀）

### 技能詳情頁（skill.html）
- 技能名稱 + 等級 + 大 XP 進度條
- 「+ 記錄 XP」：輸入小時數（自動換算）或直接輸入 XP + 備註
- 歷史記錄清單：日期、XP、備註

---

## 檔案結構

```
skill-tree/
├── app.py
├── db.py           (SQLite init + queries)
├── skill_tree.db
└── templates/
    ├── index.html
    ├── category.html
    └── skill.html
```

---

## 不在範圍內

- 多使用者支援
- 行動版 RWD（桌機優先）
- 自動 XP（所有記錄皆手動）
- 備份 / 匯出功能（第一版不做）
