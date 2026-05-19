#!/usr/bin/env python3
"""
持倉新聞監控 — 每日兩次推播
- 08:30 台股持倉多空判斷
- 21:00 美股持倉多空判斷
"""
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("portfolio_news")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "7556217543")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-49f9f0a651514aff96412fa7ad11ae85")

MAX_FETCH   = 50   # RSS 最多抓幾則
MAX_SEND    = 20   # 去重後送 DeepSeek 上限
SIMILAR_THR = 0.6  # 標題相似度閾值（超過視為重複）

# ── 持倉定義 ──────────────────────────────────────────────────────────────────

TW_HOLDINGS = [
    {
        "code": "2327",
        "name": "國巨",
        "queries": [
            "國巨 MLCC",
            "Yageo passive components",
        ],
    },
    {
        "code": "2408",
        "name": "南亞科",
        "queries": [
            "南亞科 DRAM",
            "Nanya Technology memory",
            "Micron MU DRAM memory chip",
        ],
    },
    {
        "code": "1785",
        "name": "光洋科",
        "queries": [
            "光洋科",
            "Koway precious metal recycling",
        ],
    },
    {
        "code": "2317",
        "name": "鴻海",
        "queries": [
            "鴻海 AI伺服器",
            "Foxconn Hon Hai server",
        ],
    },
    {
        "code": "006208",
        "name": "富邦台50",
        "queries": [
            "台股大盤 半導體",
            "TAIEX Taiwan stock market",
        ],
    },
    {
        "code": "00881",
        "name": "國泰永續高股息",
        "queries": [
            "台股 高股息 ETF",
            "Taiwan dividend ETF",
        ],
    },
]

US_HOLDINGS = [
    {
        "code": "VWRA",
        "name": "先鋒全球股票",
        "queries": [
            "global stock market Fed interest rate",
            "S&P 500 world equities",
        ],
    },
    {
        "code": "GRID",
        "name": "全球電力基建",
        "queries": [
            "power grid infrastructure AI data center electricity",
            "global power utilities investment",
        ],
    },
    {
        "code": "XLU",
        "name": "美國公用事業",
        "queries": [
            "US utilities sector XLU energy regulation",
        ],
    },
    {
        "code": "00864B",
        "name": "中信美債0-1年",
        "queries": [
            "US treasury yield Fed rate cut hike",
            "short term bond market",
        ],
    },
    {
        "code": "DXYZ",
        "name": "Destiny Tech100",
        "queries": [
            "Destiny Tech100 DXYZ SpaceX pre-IPO",
        ],
    },
    {
        "code": "00635U",
        "name": "元大黃金",
        "queries": [
            "gold price XAU safe haven",
            "黃金 避險",
        ],
    },
]

# ── 工具函式 ───────────────────────────────────────────────────────────────────

def fetch_news(queries: list[str], hours: int = 24) -> list[dict]:
    """從 Google News RSS 抓文章，過濾 hours 小時內，最多 MAX_FETCH 則。"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    articles = []
    seen_ids = set()

    for query in queries:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(query)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
        )
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                if pub:
                    pub_dt = datetime(*pub[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                aid = hashlib.md5(e.title.encode()).hexdigest()
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
                articles.append({
                    "title": e.title,
                    "url": e.get("link", ""),
                    "pub": pub_dt if pub else None,
                })
                if len(articles) >= MAX_FETCH:
                    break
        except Exception as ex:
            logger.warning("RSS 抓取失敗：%s — %s", query, ex)

    # 按時間排序（最新優先）
    articles.sort(key=lambda a: a["pub"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return articles


def deduplicate(articles: list[dict]) -> list[dict]:
    """移除相似度 > SIMILAR_THR 的重複標題，保留最新一則。"""
    kept = []
    for a in articles:
        is_dup = False
        for k in kept:
            ratio = SequenceMatcher(None, a["title"], k["title"]).ratio()
            if ratio >= SIMILAR_THR:
                is_dup = True
                break
        if not is_dup:
            kept.append(a)
        if len(kept) >= MAX_SEND:
            break
    return kept


def analyze(holding: dict, articles: list[dict]) -> dict:
    """用 DeepSeek V3 分析一個持倉的多空情緒。"""
    if not articles:
        return {
            "direction": "neutral",
            "score": 5,
            "summary": "過去 24 小時無相關新聞",
            "push": False,
        }

    news_list = "\n".join(f"- {a['title']}" for a in articles)

    prompt = f"""你是專業台灣投資組合分析師。以下是「{holding['name']}（{holding['code']}）」過去 24 小時的相關新聞標題（共 {len(articles)} 則）：

