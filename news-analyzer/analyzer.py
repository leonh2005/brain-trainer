import json
import logging
import os
import time
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BATCH_SIZE = 20
GROQ_MODEL = "llama-3.1-8b-instant"

SYSTEM_PROMPT = """你是財經市場情緒分析師。分析新聞標題與內容，判斷對股市的情緒傾向。
回傳純 JSON 陣列（不要有其他文字），格式如下：
[{"id": <原id>, "score": <1-10整數，1=極度看空，10=極度看多>, "summary": "<15字內中文摘要>", "tags": ["標籤1","標籤2","標籤3"]}]"""


def build_groq_client():
    return Groq(api_key=os.getenv("GROQ_API_KEY"))


def parse_groq_response(content, articles):
    """Parse Groq JSON response and merge back with articles."""
    try:
        # Strip markdown code fences if model wraps response
        text = content.strip()
        if text.startswith("```"):
            text = "\n".join(text.split("\n")[1:])
            text = text.rstrip("`").strip()
        results = json.loads(text)
        id_map = {a["id"]: a for a in articles}
        enriched = []
        for r in results:
            article = id_map.get(r["id"], {}).copy()
            article["score"] = max(1, min(10, int(r.get("score", 5))))
            article["summary"] = r.get("summary", "")
            article["tags"] = r.get("tags", [])
            enriched.append(article)
        return enriched
    except Exception as e:
        logger.warning(f"Failed to parse Groq response: {e}")
        return []


def analyze_batch(articles):
    """Send articles to Groq for sentiment analysis, return enriched list."""
    if not articles:
        return []
    try:
        client = build_groq_client()
        articles_text = "\n".join(
            f'[id={a["id"]}] 標題：{a["title"]} 內容：{(a.get("content") or "")[:200]}'
            for a in articles
        )
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"分析以下 {len(articles)} 則新聞：\n{articles_text}"},
            ],
            temperature=0.1,
        )
        return parse_groq_response(completion.choices[0].message.content, articles)
    except Exception as e:
        logger.warning(f"Groq analyze_batch failed: {e}")
        return []


def analyze_all(articles):
    """Split into batches of BATCH_SIZE, analyze each, sleep between batches."""
    results = []
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        enriched = analyze_batch(batch)
        results.extend(enriched)
        if i + BATCH_SIZE < len(articles):
            time.sleep(3)
    return results
