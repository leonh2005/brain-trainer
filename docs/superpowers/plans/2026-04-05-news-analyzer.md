# 財經新聞情緒分析管線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立自動抓取多來源財經新聞、Groq 情緒分析、SQLite 儲存、Flask 儀表板的完整管線，每 15 分鐘執行一輪。

**Architecture:** 模組化管線 — 各 fetcher 各司其職，pipeline.py 協調，storage.py 封裝資料庫，Flask app.py 提供儀表板與 API。crontab 驅動 pipeline，LaunchAgent 常駐 Flask。

**Tech Stack:** Python 3.13, feedparser, requests, beautifulsoup4, praw, groq, flask, python-dotenv, pytest, SQLite3

---

## 檔案結構

```
CCProject/news-analyzer/
├── fetcher/
│   ├── __init__.py
│   ├── rss_fetcher.py
│   ├── ptt_fetcher.py
│   └── reddit_fetcher.py
├── tests/
│   ├── __init__.py
│   ├── test_storage.py
│   ├── test_rss_fetcher.py
│   ├── test_ptt_fetcher.py
│   ├── test_reddit_fetcher.py
│   ├── test_analyzer.py
│   └── test_app.py
├── templates/
│   └── index.html
├── analyzer.py
├── storage.py
├── pipeline.py
├── app.py
├── .env
├── .env.example
├── requirements.txt
└── com.steven.news-analyzer.plist
```

---

## Task 1: 專案初始化

**Files:**
- Create: `news-analyzer/` (directory)
- Create: `news-analyzer/requirements.txt`
- Create: `news-analyzer/.env.example`
- Create: `news-analyzer/fetcher/__init__.py`
- Create: `news-analyzer/tests/__init__.py`
- Create: `news-analyzer/templates/` (directory)

- [ ] **Step 1: 建立目錄結構**

```bash
cd /Users/steven/CCProject
mkdir -p news-analyzer/fetcher news-analyzer/tests news-analyzer/templates
touch news-analyzer/fetcher/__init__.py
touch news-analyzer/tests/__init__.py
```

- [ ] **Step 2: 建立 requirements.txt**

```
feedparser==6.0.11
requests==2.31.0
beautifulsoup4==4.12.3
praw==7.7.1
groq==0.9.0
flask==3.0.3
python-dotenv==1.0.1
pytest==8.2.0
```

存至 `news-analyzer/requirements.txt`

- [ ] **Step 3: 建立 venv 並安裝套件**

```bash
cd /Users/steven/CCProject/news-analyzer
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

Expected: 所有套件安裝完成，無 error

- [ ] **Step 4: 建立 .env.example**

```
GROQ_API_KEY=your_groq_api_key_here
REDDIT_CLIENT_ID=your_reddit_client_id
REDDIT_CLIENT_SECRET=your_reddit_client_secret
REDDIT_USER_AGENT=news-analyzer/1.0
RSSHUB_BASE_URL=https://rsshub.app
X_ACCOUNTS=unusual_whales,zerohedge
```

存至 `news-analyzer/.env.example`

- [ ] **Step 5: 從 .env.example 建立 .env（填入真實 key）**

```bash
cp news-analyzer/.env.example news-analyzer/.env
# 用編輯器填入真實的 API keys
```

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add news-analyzer/
git commit -m "feat: 初始化 news-analyzer 專案結構"
```

---

## Task 2: Storage 模組

**Files:**
- Create: `news-analyzer/storage.py`
- Create: `news-analyzer/tests/test_storage.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_storage.py`：

```python
import os
import tempfile
import json
import pytest
from storage import init_db, save_articles, get_unanalyzed, update_analysis, get_articles, get_trend_data


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


def test_init_db_creates_table(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert ("articles",) in tables
    conn.close()


def test_save_articles_inserts_rows(db_path):
    articles = [
        {"source": "ptt", "title": "測試標題", "url": "https://ptt.cc/1", "content": "內容", "published_at": None},
    ]
    save_articles(articles, db_path)
    result = get_unanalyzed(db_path)
    assert len(result) == 1
    assert result[0]["title"] == "測試標題"


def test_save_articles_deduplicates(db_path):
    articles = [
        {"source": "ptt", "title": "重複", "url": "https://ptt.cc/dup", "content": "", "published_at": None},
    ]
    save_articles(articles, db_path)
    save_articles(articles, db_path)
    result = get_unanalyzed(db_path)
    assert len(result) == 1


def test_update_analysis(db_path):
    articles = [
        {"source": "ptt", "title": "A", "url": "https://ptt.cc/2", "content": "c", "published_at": None},
    ]
    save_articles(articles, db_path)
    unanalyzed = get_unanalyzed(db_path)
    article_id = unanalyzed[0]["id"]
    update_analysis(article_id, 7, "測試摘要", ["台積電", "漲"], db_path)
    result = get_articles(db_path=db_path)
    article = result["articles"][0]
    assert article["score"] == 7
    assert article["summary"] == "測試摘要"
    assert json.loads(article["tags"]) == ["台積電", "漲"]


def test_get_articles_filter_by_source(db_path):
    save_articles([
        {"source": "ptt", "title": "PTT新聞", "url": "https://ptt.cc/3", "content": "", "published_at": None},
        {"source": "cnyes", "title": "鉅亨新聞", "url": "https://cnyes.com/1", "content": "", "published_at": None},
    ], db_path)
    result = get_articles(source="ptt", db_path=db_path)
    assert result["total"] == 1
    assert result["articles"][0]["source"] == "ptt"


def test_get_articles_search_query(db_path):
    save_articles([
        {"source": "ptt", "title": "台積電大漲", "url": "https://ptt.cc/4", "content": "", "published_at": None},
        {"source": "ptt", "title": "聯發科下跌", "url": "https://ptt.cc/5", "content": "", "published_at": None},
    ], db_path)
    result = get_articles(query="台積電", db_path=db_path)
    assert result["total"] == 1
    assert "台積電" in result["articles"][0]["title"]
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd /Users/steven/CCProject/news-analyzer
venv/bin/pytest tests/test_storage.py -v
```

