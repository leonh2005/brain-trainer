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