{news_list}

請根據這些新聞對此標的的影響做出多空判斷。

回傳純 JSON，格式：
{{
  "direction": "bullish" | "bearish" | "neutral",
  "score": <影響分數 1-10，10 最強>,
  "summary": "<30字內的關鍵判斷>",
  "key_factor": "<最重要的一則新聞標題，15字內摘要>",
  "push": <true 若 score >= 6，否則 false>
}}"""

    try:
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1",
        )
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
        text = re.sub(r"```[a-z]*\n?", "", resp.choices[0].message.content.strip()).strip("`").strip()
        return json.loads(text)
    except Exception as e:
        logger.error("DeepSeek 分析失敗 %s：%s", holding["code"], e)
        return {
            "direction": "neutral",
            "score": 0,
            "summary": "分析失敗",
            "key_factor": "",
            "push": False,
        }


def direction_emoji(d: str) -> str:
    return {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(d, "➡️")


def overall_direction(results: list[dict]) -> str:
    scores = {"bullish": 0, "bearish": 0, "neutral": 0}
    for r in results:
        d = r.get("direction", "neutral")
        scores[d] = scores.get(d, 0) + r.get("score", 5)
    return max(scores, key=scores.get)


def send_telegram(text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.error("Telegram 推播失敗：%s", e)


# ── 推播邏輯 ───────────────────────────────────────────────────────────────────

def run_session(holdings: list[dict], session_label: str):
    """執行一個時段（台股 or 美股）的分析與推播。"""
    logger.info("開始分析 %s 時段，共 %d 個標的", session_label, len(holdings))

    results = []
    lines = []

    for h in holdings:
        articles = fetch_news(h["queries"])
        deduped  = deduplicate(articles)
        logger.info("%s：抓到 %d 則，去重後 %d 則", h["name"], len(articles), len(deduped))

        result = analyze(h, deduped)
        results.append(result)

        emoji = direction_emoji(result["direction"])
        score = result["score"]
        summary = result["summary"]
        key = result.get("key_factor", "")

        line = f"{emoji} <b>{h['name']}（{h['code']}）</b> {score}/10\n"
        line += f"   {summary}"
        if key:
            line += f"\n   └ {key}"
        lines.append(line)

    overall = overall_direction(results)
    overall_emoji = direction_emoji(overall)
    overall_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}.get(overall, "中性")

    now_str = datetime.now().strftime("%m/%d %H:%M")
    header = f"📊 <b>{session_label}持倉情報</b>（{now_str}）\n\n"
    body   = "\n\n".join(lines)
    footer = f"\n\n{overall_emoji} <b>整體方向：{overall_label}</b>"

    send_telegram(header + body + footer)
    logger.info("%s 時段推播完成", session_label)


def run():
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    hour, minute = now.hour, now.minute

    # 08:25–08:35 → 台股時段
    if 8 <= hour <= 8 and 25 <= minute <= 35:
        run_session(TW_HOLDINGS, "台股開盤前")
    # 21:00–21:10 → 美股時段
    elif hour == 21 and minute <= 10:
        run_session(US_HOLDINGS, "美股盤前")
    else:
        logger.info("非推播時段（%02d:%02d），略過", hour, minute)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == "tw":
            run_session(TW_HOLDINGS, "台股開盤前")
        elif sys.argv[1] == "us":
            run_session(US_HOLDINGS, "美股盤前")
        elif sys.argv[1] == "test":
            # 測試單一標的
            h = TW_HOLDINGS[3]  # 鴻海
            arts = fetch_news(h["queries"])
            deduped = deduplicate(arts)
            print(f"抓到 {len(arts)} 則，去重後 {len(deduped)} 則")
            for a in deduped[:5]:
                print(f"  - {a['title']}")
            result = analyze(h, deduped)
            print("分析結果：", json.dumps(result, ensure_ascii=False, indent=2))
    else:
        run()
