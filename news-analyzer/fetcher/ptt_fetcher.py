import requests
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PTT_INDEX = "https://www.ptt.cc/bbs/Stock/index.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
COOKIES = {"over18": "1"}
MAX_CONTENT = 500


def parse_index_page(html):
    """Parse PTT index HTML, return list of {"url": ..., "title": ...} dicts."""
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for r_ent in soup.select("div.r-ent"):
        title_div = r_ent.select_one("div.title a")
        if title_div and title_div.get("href"):
            items.append({
                "url": "https://www.ptt.cc" + title_div["href"],
                "title": title_div.get_text(strip=True),
            })
    return items


def parse_article_page(html):
    """Parse article HTML, return (title, content[:MAX_CONTENT])."""
    soup = BeautifulSoup(html, "html.parser")
    main = soup.select_one("div#main-content")
    if not main:
        return "", ""
    for tag in main.select("div.push"):
        tag.decompose()
    text = main.get_text(separator="\n", strip=True)
    return "", text[:MAX_CONTENT]


def fetch_ptt(max_articles=30):
    """Fetch PTT Stock board articles."""
    try:
        resp = requests.get(PTT_INDEX, headers=HEADERS, cookies=COOKIES, timeout=10)
        items = parse_index_page(resp.text)
        articles = []
        for item in items[:max_articles]:
            try:
                r = requests.get(item["url"], headers=HEADERS, cookies=COOKIES, timeout=10)
                _, content = parse_article_page(r.text)
                articles.append({
                    "source": "ptt",
                    "title": item["title"],
                    "url": item["url"],
                    "content": content,
                    "published_at": None,
                })
            except Exception as e:
                logger.warning(f"PTT article fetch failed {item['url']}: {e}")
        logger.info(f"[ptt] fetched {len(articles)} articles")
        return articles
    except Exception as e:
        logger.warning(f"PTT index fetch failed: {e}")
        return []
