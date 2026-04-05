# 財經新聞情緒分析管線 — 設計文件

**日期**：2026-04-05  
**狀態**：已審閱

---

## 概述

自動抓取多來源財經新聞，透過 Groq AI 批次進行情緒分析，結果存入 SQLite，並以 Flask 儀表板呈現趨勢圖與歷史新聞列表。每 15 分鐘自動執行一輪，每日可累積 200+ 則新聞。成本：$0（Groq 免費額度）。

---

## 資料來源

| 來源 | 方式 | 內容 |
|------|------|------|
| 鉅亨網 | RSS | 標題 + 摘要 |
| Yahoo Finance（台股） | RSS | 標題 + 摘要 |
| PTT Stock 版 | HTTP 爬蟲 | 標題 + 文章前 500 字 |
| Reddit（r/stocks, r/investing, r/wallstreetbets） | PRAW 官方 API（免費） | 標題 + 文章前 500 字 |
| X（Twitter）財經帳號 | rsshub RSS（指定帳號） | 標題 + 推文內容 |

---

## 架構

```
CCProject/news-analyzer/
├── fetcher/
│   ├── rss_fetcher.py       # 鉅亨網、Yahoo、rsshub(X)
│   ├── ptt_fetcher.py       # PTT Stock 版爬蟲
│   └── reddit_fetcher.py    # Reddit via PRAW
├── analyzer.py              # Groq 批次情緒分析
├── storage.py               # SQLite 讀寫封裝
├── pipeline.py              # 主程序（crontab 每 15 分鐘呼叫）
├── app.py                   # Flask 儀表板（port 5300）
├── templates/
│   └── index.html           # Chart.js 趨勢圖 + 新聞列表
├── news.db                  # SQLite 資料庫
├── .env                     # GROQ_API_KEY, REDDIT_CLIENT_ID 等
└── requirements.txt
```

---

## 資料流

```
crontab（每 15 分鐘）
  → pipeline.py
      → fetcher/*.py 各自抓取新聞
      → storage.py 過濾已存過的 URL（去重）
      → analyzer.py 批次送 Groq 分析
      → storage.py 將結果寫入 SQLite
```

---

## 資料庫 Schema

```sql
CREATE TABLE articles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT NOT NULL,        -- 'cnyes'|'yahoo'|'ptt'|'reddit'|'x'
    title        TEXT NOT NULL,
    url          TEXT UNIQUE NOT NULL,
    content      TEXT,                 -- 內文前幾段
    published_at DATETIME,
    fetched_at   DATETIME NOT NULL,
    score        INTEGER,              -- 1(極度看空) ~ 10(極度看多)
    summary      TEXT,                 -- AI 一句話中文摘要
    tags         TEXT,                 -- JSON array，如 ["台積電","Fed","升息"]
    analyzed_at  DATETIME
);
```

---

## Groq 分析模組

- 模型：`llama3-8b-8192`（免費額度最大）
- 批次大小：每次最多 20 則（避免超過 rate limit）
- Prompt 結構：送入標題 + 內文，要求回傳 JSON：
  ```json
  {
    "score": 7,
    "summary": "台積電法說會釋出正面訊號，Q2 營收指引優於預期",
    "tags": ["台積電", "法說會", "Q2"]
  }
  ```
- 失敗處理：Groq 呼叫失敗時，該則新聞的 score/summary/tags 留 NULL，下次 pipeline 重新分析

---

## Flask 儀表板（port 5300）

### 上方：情緒趨勢圖
- 使用 Chart.js 折線圖
- X 軸：時間（可切換「今日 / 本週 / 本月」）
- Y 軸：平均情緒分數 1–10
- 每條線代表一個來源（鉅亨、Yahoo、PTT、Reddit、X）

### 下方：新聞列表
- 每則顯示：來源標籤、標題、AI 摘要、關鍵詞標籤、分數色碼
  - 分數 ≥ 7：綠色（看多）
  - 分數 ≤ 4：紅色（看空）
  - 分數 5–6：灰色（中性）
- 篩選器：來源、日期範圍（日期選擇器）、分數區間
- 搜尋欄：關鍵字搜尋標題與摘要

### 歷史查詢
- 日期選擇器直接切換任意一天
- 資料永久保留於 SQLite，不自動刪除

---

## 排程

```cron
*/15 * * * * /usr/bin/python3 /Users/steven/CCProject/news-analyzer/pipeline.py >> /Users/steven/CCProject/news-analyzer/pipeline.log 2>&1
```

Flask 儀表板以 `launchd` LaunchAgent 常駐（與現有 claude_cycle_monitor 相同模式）。

---

## 環境變數（.env）

```
GROQ_API_KEY=...
REDDIT_CLIENT_ID=...
REDDIT_CLIENT_SECRET=...
REDDIT_USER_AGENT=news-analyzer/1.0
RSSHUB_BASE_URL=https://rsshub.app  # 公開實例，或自架 rsshub（Docker）
X_ACCOUNTS=elonmusk,unusual_whales  # 逗號分隔，想追蹤的 X 財經帳號
```

---

## 錯誤處理

| 情境 | 處理方式 |
|------|----------|
| RSS 來源無回應 | 跳過，記錄 log，下輪重試 |
| PTT 爬蟲被擋 | 加 User-Agent header，退避 retry |
| Groq rate limit | 批次間 sleep 1s，失敗留 NULL 待下輪補分析 |
| Reddit API 失敗 | 跳過，記錄 log |
| SQLite 鎖定 | WAL 模式避免 pipeline 與 Flask 衝突 |

---

## 不在本次範圍

- Telegram 推播（未來可加）
- twscrape 爬 X（未來視需求替換 rsshub）
- 個股追蹤（未來可加關鍵字過濾）
- 部署至 Oracle VM（目前僅本機 Mac）
