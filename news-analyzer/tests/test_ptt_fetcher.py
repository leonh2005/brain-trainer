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