Expected: FAIL（`ModuleNotFoundError: No module named 'storage'`）

- [ ] **Step 3: 實作 storage.py**

存至 `news-analyzer/storage.py`：

```python
import sqlite3
import json
from datetime import datetime

DB_PATH = "news.db"


def get_conn(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path=DB_PATH):
    with get_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                source       TEXT NOT NULL,
                title        TEXT NOT NULL,
                url          TEXT UNIQUE NOT NULL,
                content      TEXT,
                published_at DATETIME,
                fetched_at   DATETIME NOT NULL,
                score        INTEGER,
                summary      TEXT,
                tags         TEXT,
                analyzed_at  DATETIME
            )
        """)
        conn.commit()


def save_articles(articles, db_path=DB_PATH):
    """Insert articles, ignore duplicates by URL."""
    now = datetime.utcnow().isoformat()
    with get_conn(db_path) as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO articles
               (source, title, url, content, published_at, fetched_at)
               VALUES (:source, :title, :url, :content, :published_at, :fetched_at)""",
            [
                {
                    "source": a["source"],
                    "title": a["title"],
                    "url": a["url"],
                    "content": a.get("content"),
                    "published_at": a.get("published_at"),
                    "fetched_at": now,
                }
                for a in articles
            ],
        )
        conn.commit()


def get_unanalyzed(db_path=DB_PATH):
    """Return up to 100 articles with no score yet."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, source, title, content FROM articles WHERE score IS NULL ORDER BY fetched_at DESC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]


def update_analysis(article_id, score, summary, tags, db_path=DB_PATH):
    now = datetime.utcnow().isoformat()
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE articles SET score=?, summary=?, tags=?, analyzed_at=? WHERE id=?",
            (score, summary, json.dumps(tags, ensure_ascii=False), now, article_id),
        )
        conn.commit()


def get_articles(source=None, date=None, score_min=None, score_max=None, query=None, page=1, db_path=DB_PATH):
    """Return paginated articles for dashboard."""
    conditions = []
    params = []
    if source and source != "all":
        conditions.append("source = ?")
        params.append(source)
    if date:
        conditions.append("DATE(fetched_at) = ?")
        params.append(date)
    if score_min is not None:
        conditions.append("score >= ?")
        params.append(score_min)
    if score_max is not None:
        conditions.append("score <= ?")
        params.append(score_max)
    if query:
        conditions.append("(title LIKE ? OR summary LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    limit = 20
    offset = (page - 1) * limit

    with get_conn(db_path) as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM articles {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM articles {where} ORDER BY fetched_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "articles": [dict(r) for r in rows]}


def get_trend_data(period="day", db_path=DB_PATH):
    """Return average scores per time bucket per source, for Chart.js."""
    if period == "day":
        time_bucket = "strftime('%H:00', fetched_at)"
        condition = "DATE(fetched_at) = DATE('now')"
    elif period == "week":
        time_bucket = "strftime('%m-%d', fetched_at)"
        condition = "fetched_at >= DATE('now', '-7 days')"
    else:  # month
        time_bucket = "strftime('%m-%d', fetched_at)"
        condition = "fetched_at >= DATE('now', '-30 days')"

    with get_conn(db_path) as conn:
        rows = conn.execute(
            f"""SELECT {time_bucket} as t, source, ROUND(AVG(score), 2) as avg_score
                FROM articles
                WHERE score IS NOT NULL AND {condition}
                GROUP BY t, source
                ORDER BY t""",
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: 執行測試確認通過**

```bash
cd /Users/steven/CCProject/news-analyzer
venv/bin/pytest tests/test_storage.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/steven/CCProject
git add news-analyzer/storage.py news-analyzer/tests/test_storage.py
git commit -m "feat: storage 模組（SQLite WAL + CRUD）"
```

---

## Task 3: RSS Fetcher

**Files:**
- Create: `news-analyzer/fetcher/rss_fetcher.py`
- Create: `news-analyzer/tests/test_rss_fetcher.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_rss_fetcher.py`：

```python
from unittest.mock import patch, MagicMock
from fetcher.rss_fetcher import fetch_rss, fetch_all_rss


def _mock_feed(entries):
    feed = MagicMock()
    feed.entries = entries
    return feed


def _make_entry(title, link, summary="摘要", published=None):
    e = MagicMock()
    e.title = title
    e.link = link
    e.summary = summary
    e.get = lambda k, default="": published if k == "published" else default
    return e


def test_fetch_rss_returns_articles():
    entry = _make_entry("台積電大漲", "https://cnyes.com/1", "營收創新高")
    with patch("feedparser.parse", return_value=_mock_feed([entry])):
        result = fetch_rss("https://fake.rss", "cnyes")
    assert len(result) == 1
    assert result[0]["source"] == "cnyes"
    assert result[0]["title"] == "台積電大漲"
    assert result[0]["url"] == "https://cnyes.com/1"
    assert result[0]["content"] == "營收創新高"


