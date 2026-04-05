import os
import json
import tempfile
import pytest
from storage import init_db, save_articles, update_analysis, get_unanalyzed
import app as flask_app


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    save_articles([
        {"source": "ptt", "title": "台積電分析", "url": "https://ptt.cc/1", "content": "看多", "published_at": None},
        {"source": "cnyes", "title": "Fed恐慌", "url": "https://cnyes.com/1", "content": "看空", "published_at": None},
    ], db)
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
