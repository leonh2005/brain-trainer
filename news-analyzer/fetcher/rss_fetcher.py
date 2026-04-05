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