def test_fetch_rss_empty_feed():
    with patch("feedparser.parse", return_value=_mock_feed([])):
        result = fetch_rss("https://fake.rss", "yahoo")
    assert result == []


def test_fetch_rss_handles_exception():
    with patch("feedparser.parse", side_effect=Exception("network error")):
        result = fetch_rss("https://fake.rss", "cnyes")
    assert result == []


def test_fetch_all_rss_aggregates_sources():
    entry = _make_entry("新聞A", "https://cnyes.com/a")
    with patch("feedparser.parse", return_value=_mock_feed([entry])):
        result = fetch_all_rss()
    assert len(result) > 0
    sources = {a["source"] for a in result}
    assert "cnyes" in sources or "yahoo" in sources or "x" in sources
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd /Users/steven/CCProject/news-analyzer
venv/bin/pytest tests/test_rss_fetcher.py -v
```

Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 實作 fetcher/rss_fetcher.py**

```python
import feedparser
import logging
import os
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

RSS_SOURCES = [
    {"url": "https://news.cnyes.com/rss/cat/tw_stock", "source": "cnyes"},
    {"url": "https://finance.yahoo.com/news/rssindex", "source": "yahoo"},
]


def _build_x_rss_sources():
    base = os.getenv("RSSHUB_BASE_URL", "https://rsshub.app")
    accounts = os.getenv("X_ACCOUNTS", "").split(",")
    return [
        {"url": f"{base}/twitter/user/{acct.strip()}", "source": "x"}
        for acct in accounts if acct.strip()
    ]


def fetch_rss(url, source_name):
    """Fetch one RSS feed and return list of article dicts."""
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries:
            articles.append({
                "source": source_name,
                "title": entry.title,
                "url": entry.link,
                "content": entry.summary if hasattr(entry, "summary") else "",
                "published_at": entry.get("published", None),
            })
        return articles
    except Exception as e:
        logger.warning(f"RSS fetch failed for {url}: {e}")
        return []


def fetch_all_rss():
    """Fetch all configured RSS sources."""
    sources = RSS_SOURCES + _build_x_rss_sources()
    all_articles = []
    for s in sources:
        articles = fetch_rss(s["url"], s["source"])
        all_articles.extend(articles)
        logger.info(f"[{s['source']}] fetched {len(articles)} articles")
    return all_articles
```

- [ ] **Step 4: 執行測試確認通過**

```bash
venv/bin/pytest tests/test_rss_fetcher.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add news-analyzer/fetcher/rss_fetcher.py news-analyzer/tests/test_rss_fetcher.py
git commit -m "feat: RSS fetcher（鉅亨/Yahoo/X rsshub）"
```

---

## Task 4: PTT Fetcher

**Files:**
- Create: `news-analyzer/fetcher/ptt_fetcher.py`
- Create: `news-analyzer/tests/test_ptt_fetcher.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_ptt_fetcher.py`：

```python
from unittest.mock import patch, MagicMock
from fetcher.ptt_fetcher import parse_index_page, parse_article_page, fetch_ptt

INDEX_HTML = """
<html><body>
<div class="r-ent">
  <div class="title"><a href="/bbs/Stock/M.111.html">台積電分析</a></div>
</div>
<div class="r-ent">
  <div class="title"><span class="delete">(已刪除)</span></div>
</div>
</body></html>
"""

ARTICLE_HTML = """
<html><body>
<div id="main-content">
這是文章正文內容，包含很多財經資訊。市場分析如下...
<div class="push">推文區塊</div>
</div>
</body></html>
"""


def test_parse_index_page_extracts_links():
    items = parse_index_page(INDEX_HTML)
    assert len(items) == 1
    assert items[0]["url"] == "https://www.ptt.cc/bbs/Stock/M.111.html"
    assert items[0]["title"] == "台積電分析"


def test_parse_index_page_skips_deleted():
    items = parse_index_page(INDEX_HTML)
    assert all("已刪除" not in item["title"] for item in items)


def test_parse_article_page_extracts_content():
    title, content = parse_article_page(ARTICLE_HTML)
    assert "財經資訊" in content
    assert "推文區塊" not in content


def test_fetch_ptt_returns_articles():
    mock_resp_index = MagicMock()
    mock_resp_index.text = INDEX_HTML
    mock_resp_index.status_code = 200

    mock_resp_article = MagicMock()
    mock_resp_article.text = ARTICLE_HTML
    mock_resp_article.status_code = 200

    with patch("requests.get", side_effect=[mock_resp_index, mock_resp_article]):
        result = fetch_ptt(max_articles=1)

    assert len(result) == 1
    assert result[0]["source"] == "ptt"
    assert result[0]["title"] == "台積電分析"
    assert result[0]["url"] == "https://www.ptt.cc/bbs/Stock/M.111.html"


def test_fetch_ptt_handles_network_error():
    with patch("requests.get", side_effect=Exception("timeout")):
        result = fetch_ptt(max_articles=5)
    assert result == []
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
venv/bin/pytest tests/test_ptt_fetcher.py -v
```

Expected: FAIL

- [ ] **Step 3: 實作 fetcher/ptt_fetcher.py**

```python
import requests
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PTT_INDEX = "https://www.ptt.cc/bbs/Stock/index.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
COOKIES = {"over18": "1"}
MAX_CONTENT = 500


