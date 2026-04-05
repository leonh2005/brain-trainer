import praw
import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

SUBREDDITS = ["stocks", "investing", "wallstreetbets"]
MAX_CONTENT = 500


def build_reddit_client():
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT", "news-analyzer/1.0"),
    )


def fetch_reddit(subreddits=None, limit=20):
    """Fetch hot posts from given subreddits."""
    if subreddits is None:
        subreddits = SUBREDDITS
    try:
        reddit = build_reddit_client()
        articles = []
        for name in subreddits:
            sub = reddit.subreddit(name)
            for post in sub.hot(limit=limit):
                articles.append({
                    "source": "reddit",
                    "title": post.title,
                    "url": post.url,
                    "content": post.selftext[:MAX_CONTENT],
                    "published_at": datetime.fromtimestamp(post.created_utc, tz=timezone.utc).isoformat(),
                })
        logger.info(f"[reddit] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        logger.warning(f"Reddit fetch failed: {e}")
        return []
