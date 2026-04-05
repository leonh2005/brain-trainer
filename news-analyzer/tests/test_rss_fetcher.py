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