def parse_index_page(html):
    """Parse PTT index HTML, return list of {"url": ..., "title": ...} dicts."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for r_ent in soup.select("div.r-ent"):
        title_div = r_ent.select_one("div.title a")
        if title_div and title_div.get("href"):
            items.append({
                "url": "https://www.ptt.cc" + title_div["href"],
                "title": title_div.get_text(strip=True),
            })
    return items


def parse_article_page(html):
    """Parse article HTML, return (title, content[:500])."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("div#main-content")
    if not main:
        return "", ""
    # Remove push (推文) sections
    for tag in main.select("div.push"):
        tag.decompose()
    text = main.get_text(separator="\n", strip=True)
    return "", text[:MAX_CONTENT]


def fetch_ptt(max_articles=30):
    """Fetch PTT Stock board articles."""
    try:
        resp = requests.get(PTT_INDEX, headers=HEADERS, cookies=COOKIES, timeout=10)
        urls = parse_index_page(resp.text)
        articles = []
        for item in urls[:max_articles]:
            try:
                r = requests.get(item["url"], headers=HEADERS, cookies=COOKIES, timeout=10)
                _, content = parse_article_page(r.text)
                articles.append({
                    "source": "ptt",
                    "title": item["title"],
                    "url": item["url"],
                    "content": content,
                    "published_at": None,
                })
            except Exception as e:
                logger.warning(f"PTT article fetch failed {url}: {e}")
        logger.info(f"[ptt] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        logger.warning(f"PTT index fetch failed: {e}")
        return []
```

- [ ] **Step 4: 執行測試確認通過**

```bash
venv/bin/pytest tests/test_ptt_fetcher.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add news-analyzer/fetcher/ptt_fetcher.py news-analyzer/tests/test_ptt_fetcher.py
git commit -m "feat: PTT Stock 版爬蟲（含 18+ cookie）"
```

---

## Task 5: Reddit Fetcher

**Files:**
- Create: `news-analyzer/fetcher/reddit_fetcher.py`
- Create: `news-analyzer/tests/test_reddit_fetcher.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_reddit_fetcher.py`：

```python
from unittest.mock import patch, MagicMock
from fetcher.reddit_fetcher import fetch_reddit, build_reddit_client

SUBREDDITS = ["stocks"]


def _mock_submission(title, url, selftext="post body content here"):
    s = MagicMock()
    s.title = title
    s.url = url
    s.selftext = selftext
    s.created_utc = 1712300000.0
    return s


def test_fetch_reddit_returns_articles():
    mock_sub = MagicMock()
    mock_sub.hot.return_value = [_mock_submission("市場分析", "https://reddit.com/r/stocks/1")]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub

    with patch("fetcher.reddit_fetcher.build_reddit_client", return_value=mock_reddit):
        result = fetch_reddit(["stocks"], limit=1)

    assert len(result) == 1
    assert result[0]["source"] == "reddit"
    assert result[0]["title"] == "市場分析"


def test_fetch_reddit_truncates_content():
    long_text = "x" * 1000
    mock_sub = MagicMock()
    mock_sub.hot.return_value = [_mock_submission("T", "https://reddit.com/1", long_text)]

    mock_reddit = MagicMock()
    mock_reddit.subreddit.return_value = mock_sub

    with patch("fetcher.reddit_fetcher.build_reddit_client", return_value=mock_reddit):
        result = fetch_reddit(["stocks"], limit=1)

    assert len(result[0]["content"]) <= 500


def test_fetch_reddit_handles_exception():
    with patch("fetcher.reddit_fetcher.build_reddit_client", side_effect=Exception("auth failed")):
        result = fetch_reddit(["stocks"])
    assert result == []
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
venv/bin/pytest tests/test_reddit_fetcher.py -v
```

Expected: FAIL

- [ ] **Step 3: 實作 fetcher/reddit_fetcher.py**

```python
import praw
import logging
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUBREDDITS = ["stocks", "investing", "wallstreetbets"]
MAX_CONTENT = 500


def build_reddit_client():
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT", "news-analyzer/1.0"),
    )


