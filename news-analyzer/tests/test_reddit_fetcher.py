from unittest.mock import patch, MagicMock
from fetcher.reddit_fetcher import fetch_reddit, build_reddit_client


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
