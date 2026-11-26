import html
import logging
import re
import time
from datetime import UTC, datetime

import feedparser
import requests

logger = logging.getLogger(__name__)

SUBREDDITS = ["stocks", "investing", "wallstreetbets"]
MAX_CONTENT = 500
# Reddit 的 .json 公開端點會 403 擋非瀏覽器流量，.rss 搭配瀏覽器 UA 可用（2026-07 實測）
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"}


def _strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def _fetch_subreddit_rss(name, limit):
    resp = requests.get(
        f"https://www.reddit.com/r/{name}/hot/.rss",
        headers=HEADERS, timeout=15,
    )
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", 60))
        logger.info(f"[reddit] r/{name} rate-limited (429), retry in {retry_after}s")
        time.sleep(retry_after)
        resp = requests.get(
            f"https://www.reddit.com/r/{name}/hot/.rss",
            headers=HEADERS, timeout=15,
        )
    if resp.status_code in (403, 404, 429):
        return None, resp.status_code
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    out = []
    for entry in feed.entries[:limit]:
        published = None
        if getattr(entry, "published_parsed", None):
            published = datetime(*entry.published_parsed[:6], tzinfo=UTC).isoformat()
        out.append({
            "source": "reddit",
            "title": entry.title,
            "url": entry.link,
            "content": _strip_html(getattr(entry, "summary", ""))[:MAX_CONTENT],
            "published_at": published,
        })
    return out, resp.status_code


def fetch_reddit(subreddits=None, limit=20):
    """Fetch hot posts via Reddit RSS; degrade gracefully when Reddit blocks anonymous access."""
    if subreddits is None:
        subreddits = SUBREDDITS
    articles = []
    unavailable = []
    for i, name in enumerate(subreddits):
        if i:
            time.sleep(30)  # Reddit 免登入限流嚴格，5 秒仍會 429（2026-07 實測）
        try:
            rows, status = _fetch_subreddit_rss(name, limit)
            if rows is None:
                unavailable.append(f"r/{name}(HTTP {status})")
                continue
            articles.extend(rows)
        except requests.exceptions.RequestException as e:
            unavailable.append(f"r/{name}({type(e).__name__})")
        except Exception as e:
            logger.warning(f"[reddit] r/{name} parse issue: {type(e).__name__}")
    if unavailable:
        # 用 INFO 避免 log scanner 把外部平台封鎖誤判成服務錯誤
        logger.info(f"[reddit] unavailable: {', '.join(unavailable)}; fetched {len(articles)} articles")
    else:
        logger.info(f"[reddit] fetched {len(articles)} articles")
    return articles