def fetch_reddit(subreddits=None, limit=20):
    """Fetch hot posts from given subreddits."""
    if subreddits is None:
        subreddits = SUBREDDITS
    try:
        reddit = build_reddit_client()
        articles = []
        for name in subreddits:
            sub = reddit.subreddit(name)
            for post in sub.hot(limit=limit):
                articles.append({
                    "source": "reddit",
                    "title": post.title,
                    "url": post.url,
                    "content": post.selftext[:MAX_CONTENT],
                    "published_at": datetime.utcfromtimestamp(post.created_utc).isoformat(),
                })
        logger.info(f"[reddit] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        logger.warning(f"Reddit fetch failed: {e}")
        return []
```

- [ ] **Step 4: 執行測試確認通過**

```bash
venv/bin/pytest tests/test_reddit_fetcher.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add news-analyzer/fetcher/reddit_fetcher.py news-analyzer/tests/test_reddit_fetcher.py
git commit -m "feat: Reddit fetcher via PRAW（stocks/investing/wsb）"
```

---

## Task 6: Groq 情緒分析模組

**Files:**
- Create: `news-analyzer/analyzer.py`
- Create: `news-analyzer/tests/test_analyzer.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_analyzer.py`：

```python
import json
from unittest.mock import patch, MagicMock
from analyzer import analyze_batch, parse_groq_response

SAMPLE_ARTICLES = [
    {"id": 1, "source": "ptt", "title": "台積電大漲 5%", "content": "外資買超，法說會優於預期"},
    {"id": 2, "source": "cnyes", "title": "聯準會升息恐慌", "content": "市場擔憂通膨持續"},
]

SAMPLE_GROQ_RESPONSE = json.dumps([
    {"id": 1, "score": 8, "summary": "台積電法說優於預期，外資買超", "tags": ["台積電", "外資", "法說會"]},
    {"id": 2, "score": 3, "summary": "Fed 升息恐慌壓制市場", "tags": ["Fed", "升息", "通膨"]},
])


def test_parse_groq_response_valid():
    result = parse_groq_response(SAMPLE_GROQ_RESPONSE, SAMPLE_ARTICLES)
    assert len(result) == 2
    assert result[0]["score"] == 8
    assert result[0]["summary"] == "台積電法說優於預期，外資買超"
    assert result[0]["tags"] == ["台積電", "外資", "法說會"]


def test_parse_groq_response_invalid_json():
    result = parse_groq_response("not json", SAMPLE_ARTICLES)
    assert result == []


def test_analyze_batch_calls_groq():
    mock_message = MagicMock()
    mock_message.content = SAMPLE_GROQ_RESPONSE

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion

    with patch("analyzer.build_groq_client", return_value=mock_client):
        result = analyze_batch(SAMPLE_ARTICLES)

    assert len(result) == 2
    assert result[0]["score"] in range(1, 11)


def test_analyze_batch_handles_groq_failure():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("rate limit")

    with patch("analyzer.build_groq_client", return_value=mock_client):
        result = analyze_batch(SAMPLE_ARTICLES)

    assert result == []
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
venv/bin/pytest tests/test_analyzer.py -v
```

Expected: FAIL

- [ ] **Step 3: 實作 analyzer.py**

```python
import json
import logging
import os
import time
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BATCH_SIZE = 20
GROQ_MODEL = "llama3-8b-8192"

SYSTEM_PROMPT = """你是財經市場情緒分析師。分析新聞標題與內容，判斷對股市的情緒傾向。
回傳純 JSON 陣列（不要有其他文字），格式如下：
[{"id": <原id>, "score": <1-10整數，1=極度看空，10=極度看多>, "summary": "<15字內中文摘要>", "tags": ["標籤1","標籤2","標籤3"]}]"""


def build_groq_client():
    return Groq(api_key=os.getenv("GROQ_API_KEY"))


def parse_groq_response(content, articles):
    """Parse Groq JSON response and merge back with articles."""
    try:
        results = json.loads(content)
        id_map = {a["id"]: a for a in articles}
        enriched = []
        for r in results:
            article = id_map.get(r["id"], {}).copy()
            article["score"] = max(1, min(10, int(r.get("score", 5))))
            article["summary"] = r.get("summary", "")
            article["tags"] = r.get("tags", [])
            enriched.append(article)
        return enriched
    except Exception as e:
        logger.warning(f"Failed to parse Groq response: {e}")
        return []


def analyze_batch(articles):
    """Send articles to Groq for sentiment analysis, return enriched list."""
    if not articles:
        return []
    try:
        client = build_groq_client()
        articles_text = "\n".join(
            f'[id={a["id"]}] 標題：{a["title"]} 內容：{(a.get("content") or "")[:200]}'
            for a in articles
        )
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"分析以下 {len(articles)} 則新聞：\n{articles_text}"},
            ],
            temperature=0.1,
        )
        return parse_groq_response(completion.choices[0].message.content, articles)
    except Exception as e:
        logger.warning(f"Groq analyze_batch failed: {e}")
        return []


def analyze_all(articles):
    """Split into batches of BATCH_SIZE, analyze each, sleep between batches."""
    results = []
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        enriched = analyze_batch(batch)
        results.extend(enriched)
        if i + BATCH_SIZE < len(articles):
            time.sleep(1)
    return results
```

- [ ] **Step 4: 執行測試確認通過**

```bash
venv/bin/pytest tests/test_analyzer.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add news-analyzer/analyzer.py news-analyzer/tests/test_analyzer.py
git commit -m "feat: Groq 情緒分析模組（score/summary/tags）"
```

---

## Task 7: Pipeline 主程序

**Files:**
- Create: `news-analyzer/pipeline.py`
- Create: `news-analyzer/tests/test_pipeline.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_pipeline.py`：

```python
import os
import tempfile
from unittest.mock import patch
from pipeline import run_pipeline
from storage import init_db, get_articles

MOCK_ARTICLES = [
    {"source": "cnyes", "title": "台股大漲", "url": "https://cnyes.com/1", "content": "漲幅驚人", "published_at": None},
]

MOCK_ANALYZED = [
    {"id": 1, "source": "cnyes", "title": "台股大漲", "url": "https://cnyes.com/1",
     "content": "漲幅驚人", "score": 8, "summary": "台股急漲", "tags": ["台股"]},
]


def test_run_pipeline_saves_and_analyzes():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        init_db(db_path)
        with patch("pipeline.fetch_all_rss", return_value=MOCK_ARTICLES), \
             patch("pipeline.fetch_ptt", return_value=[]), \
             patch("pipeline.fetch_reddit", return_value=[]), \
             patch("pipeline.analyze_all", return_value=MOCK_ANALYZED):
            run_pipeline(db_path=db_path)

        result = get_articles(db_path=db_path)
        assert result["total"] == 1
        assert result["articles"][0]["score"] == 8
    finally:
        os.unlink(db_path)


