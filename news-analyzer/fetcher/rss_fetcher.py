import feedparser
import logging

logger = logging.getLogger(__name__)

RSS_SOURCES = [
    {"url": "https://tw.stock.yahoo.com/rss", "source": "yahoo_tw"},
    {"url": "https://finance.yahoo.com/news/rssindex", "source": "yahoo_us"},
    {"url": "https://money.udn.com/rssfeed/news/1001/USD/NEWS.rss", "source": "udn"},
    {"url": "https://feeds.marketwatch.com/marketwatch/topstories/", "source": "marketwatch"},
    {"url": "https://www.cnbc.com/id/10001147/device/rss/rss.html", "source": "cnbc"},
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
    sources = RSS_SOURCES
    all_articles = []
    for s in sources:
        articles = fetch_rss(s["url"], s["source"])
        all_articles.extend(articles)
        logger.info(f"[{s['source']}] fetched {len(articles)} articles")
    return all_articles
