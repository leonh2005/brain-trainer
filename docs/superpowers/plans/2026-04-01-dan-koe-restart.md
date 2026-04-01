# Dan Koe 一日人生重啟網站 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一個單頁靜態 HTML 網站，讓使用者依照 Dan Koe 方法論完成「一日人生重啟」三階段反思日誌，資料存於 localStorage。

**Architecture:** 單一 `index.html` 內嵌 CSS 與 JS，無外部依賴，無需伺服器。所有輸入框以 `data-section` / `data-field` 屬性標記，JS 統一讀寫 localStorage。日期作為每日紀錄的 key。

**Tech Stack:** 純 HTML5 + CSS3 + Vanilla JavaScript，localStorage API

---

## 檔案結構

```
dan-koe-restart/
└── index.html    ← 全部 HTML / CSS / JS 在此
```

---

## Task 1: 建立專案資料夾與基礎 HTML 骨架 + CSS

**Files:**
- Create: `dan-koe-restart/index.html`

- [ ] **Step 1: 建立資料夾**

```bash
mkdir -p /Users/steven/CCProject/dan-koe-restart
```

- [ ] **Step 2: 建立 index.html（骨架 + 完整 CSS）**

建立 `dan-koe-restart/index.html`，內容如下：

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>一日人生重啟</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: #ede8e0;
    font-family: 'Georgia', 'Microsoft JhengHei', '微軟正黑體', serif;
    color: #2d2d2d;
    font-size: 18px;
  }

  /* ── 頂部導覽 ── */
  .top-bar {
    background: #e8e0d4;
    border-bottom: 1px solid #d8cfc0;
    padding: 20px 48px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .top-bar .date { font-size: 17px; color: #a09080; letter-spacing: 1px; }
  .top-bar .site-title { font-size: 26px; color: #2d2d2d; font-weight: 400; letter-spacing: 2px; }
  .progress-dots { display: flex; gap: 12px; align-items: center; }
  .dot {
    width: 14px; height: 14px;
    border-radius: 50%;
    background: #c8bfb0;
    transition: background 0.4s;
    cursor: default;
    position: relative;
  }
  .dot[title]:hover::after {
    content: attr(title);
    position: absolute;
    bottom: -28px;
    right: 0;
    background: #2d2d2d;
    color: #f5f0e8;
    font-size: 12px;
    padding: 3px 8px;
    border-radius: 4px;
    white-space: nowrap;
  }
  .dot.partial { background: #c9963a; }
  .dot.done    { background: #8b6914; }

  /* ── Hero ── */
  .hero {
    background: #e8e0d4;
    padding: 64px 48px 52px;
    text-align: center;
    border-bottom: 1px solid #d8cfc0;
  }
  .hero .badge {
    display: inline-block;
    background: #8b6914;
    color: #f5f0e8;
    font-size: 15px;
    letter-spacing: 3px;
    padding: 7px 22px;
    border-radius: 20px;
    margin-bottom: 24px;
  }
  .hero h1 {
    font-size: 52px;
    font-weight: 300;
    line-height: 1.5;
    color: #1a1a1a;
    margin-bottom: 14px;
  }
  .hero .subtitle { font-size: 20px; color: #9a8a78; }

  /* ── Section ── */
  .section { width: 100%; padding: 48px 48px; }

  .section-header {
    display: flex;
    align-items: center;
    gap: 20px;
    margin-bottom: 32px;
  }
  .section-icon {
    width: 50px; height: 50px;
    background: #8b6914;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    color: #f5f0e8; font-size: 19px;
    flex-shrink: 0;
  }
  .section-title { font-size: 30px; font-weight: 400; }
  .section-sub   { font-size: 17px; color: #9a8a78; margin-top: 5px; }

  /* ── 兩欄卡片 ── */
  .two-col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 22px;
    margin-bottom: 22px;
  }

  /* ── 卡片 ── */
  .card {
    background: #f0e9dc;
    border: 1px solid #d8cfc0;
    border-radius: 14px;
    padding: 30px 34px;
  }
  .card .q-label {
    font-size: 14px;
    color: #8b6914;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 14px;
  }
  .card .question {
    font-size: 22px;
    color: #2d2d2d;
    line-height: 1.8;
    margin-bottom: 20px;
  }

  /* ── 輸入框 ── */
  textarea, input[type="text"] {
    width: 100%;
    background: #e8e0d4;
    border: 1px solid #d0c7b8;
    border-radius: 8px;
    padding: 14px 18px;
    font-size: 18px;
    color: #3d3025;
    font-family: inherit;
    transition: border-color 0.2s;
    outline: none;
  }
  textarea {
    height: 104px;
    resize: vertical;
  }
  input[type="text"] { height: 54px; }
  textarea:focus, input[type="text"]:focus {
    border-color: #8b6914;
  }
  textarea::placeholder, input::placeholder { color: #b0a090; }

  /* ── 資訊卡 ── */
  .info-card {
    background: #f5edcc;
    border: 1px solid #e0cc80;
    border-radius: 14px;
    padding: 24px 34px;
    margin-bottom: 22px;
    font-size: 19px;
    color: #7a5c00;
    line-height: 1.8;
  }
  .info-card strong {
    display: block;
    margin-bottom: 6px;
    font-size: 15px;
    letter-spacing: 1px;
  }

  /* ── 三欄目標 ── */
  .goals-grid {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 20px;
  }
  .goal-item {
    background: #f0e9dc;
    border: 1px solid #d8cfc0;
    border-radius: 14px;
    padding: 24px 30px;
  }
  .goal-item .timeframe {
    font-size: 15px;
    color: #8b6914;
    letter-spacing: 1px;
    margin-bottom: 14px;
    text-transform: uppercase;
  }
  .goals-label {
    font-size: 19px;
    color: #8b6914;
    letter-spacing: 1px;
    margin-bottom: 18px;
    padding-left: 4px;
  }

  /* ── 分隔線 ── */
  .divider {
    border: none;
    border-top: 1px dashed #c8bfb0;
    margin: 0 48px 48px;
  }

  /* ── 完成區 ── */
  .complete-area { text-align: center; padding: 48px 48px 68px; }
  .btn-complete {
    background: #8b6914;
    color: #f5f0e8;
    border: none;
    padding: 20px 64px;
    font-size: 22px;
    border-radius: 40px;
    cursor: pointer;
    letter-spacing: 2px;
    font-family: inherit;
    transition: background 0.2s;
  }
  .btn-complete:hover { background: #a07820; }
  .btn-complete.completed { background: #4a7c59; }
  .btn-export {
    display: block;
    margin: 16px auto 0;
    background: transparent;
    border: 1px solid #c9963a;
    color: #8b6914;
    padding: 12px 32px;
    font-size: 17px;
    border-radius: 26px;
    cursor: pointer;
    letter-spacing: 1px;
    font-family: inherit;
    transition: background 0.2s;
  }
  .btn-export:hover { background: #f5edcc; }
  .save-note { font-size: 15px; color: #b0a090; margin-top: 14px; }

  /* ── 儲存提示 toast ── */
  #toast {
    position: fixed;
    bottom: 32px;
    right: 32px;
    background: #4a7c59;
    color: #fff;
    padding: 12px 22px;
    border-radius: 8px;
    font-size: 15px;
    opacity: 0;
    transition: opacity 0.4s;
    pointer-events: none;
    z-index: 999;
  }
  #toast.show { opacity: 1; }
</style>
</head>
<body>

<!-- 頂部導覽 -->
<nav class="top-bar">
  <div class="date" id="today-date"></div>
  <div class="site-title">一日人生重啟</div>
  <div class="progress-dots">
    <div class="dot" id="dot-morning" title="早晨"></div>
    <div class="dot" id="dot-daytime" title="白天"></div>
    <div class="dot" id="dot-evening" title="晚上"></div>
  </div>
</nav>

<!-- Hero -->
<header class="hero">
  <div class="badge">一日重啟 · 開始</div>
  <h1>對抗真相<br>誠實面對逃避的事物</h1>
  <p class="subtitle">從早晨到晚上，三個階段帶你完成一次完整的人生重啟</p>
</header>

<!-- 早晨區塊 -->
<section class="section" id="section-morning">
  <div class="section-header">
    <div class="section-icon">一</div>
    <div>
      <div class="section-title">早晨 · 心理挖掘</div>
      <div class="section-sub">對抗真相，誠實面對逃避的事物 · 需時約 30 分鐘</div>
    </div>
  </div>

  <!-- 反向願景 -->
  <div class="two-col">
    <div class="card">
      <div class="q-label">反向願景 · 核心提問</div>
      <div class="question">若人生五年內毫無改變，那天會怎麼過？觀察身邊活成這樣的人，你有何感受？</div>
      <textarea data-section="morning" data-field="reverse_vision" placeholder="誠實地寫下來…"></textarea>
    </div>
    <div class="card">
      <div class="q-label">反向願景 · 情緒觀察</div>
      <div class="question">想像這個畫面，你身體哪個部位感到不舒服？那種感覺是什麼？</div>
      <textarea data-section="morning" data-field="emotion_observation" placeholder="描述那種感受…"></textarea>
    </div>
  </div>

  <!-- 正向願景 -->
  <div class="two-col">
    <div class="card">
      <div class="q-label">正向願景 · 核心提問</div>
      <div class="question">三年後你真正想要的生活場景是什麼？</div>
      <textarea data-section="morning" data-field="positive_vision" placeholder="描述那個畫面…"></textarea>
    </div>
    <div class="card">
      <div class="q-label">正向願景 · 身份認同</div>
      <div class="question">為了讓這一切自然發生，你必須相信自己是誰？</div>
      <textarea data-section="morning" data-field="identity" placeholder="我是一個…"></textarea>
    </div>
  </div>
</section>

<hr class="divider">

<!-- 白天區塊 -->
<section class="section" id="section-daytime">
  <div class="section-header">
    <div class="section-icon">二</div>
    <div>
      <div class="section-title">白天 · 設計與導航</div>
      <div class="section-sub">中斷自動模式，察覺慣性行為</div>
    </div>
  </div>

  <div class="info-card">
    <strong>📱 今日任務：設定隨機提醒</strong>
    在手機設定 3-5 個隨機鬧鐘。鬧鐘響時暫停手邊工作，用下方問題審視自己當下的行為是否在走向目標。
  </div>

  <div class="two-col">
    <div class="card">
      <div class="q-label">自我審視</div>
      <div class="question">我現在做的事，是在走向目標，還是保護我不去面對重要事項？</div>
      <textarea data-section="daytime" data-field="self_audit" placeholder="當下的觀察…"></textarea>
    </div>
    <div class="card">
      <div class="q-label">攝影機隱喻</div>
      <div class="question">假設過去兩小時有人全程拍攝你，他會認為你在追求什麼樣的人生？</div>
      <textarea data-section="daytime" data-field="camera_metaphor" placeholder="誠實描述…"></textarea>
    </div>
  </div>
</section>

<hr class="divider">

<!-- 晚上區塊 -->
<section class="section" id="section-evening">
  <div class="section-header">
    <div class="section-icon">三</div>
    <div>
      <div class="section-title">晚上 · 整合與結構化</div>
      <div class="section-sub">界定執行邊界，轉化為具體行動</div>
    </div>
  </div>

  <div class="two-col">
    <div class="card">
      <div class="q-label">底線宣告</div>
      <div class="question">絕對不允許自己繼續活成的樣子：</div>
      <textarea data-section="evening" data-field="baseline" placeholder="我絕對不要…"></textarea>
    </div>
    <div class="card">
      <div class="q-label">方向聲明</div>
      <div class="question">正在打造的人生方向：</div>
      <textarea data-section="evening" data-field="direction" placeholder="我正在成為…"></textarea>
    </div>
  </div>

  <div class="goals-label">三級目標拆解</div>
  <div class="goals-grid">
    <div class="goal-item">
      <div class="timeframe">一年後</div>
      <input type="text" data-section="evening" data-field="goal_1year" placeholder="具體打破舊模式的成果…">
    </div>
    <div class="goal-item">
      <div class="timeframe">一個月內</div>
      <input type="text" data-section="evening" data-field="goal_1month" placeholder="必須完成的關鍵行動…">
    </div>
    <div class="goal-item">
      <div class="timeframe">明天</div>
      <input type="text" data-section="evening" data-field="goal_tomorrow" placeholder="2-3 件那個理想身份會做的事…">
    </div>
  </div>
</section>

<!-- 完成區 -->
<div class="complete-area">
  <button class="btn-complete" id="btn-complete">完成今日重啟</button>
  <button class="btn-export" id="btn-export">匯出紀錄</button>
  <div class="save-note">紀錄自動儲存於此裝置</div>
</div>

<!-- Toast 提示 -->
<div id="toast">已儲存</div>

<script>
// ── 請在 Task 2 加入 JS ──
</script>
</body>
</html>
```

- [ ] **Step 3: 在瀏覽器開啟確認版面**

```bash
open /Users/steven/CCProject/dan-koe-restart/index.html
```

預期：看到完整頁面架構，三個區塊可見，樣式正確（暖棕色調、無白底）。JS 尚未實作，輸入內容不會儲存。

- [ ] **Step 4: Commit**

```bash
cd /Users/steven/CCProject
git add dan-koe-restart/index.html
git commit -m "feat: add dan-koe-restart HTML structure and CSS"
```

---

## Task 2: 加入 localStorage 自動儲存、載入、完成與匯出 JS

**Files:**
- Modify: `dan-koe-restart/index.html`（替換 `<script>` 區塊內容）

- [ ] **Step 1: 將 `<script>` 區塊替換為完整 JS**

找到 `index.html` 中的：
```html
<script>
// ── 請在 Task 2 加入 JS ──
</script>
```

替換為：

```html
<script>
const STORAGE_KEY = 'dan-koe-restart';

// ── 今日日期 ──
function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

// ── 讀取所有紀錄 ──
function loadAllRecords() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : { records: [] };
  } catch { return { records: [] }; }
}

// ── 取得或建立今日紀錄 ──
function getTodayRecord(data) {
  const today = todayStr();
  let rec = data.records.find(r => r.date === today);
  if (!rec) {
    rec = {
      date: today,
      morning: { reverse_vision:'', emotion_observation:'', positive_vision:'', identity:'' },
      daytime: { self_audit:'', camera_metaphor:'' },
      evening: { baseline:'', direction:'', goal_1year:'', goal_1month:'', goal_tomorrow:'' },
      completed: false
    };
    data.records.push(rec);
  }
  return rec;
}

// ── 儲存 ──
function saveRecord(rec) {
  const data = loadAllRecords();
  const today = todayStr();
  const idx = data.records.findIndex(r => r.date === today);
  if (idx >= 0) data.records[idx] = rec;
  else data.records.push(rec);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

// ── Toast 提示 ──
function showToast(msg = '已儲存') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}

// ── 進度點更新 ──
const DOT_FIELDS = {
  morning: ['reverse_vision','emotion_observation','positive_vision','identity'],
  daytime: ['self_audit','camera_metaphor'],
  evening: ['baseline','direction','goal_1year','goal_1month','goal_tomorrow']
};

function updateDots(rec) {
  ['morning','daytime','evening'].forEach(section => {
    const dot = document.getElementById(`dot-${section}`);
    const fields = DOT_FIELDS[section];
    const filled = fields.filter(f => rec[section][f] && rec[section][f].trim() !== '');
    dot.classList.remove('partial','done');
    if (filled.length === fields.length) dot.classList.add('done');
    else if (filled.length > 0) dot.classList.add('partial');
  });
}

// ── 主程式 ──
document.addEventListener('DOMContentLoaded', () => {
  // 顯示今日日期
  const d = new Date();
  document.getElementById('today-date').textContent =
    `${d.getFullYear()}年${d.getMonth()+1}月${d.getDate()}日`;

  // 載入今日紀錄
  const data = loadAllRecords();
  const rec = getTodayRecord(data);
  saveRecord(rec); // 確保新紀錄寫入

  // 填入已有內容
  document.querySelectorAll('[data-section][data-field]').forEach(el => {
    const section = el.dataset.section;
    const field   = el.dataset.field;
    el.value = rec[section][field] || '';
  });

  // 完成按鈕狀態
  const btnComplete = document.getElementById('btn-complete');
  if (rec.completed) {
    btnComplete.textContent = '✓ 已完成今日重啟';
    btnComplete.classList.add('completed');
  }

  updateDots(rec);

  // ── Debounce 自動儲存 ──
  let saveTimer = null;
  document.querySelectorAll('[data-section][data-field]').forEach(el => {
    el.addEventListener('input', () => {
      const section = el.dataset.section;
      const field   = el.dataset.field;
      rec[section][field] = el.value;
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => {
        saveRecord(rec);
        updateDots(rec);
        showToast('已儲存');
      }, 1000);
    });
  });

  // ── 完成按鈕 ──
  btnComplete.addEventListener('click', () => {
    rec.completed = true;
    saveRecord(rec);
    updateDots(rec);
    btnComplete.textContent = '✓ 已完成今日重啟';
    btnComplete.classList.add('completed');
    showToast('🎉 今日重啟完成！');
  });

  // ── 匯出 JSON ──
  document.getElementById('btn-export').addEventListener('click', () => {
    const json = JSON.stringify(rec, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `dan-koe-restart-${rec.date}.json`;
    a.click();
    URL.revokeObjectURL(url);
  });
});
</script>
```

- [ ] **Step 2: 在瀏覽器驗證自動儲存**

```bash
open /Users/steven/CCProject/dan-koe-restart/index.html
```

驗證步驟：
1. 在「反向願景」輸入框輸入文字，等 1 秒，右下角出現「已儲存」toast
2. 重新整理頁面，剛才輸入的文字應仍在
3. 進度點應從灰色變為橘金（部分填寫）

- [ ] **Step 3: 驗證三欄進度點**

1. 把早晨四個欄位都填滿 → 左邊進度點變深金棕
2. 白天填一半 → 中間進度點變橘金
3. 晚上不填 → 右邊進度點維持灰色

- [ ] **Step 4: 驗證完成按鈕**

點「完成今日重啟」：
- 按鈕變綠色，文字改為「✓ 已完成今日重啟」
- Toast 顯示「🎉 今日重啟完成！」
- 重新整理後按鈕狀態保留

- [ ] **Step 5: 驗證匯出**

點「匯出紀錄」：
- 瀏覽器下載一個 `dan-koe-restart-YYYY-MM-DD.json` 檔案
- 開啟檔案，確認內容包含所有已填欄位

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add dan-koe-restart/index.html
git commit -m "feat: add localStorage auto-save, progress dots, complete and export"
```

---

## 驗收清單（對應 spec 成功標準）

- [ ] 使用者可以填完三個區塊所有題目
- [ ] 關閉瀏覽器再開，當日填寫內容完整保留（localStorage）
- [ ] 可成功匯出 JSON 檔案（`dan-koe-restart-YYYY-MM-DD.json`）
- [ ] 全站無純白底色，字體清晰易讀（目測確認）
- [ ] 直接開啟 `index.html` 即可使用，無需安裝任何套件