def test_run_pipeline_deduplicates_across_runs():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        init_db(db_path)
        with patch("pipeline.fetch_all_rss", return_value=MOCK_ARTICLES), \
             patch("pipeline.fetch_ptt", return_value=[]), \
             patch("pipeline.fetch_reddit", return_value=[]), \
             patch("pipeline.analyze_all", return_value=MOCK_ANALYZED):
            run_pipeline(db_path=db_path)
            run_pipeline(db_path=db_path)

        result = get_articles(db_path=db_path)
        assert result["total"] == 1
    finally:
        os.unlink(db_path)
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
venv/bin/pytest tests/test_pipeline.py -v
```

Expected: FAIL

- [ ] **Step 3: 實作 pipeline.py**

```python
#!/usr/bin/env python3
"""
Main pipeline: fetch → deduplicate → analyze → store.
Run via crontab every 15 minutes.
"""
import logging
import os
import sys

# Ensure imports work when called directly by crontab
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fetcher.rss_fetcher import fetch_all_rss
from fetcher.ptt_fetcher import fetch_ptt
from fetcher.reddit_fetcher import fetch_reddit
from analyzer import analyze_all
from storage import DB_PATH, init_db, save_articles, get_unanalyzed, update_analysis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline")


def run_pipeline(db_path=DB_PATH):
    logger.info("=== Pipeline start ===")
    init_db(db_path)

    # 1. Fetch from all sources
    rss = fetch_all_rss()
    ptt = fetch_ptt(max_articles=30)
    reddit = fetch_reddit()
    all_articles = rss + ptt + reddit
    logger.info(f"Total fetched: {len(all_articles)}")

    # 2. Save (deduplicates by URL)
    save_articles(all_articles, db_path)

    # 3. Analyze unanalyzed articles
    unanalyzed = get_unanalyzed(db_path)
    logger.info(f"Unanalyzed: {len(unanalyzed)}")
    if unanalyzed:
        enriched = analyze_all(unanalyzed)
        for a in enriched:
            update_analysis(a["id"], a["score"], a["summary"], a["tags"], db_path)
        logger.info(f"Analyzed: {len(enriched)}")

    logger.info("=== Pipeline done ===")


if __name__ == "__main__":
    run_pipeline()
```

- [ ] **Step 4: 執行測試確認通過**

```bash
venv/bin/pytest tests/test_pipeline.py -v
```

Expected: 2 passed

- [ ] **Step 5: 手動跑一次 pipeline 驗證**

```bash
cd /Users/steven/CCProject/news-analyzer
venv/bin/python pipeline.py
```

Expected: log 顯示各來源 fetched 數量，無 crash

- [ ] **Step 6: Commit**

```bash
git add news-analyzer/pipeline.py news-analyzer/tests/test_pipeline.py
git commit -m "feat: pipeline 主程序（fetch → analyze → store）"
```

---

## Task 8: Flask API

**Files:**
- Create: `news-analyzer/app.py`
- Create: `news-analyzer/tests/test_app.py`

- [ ] **Step 1: 撰寫測試**

存至 `news-analyzer/tests/test_app.py`：

```python
import os
import json
import tempfile
import pytest
from storage import init_db, save_articles, update_analysis
import app as flask_app


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    save_articles([
        {"source": "ptt", "title": "台積電分析", "url": "https://ptt.cc/1", "content": "看多", "published_at": None},
        {"source": "cnyes", "title": "Fed恐慌", "url": "https://cnyes.com/1", "content": "看空", "published_at": None},
    ], db)
    # Manually update one with score
    from storage import get_unanalyzed
    arts = get_unanalyzed(db)
    update_analysis(arts[0]["id"], 7, "台積電看多", ["台積電"], db)
    update_analysis(arts[1]["id"], 3, "Fed恐慌看空", ["Fed"], db)

    flask_app.app.config["TESTING"] = True
    flask_app.DB_PATH = db
    with flask_app.app.test_client() as c:
        yield c


def test_index_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_api_articles_returns_json(client):
    resp = client.get("/api/articles")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "total" in data
    assert "articles" in data
    assert data["total"] == 2


def test_api_articles_filter_source(client):
    resp = client.get("/api/articles?source=ptt")
    data = json.loads(resp.data)
    assert data["total"] == 1
    assert data["articles"][0]["source"] == "ptt"


def test_api_articles_search(client):
    resp = client.get("/api/articles?q=台積電")
    data = json.loads(resp.data)
    assert data["total"] == 1


def test_api_trend_returns_json(client):
    resp = client.get("/api/trend?period=day")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "labels" in data
    assert "datasets" in data
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
venv/bin/pytest tests/test_app.py -v
```

Expected: FAIL

- [ ] **Step 3: 實作 app.py**

```python
import json
import os
import sys
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from storage import DB_PATH as _DEFAULT_DB, get_articles, get_trend_data, init_db

app = Flask(__name__)
DB_PATH = _DEFAULT_DB


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/articles")
def api_articles():
    source = request.args.get("source")
    date = request.args.get("date")
    score_min = request.args.get("score_min", type=int)
    score_max = request.args.get("score_max", type=int)
    query = request.args.get("q")
    page = request.args.get("page", 1, type=int)
    result = get_articles(
        source=source, date=date,
        score_min=score_min, score_max=score_max,
        query=query, page=page,
        db_path=DB_PATH,
    )
    # Parse tags JSON for each article
    for a in result["articles"]:
        if a.get("tags"):
            try:
                a["tags"] = json.loads(a["tags"])
            except Exception:
                a["tags"] = []
    return jsonify(result)


