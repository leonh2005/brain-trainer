#!/usr/bin/env python3
"""
川普發言監測 — 每 5 分鐘掃 Google News RSS
僅在週一至週五 08:30–13:30 推播
週一 08:30 第一次：推播週五收盤後至今的所有新聞總結
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("trump_monitor")

TZ = ZoneInfo("Asia/Taipei")
BOT_TOKEN  = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
CHAT_ID    = "7556217543"
SEEN_FILE  = Path(__file__).parent / "trump_seen.json"
STATE_FILE = Path(__file__).parent / "trump_state.json"

MARKET_START = time(8, 30)
MARKET_END   = time(13, 30)

RSS_FEEDS = [
    "https://news.google.com/rss/search?q=Trump+tariff+OR+trade+deal+OR+China+sanctions&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+tech+OR+semiconductor+OR+AI+chip+export&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Trump+Taiwan+OR+TSMC+OR+Asia+market&hl=en-US&gl=US&ceid=US:en",
]

SINGLE_PROMPT = """你是台股分析師。判斷以下川普相關新聞是否與「AI產業」或「台股大盤走勢」直接相關。

push=true 的條件（必須同時符合）：
1. 與 AI、半導體、晶片、科技股、或台股大盤有直接關聯
2. 影響分數 ≥ 7

push=false 的情況（任一符合即不推）：
- 主題是伊朗、印度、俄羅斯、古巴等與台股無關的議題
- 僅是貿易談判進度，無具體關稅行動
- 選舉、內政、個人新聞

回傳純 JSON，格式：
{
  "score": <1-10整數>,
  "push": <true/false>,
  "impact": "<對台股的影響，20字內>",
  "affected": ["受影響板塊，如半導體、AI、大盤"],
  "direction": "positive" | "negative" | "neutral"
}"""

WEEKLY_PROMPT = """你是台股分析師。以下是週五台股收盤後至週一開盤前，與川普相關的所有新聞標題。

請：
1. 挑出對台股（AI/半導體板塊優先）影響最大的 3-5 則
2. 綜合判斷整體情緒
3. 給出週一開盤參考建議

新聞列表：
{news_list}

回傳純 JSON：
{{
  "overall": "偏多" | "偏空" | "中性",
  "score": <整體影響分1-10>,
  "key_news": [{{"title": "...", "impact": "<15字內>", "direction": "positive|negative|neutral"}}],
  "suggestion": "<30字內開盤參考>"
}}"""


def is_market_hours(now=None):
    if now is None:
        now = datetime.now(TZ)
    if now.weekday() >= 5:
        return False
    return MARKET_START <= now.time() <= MARKET_END


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except Exception:
            pass
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)[-1000:]))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def article_id(title: str, url: str) -> str:
    return hashlib.md5(f"{title}{url}".encode()).hexdigest()


def fetch_articles() -> list[dict]:
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=3)
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        logger.debug(f"跳過舊文章：{e.title[:50]}")
                        continue
                articles.append({
                    "id": article_id(e.title, e.get("link", "")),
                    "title": e.title,
                    "url": e.get("link", ""),
                })
        except Exception as ex:
            logger.warning(f"RSS fetch failed: {url} — {ex}")
    return articles


def gemini_call(messages: list, max_tokens=1000) -> dict:
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    prompt = "\n\n".join(
        m["content"] for m in messages if m.get("role") in ("system", "user")
    )
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=genai.types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=max_tokens,
            thinking_config=genai.types.ThinkingConfig(thinking_budget=0),
        ),
    )
    text = re.sub(r"```(?:json)?\n?", "", resp.text.strip()).strip("`").strip()
    return json.loads(text)


def send_telegram(text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Telegram failed: {e}")


def direction_emoji(d: str) -> str:
    return {"positive": "📈", "negative": "📉", "neutral": "➡️"}.get(d, "❓")


def send_weekly_summary(new_articles: list[dict]):
    if not new_articles:
        send_telegram("🗓 <b>週末川普動態</b>\n\n本週末無新增相關新聞。")
        return

    news_list = "\n".join(f"- {a['title']}" for a in new_articles[:60])
    try:
        result = gemini_call(
            [{"role": "user", "content": WEEKLY_PROMPT.format(news_list=news_list)}],
            max_tokens=2000,
        )
        overall_emoji = {"偏多": "📈", "偏空": "📉", "中性": "➡️"}.get(result["overall"], "❓")
        key_lines = "\n".join(
            f"{direction_emoji(k['direction'])} {k['impact']}\n   └ {k['title'][:55]}"
            for k in result.get("key_news", [])
        )
        msg = (
            f"🗓 <b>週末川普動態週報</b>（{len(new_articles)} 則新聞）\n\n"
            f"{overall_emoji} <b>整體情緒：</b>{result['overall']}　"
            f"📊 <b>影響：</b>{result['score']}/10\n\n"
            f"<b>重點新聞：</b>\n{key_lines}\n\n"
            f"💡 <b>開盤參考：</b>{result['suggestion']}"
        )
        send_telegram(msg)
        logger.info(f"週末總結推播完成，共 {len(new_articles)} 則")
    except Exception as e:
        logger.error(f"週末總結失敗：{e}")


def run():
    now = datetime.now(TZ)

    if not is_market_hours(now):
        logger.info("非交易時段，略過")
        return

    seen   = load_seen()
    state  = load_state()
    today  = now.strftime("%Y-%m-%d")

    is_first_init    = len(seen) == 0
    is_monday_first  = (
        now.weekday() == 0
        and now.time() < time(8, 36)
        and state.get("monday_summary_sent") != today
    )

    articles     = fetch_articles()
    new_articles = [a for a in articles if a["id"] not in seen]
    logger.info(f"抓到 {len(articles)} 篇，其中 {len(new_articles)} 篇未見過")

    # 首次初始化
    if is_first_init:
        for a in articles:
            seen.add(a["id"])
        save_seen(seen)
        logger.info("首次執行：已建立基準線")
        return

    # 週一 08:30 週末總結
    if is_monday_first:
        for a in new_articles:
            seen.add(a["id"])
        save_seen(seen)
        send_weekly_summary(new_articles)
        state["monday_summary_sent"] = today
        save_state(state)
        return

    # 一般執行：個別分析推播
    pushed = 0
    for a in new_articles:
        seen.add(a["id"])
        try:
            result = gemini_call([
                {"role": "system", "content": SINGLE_PROMPT},
                {"role": "user", "content": f"新聞標題：{a['title']}"},
            ])
            logger.info(f"score={result['score']} push={result['push']} | {a['title'][:60]}")
            if result.get("push"):
                emoji    = direction_emoji(result.get("direction", "neutral"))
                affected = "、".join(result.get("affected", []))
                msg = (
                    f"🇺🇸 <b>川普動態 × 台股影響</b>\n\n"
                    f"{emoji} {result['impact']}\n\n"
                    f"📌 <b>標題：</b>{a['title']}\n"
                    f"🏷 <b>影響板塊：</b>{affected or '大盤'}\n"
                    f"📊 <b>影響分數：</b>{result['score']}/10\n"
                    f"🔗 {a['url']}"
                )
                send_telegram(msg)
                pushed += 1
        except Exception as e:
            logger.warning(f"分析失敗：{a['title'][:50]} — {e}")

    save_seen(seen)
    logger.info(f"完成，推播 {pushed} 則")


if __name__ == "__main__":
    run()
