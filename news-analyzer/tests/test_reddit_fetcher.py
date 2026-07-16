from unittest.mock import patch, MagicMock
from fetcher.reddit_fetcher import fetch_reddit

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>{title}</title>
    <link href="https://www.reddit.com/r/stocks/comments/abc/post/"/>
    <published>2026-07-16T09:30:00+00:00</published>
    <content type="html">{content}</content>
  </entry>
</feed>"""


def _mock_response(title="市場分析", content="post body content here"):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = RSS_TEMPLATE.format(title=title, content=content)
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_reddit_returns_articles():
    with patch("fetcher.reddit_fetcher.requests.get", return_value=_mock_response()):
        result = fetch_reddit(["stocks"], limit=1)

    assert len(result) == 1
    assert result[0]["source"] == "reddit"
    assert result[0]["title"] == "市場分析"
    assert result[0]["published_at"].startswith("2026-07-16")


def test_fetch_reddit_truncates_content():
    with patch("fetcher.reddit_fetcher.requests.get",
               return_value=_mock_response(title="T", content="x" * 1000)):
        result = fetch_reddit(["stocks"], limit=1)

    assert len(result[0]["content"]) <= 500


def test_fetch_reddit_handles_exception():
    with patch("fetcher.reddit_fetcher.requests.get", side_effect=Exception("blocked")):
        result = fetch_reddit(["stocks"])
    assert result == []