@app.route("/api/trend")
def api_trend():
    period = request.args.get("period", "day")
    rows = get_trend_data(period=period, db_path=DB_PATH)

    # Build Chart.js dataset structure
    labels_set = sorted({r["t"] for r in rows})
    sources = sorted({r["source"] for r in rows})
    score_map = {(r["source"], r["t"]): r["avg_score"] for r in rows}

    COLORS = {
        "cnyes": "#3b82f6", "yahoo": "#8b5cf6",
        "ptt": "#f59e0b", "reddit": "#ef4444", "x": "#10b981",
    }

    datasets = []
    for src in sources:
        datasets.append({
            "label": src,
            "data": [score_map.get((src, t)) for t in labels_set],
            "borderColor": COLORS.get(src, "#6b7280"),
            "backgroundColor": COLORS.get(src, "#6b7280") + "33",
            "tension": 0.3,
            "spanGaps": True,
        })

    return jsonify({"labels": labels_set, "datasets": datasets})


if __name__ == "__main__":
    init_db(DB_PATH)
    app.run(host="0.0.0.0", port=5300, debug=False)
```

- [ ] **Step 4: 執行測試確認通過**

```bash
venv/bin/pytest tests/test_app.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add news-analyzer/app.py news-analyzer/tests/test_app.py
git commit -m "feat: Flask API（articles + trend endpoints）"
```

---

## Task 9: 前端儀表板

**Files:**
- Create: `news-analyzer/templates/index.html`

- [ ] **Step 1: 建立 index.html**

存至 `news-analyzer/templates/index.html`：

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>財經新聞情緒儀表板</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  .header { padding: 20px 24px; border-bottom: 1px solid #1e293b; display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 1.25rem; font-weight: 700; }
  .period-btns { margin-left: auto; display: flex; gap: 8px; }
  .period-btns button { background: #1e293b; color: #94a3b8; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }
  .period-btns button.active { background: #3b82f6; color: #fff; }
  .chart-section { padding: 24px; }
  .chart-wrap { background: #1e293b; border-radius: 12px; padding: 20px; height: 300px; }
  .filters { padding: 0 24px 16px; display: flex; flex-wrap: wrap; gap: 10px; }
  .filters input, .filters select { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; padding: 8px 12px; border-radius: 8px; font-size: 0.875rem; }
  .filters input[type="text"] { width: 220px; }
  .news-list { padding: 0 24px 40px; display: flex; flex-direction: column; gap: 10px; }
  .card { background: #1e293b; border-radius: 10px; padding: 14px 18px; display: flex; gap: 14px; align-items: flex-start; }
  .score { font-size: 1.4rem; font-weight: 800; min-width: 36px; text-align: center; padding-top: 2px; }
  .score.bull { color: #22c55e; }
  .score.bear { color: #ef4444; }
  .score.neutral { color: #94a3b8; }
  .card-body { flex: 1; }
  .card-title { font-size: 0.95rem; font-weight: 600; margin-bottom: 4px; }
  .card-title a { color: #e2e8f0; text-decoration: none; }
  .card-title a:hover { color: #3b82f6; }
  .card-summary { font-size: 0.83rem; color: #94a3b8; margin-bottom: 6px; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; }
  .tag { background: #0f172a; color: #64748b; font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; }
  .source-badge { font-size: 0.72rem; background: #334155; color: #94a3b8; padding: 2px 8px; border-radius: 4px; margin-right: 8px; }
  .pagination { padding: 16px 24px; display: flex; gap: 8px; align-items: center; color: #64748b; font-size: 0.875rem; }
  .pagination button { background: #1e293b; color: #94a3b8; border: none; padding: 6px 14px; border-radius: 6px; cursor: pointer; }
  .pagination button:disabled { opacity: 0.4; cursor: default; }
</style>
</head>
<body>

<div class="header">
  <span>📊</span>
  <h1>財經新聞情緒儀表板</h1>
  <div class="period-btns">
    <button class="active" onclick="setPeriod('day', this)">今日</button>
    <button onclick="setPeriod('week', this)">本週</button>
    <button onclick="setPeriod('month', this)">本月</button>
  </div>
</div>

<div class="chart-section">
  <div class="chart-wrap">
    <canvas id="trendChart"></canvas>
  </div>
</div>

<div class="filters">
  <input type="text" id="searchInput" placeholder="搜尋標題、摘要..." oninput="debounceLoad()">
  <select id="sourceSelect" onchange="loadArticles(1)">
    <option value="">所有來源</option>
    <option value="cnyes">鉅亨網</option>
    <option value="yahoo">Yahoo</option>
    <option value="ptt">PTT</option>
    <option value="reddit">Reddit</option>
    <option value="x">X</option>
  </select>
  <input type="date" id="dateInput" onchange="loadArticles(1)">
  <select id="scoreFilter" onchange="loadArticles(1)">
    <option value="">所有情緒</option>
    <option value="7,10">看多（7-10）</option>
    <option value="1,4">看空（1-4）</option>
    <option value="5,6">中性（5-6）</option>
  </select>
</div>

<div class="news-list" id="newsList"></div>

<div class="pagination" id="pagination"></div>

<script>
let chart = null;
let currentPage = 1;
let debounceTimer = null;

function debounceLoad() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => loadArticles(1), 400);
}

async function loadTrend(period = 'day') {
  const res = await fetch(`/api/trend?period=${period}`);
  const data = await res.json();
  const ctx = document.getElementById('trendChart').getContext('2d');
  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: 'line',
    data: { labels: data.labels, datasets: data.datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { min: 1, max: 10, ticks: { color: '#94a3b8' }, grid: { color: '#1e293b' } },
        x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } }
      },
      plugins: { legend: { labels: { color: '#e2e8f0' } } }
    }
  });
}

function setPeriod(period, btn) {
  document.querySelectorAll('.period-btns button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  loadTrend(period);
}

async function loadArticles(page = 1) {
  currentPage = page;
  const q = document.getElementById('searchInput').value;
  const source = document.getElementById('sourceSelect').value;
  const date = document.getElementById('dateInput').value;
  const scoreVal = document.getElementById('scoreFilter').value;
  let url = `/api/articles?page=${page}`;
  if (q) url += `&q=${encodeURIComponent(q)}`;
  if (source) url += `&source=${source}`;
  if (date) url += `&date=${date}`;
  if (scoreVal) {
    const [min, max] = scoreVal.split(',');
    url += `&score_min=${min}&score_max=${max}`;
  }
  const res = await fetch(url);
  const data = await res.json();
  renderArticles(data.articles);
  renderPagination(data.total, page);
}

function scoreClass(s) {
  if (s === null || s === undefined) return 'neutral';
  if (s >= 7) return 'bull';
  if (s <= 4) return 'bear';
  return 'neutral';
}

function renderArticles(articles) {
  const el = document.getElementById('newsList');
  if (!articles.length) { el.innerHTML = '<div style="color:#64748b;padding:20px">沒有符合的新聞</div>'; return; }
  el.innerHTML = articles.map(a => `
    <div class="card">
      <div class="score ${scoreClass(a.score)}">${a.score ?? '–'}</div>
      <div class="card-body">
        <div class="card-title">
          <span class="source-badge">${a.source}</span>
          <a href="${a.url}" target="_blank">${a.title}</a>
        </div>
        ${a.summary ? `<div class="card-summary">${a.summary}</div>` : ''}
        <div class="tags">${(Array.isArray(a.tags) ? a.tags : []).map(t => `<span class="tag">#${t}</span>`).join('')}</div>
      </div>
    </div>
  `).join('');
}

function renderPagination(total, page) {
  const pages = Math.ceil(total / 20);
  const el = document.getElementById('pagination');
  el.innerHTML = `
    <button onclick="loadArticles(${page - 1})" ${page <= 1 ? 'disabled' : ''}>上一頁</button>
    <span>${page} / ${pages || 1}（共 ${total} 則）</span>
    <button onclick="loadArticles(${page + 1})" ${page >= pages ? 'disabled' : ''}>下一頁</button>
  `;
}

// Init
loadTrend('day');
loadArticles(1);
</script>
</body>
</html>
```

