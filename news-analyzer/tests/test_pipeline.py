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
