import feedparser
import html
import logging
import re
import requests
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SUBREDDITS = ["stocks", "investing", "wallstreetbets"]
MAX_CONTENT = 500
# Reddit 的 .json 公開端點會 403 擋非瀏覽器流量，.rss 搭配瀏覽器 UA 可用（2026-07 實測）
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"}


def _strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


def fetch_reddit(subreddits=None, limit=20):
    """Fetch hot posts via Reddit RSS（免憑證，取代原 praw 401 問題）."""
    if subreddits is None:
        subreddits = SUBREDDITS
    articles = []
    for i, name in enumerate(subreddits):
        if i:
            time.sleep(30)  # Reddit 免登入限流嚴格，5 秒仍會 429（2026-07 實測）
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{name}/hot/.rss",
                headers=HEADERS, timeout=15,
            )
            if resp.status_code == 429:
                # 特定 subreddit 偶爾會被額外限流，多等一下重試一次（2026-08-05 實測 r/investing 常見）
                retry_after = int(resp.headers.get("Retry-After", 60))
                logger.warning(f"Reddit 429 for r/{name}，{retry_after}秒後重試一次")
                time.sleep(retry_after)
                resp = requests.get(
                    f"https://www.reddit.com/r/{name}/hot/.rss",
                    headers=HEADERS, timeout=15,
                )
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            for entry in feed.entries[:limit]:
                published = None
                if getattr(entry, "published_parsed", None):
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).isoformat()
                articles.append({
                    "source": "reddit",
                    "title": entry.title,
                    "url": entry.link,
                    "content": _strip_html(getattr(entry, "summary", ""))[:MAX_CONTENT],
                    "published_at": published,
                })
        except Exception as e:
            logger.warning(f"Reddit fetch failed for r/{name}: {e}")
    logger.info(f"[reddit] fetched {len(articles)} articles")
    return articles
