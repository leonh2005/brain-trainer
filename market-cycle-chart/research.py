# -*- coding: utf-8 -*-
"""杜金龍最新說法查詢：Google News RSS → Groq 擷取成劇本草稿。

設計原則：只產生「草稿」，不直接寫入劇本檔；草稿一律經人工確認後才由
wave_scripts.save_script() 落盤。媒體標題常誇大，數字務必人工把關。

Groq 模型注意：llama-3.3-70b-versatile 已於 2026-09 下架，改用推理模型
openai/gpt-oss-120b——這類模型會先消耗 reasoning token，max_tokens 太小會
回傳空 content，故設 3000 並對空字串守門。
"""

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
DEFAULT_QUERY = "杜金龍 台股 波浪"
MAX_NEWS = 10

_SYS = "你是台股波浪劇本整理助手，只輸出 JSON，不輸出任何其他文字。"

_PROMPT = """以下是近期關於台股分析師杜金龍的新聞標題與摘要：

{news}

請整理成一份波浪劇本草稿，輸出 JSON（不要有其他文字）：
{{
  "label": "版本名稱（含日期與目標點位）",
  "source": "主要來源網址（新聞中有才填，否則空字串）",
  "source_date": "YYYY-MM-DD（最新一則日期）",
  "description": "2-3 句說明劇本重點與關鍵數字",
  "anchors": [{{"wave": "波浪名稱", "date": "YYYY-MM", "price": 數字, "note": "依據"}}],
  "targets": [{{"label": "目標說明", "price": 數字}}],
  "projected": [{{"wave": "波段名", "date": "YYYY-MM", "price": 數字, "note": "依據"}}]
}}

嚴格規則（務必遵守，寧缺勿濫）：
- anchors 只放「加權指數的波浪轉折點」：date 必須是 YYYY-MM（例：2025-04），
  price 必須是加權指數點位（四位數以上，例：17306）。
- 下列數字一律「不可」當成 anchor：個股股價、漲跌點數（如跌1800點）、百分比（如10%）、
  年份（如2028）、檔數（如3檔）、本益比。這些是雜訊。
- 新聞若只是當日盤勢、個股推薦，沒有明確的波浪轉折，anchors 就給空陣列 []。
- 不要為了填欄位而湊數字；不確定的欄位一律給空陣列。
- targets 只放「加權指數的目標點位」（四位數以上）。
"""


class ResearchError(Exception):
    """查詢或擷取失敗。"""


def _groq_key() -> str:
    key = os.environ.get("GROQ_API_KEY")
    if key:
        return key
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("GROQ_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise ResearchError("找不到 GROQ_API_KEY（環境變數或 .env）")


def fetch_news(query: str = DEFAULT_QUERY, limit: int = MAX_NEWS) -> list[dict]:
    """抓 Google News RSS 前 limit 則，回傳標題/連結/日期/純文字摘要。"""
    import feedparser

    url = RSS_URL.format(q=urllib.parse.quote(query))
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", 0) and not feed.entries:
        raise ResearchError(f"RSS 讀取失敗：{url}")
    out = []
    for e in feed.entries[:limit]:
        out.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "published": e.get("published", ""),
            "summary": re.sub(r"<[^>]+>", "", e.get("summary", ""))[:400].strip(),
        })
    return out


def extract_script(news: list[dict]) -> dict:
    """把新聞丟給 Groq，擷取成劇本草稿 dict。"""
    if not news:
        raise ResearchError("沒有可分析的新聞")
    news_text = "\n\n".join(
        f"[{n['published']}] {n['title']}\n{n['summary']}" for n in news
    )
    body = json.dumps({
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYS},
            {"role": "user", "content": _PROMPT.format(news=news_text)},
        ],
        "temperature": 0.2,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        GROQ_URL, data=body,
        headers={
            "Authorization": f"Bearer {_groq_key()}",
            "Content-Type": "application/json",
            # Groq 走 Cloudflare，會擋掉 urllib 預設 UA(回 403 code 1010)
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise ResearchError(f"Groq 呼叫失敗 {e.code}: {e.read()[:200]!r}")
    except Exception as e:  # urllib 各類網路錯誤
        raise ResearchError(f"Groq 連線失敗：{e}")

    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not content or not content.strip():
        raise ResearchError("Groq 回傳空內容（reasoning 可能吃光配額，請提高 max_tokens）")
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise ResearchError(f"Groq 回傳非 JSON：{e}")


def research(query: str = DEFAULT_QUERY) -> dict:
    """查新聞並擷取成草稿，回傳 {news, draft}。"""
    news = fetch_news(query)
    draft = extract_script(news)
    return {"news": news, "draft": draft}