- [ ] **Step 2: 手動確認儀表板**

```bash
cd /Users/steven/CCProject/news-analyzer
venv/bin/python app.py
# 瀏覽器開 http://localhost:5300
```

Expected: 儀表板正常顯示，趨勢圖與新聞列表載入（若 DB 空則列表空白是正常）

- [ ] **Step 3: Commit**

```bash
git add news-analyzer/templates/index.html
git commit -m "feat: 財經儀表板前端（Chart.js + 篩選器）"
```

---

## Task 10: 排程部署

**Files:**
- Create: `news-analyzer/com.steven.news-analyzer.plist`

- [ ] **Step 1: 設定 crontab（pipeline 每 15 分鐘）**

```bash
crontab -e
```

加入這行：

```
*/15 * * * * /Users/steven/CCProject/news-analyzer/venv/bin/python /Users/steven/CCProject/news-analyzer/pipeline.py >> /Users/steven/CCProject/news-analyzer/pipeline.log 2>&1
```

驗證：
```bash
crontab -l | grep news-analyzer
```

Expected: 顯示剛加入的那行

- [ ] **Step 2: 建立 LaunchAgent plist（Flask 常駐）**

存至 `news-analyzer/com.steven.news-analyzer.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.steven.news-analyzer</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/steven/CCProject/news-analyzer/venv/bin/python</string>
        <string>/Users/steven/CCProject/news-analyzer/app.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/steven/CCProject/news-analyzer</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/steven/CCProject/news-analyzer/flask.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/steven/CCProject/news-analyzer/flask.log</string>
</dict>
</plist>
```

- [ ] **Step 3: 安裝 LaunchAgent**

```bash
cp /Users/steven/CCProject/news-analyzer/com.steven.news-analyzer.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.steven.news-analyzer.plist
```

驗證：
```bash
launchctl list | grep news-analyzer
curl http://localhost:5300/api/articles
```

Expected: launchctl 顯示 pid，curl 回傳 `{"articles": [], "total": 0}`

- [ ] **Step 4: 手動跑一次 pipeline 驗證端對端**

```bash
/Users/steven/CCProject/news-analyzer/venv/bin/python /Users/steven/CCProject/news-analyzer/pipeline.py
```

然後：
```bash
curl "http://localhost:5300/api/articles" | python3 -m json.tool | head -30
```

Expected: `total` > 0，articles 有 score/summary/tags

- [ ] **Step 5: Commit**

```bash
git add news-analyzer/com.steven.news-analyzer.plist
git commit -m "feat: crontab + LaunchAgent 部署設定"
```

---

## 所有測試最終確認

- [ ] **執行完整測試套件**

```bash
cd /Users/steven/CCProject/news-analyzer
venv/bin/pytest tests/ -v
```

Expected: 全部 pass（storage: 6, rss: 4, ptt: 5, reddit: 3, analyzer: 4, pipeline: 2, app: 5 = 29 tests）
