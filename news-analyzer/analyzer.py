import json
import logging
import os
import re
import time
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BATCH_SIZE = 20
GROQ_MODEL = "llama-3.1-8b-instant"

BASE_SYSTEM_PROMPT = """你是財經市場情緒分析師。分析新聞標題與內容：
1. 判斷是否與股票市場、金融投資、總體經濟相關（relevant）
2. 若相關，給出情緒分數

回傳純 JSON 陣列（不要有其他文字），格式如下：
[{"id": <原id>, "relevant": <true/false>, "score": <1-10整數，1=極度看空，10=極度看多，不相關填5>, "summary": "<15字內中文摘要，不相關填空字串>", "tags": ["標籤1","標籤2","標籤3"]}]"""

BASE_SINGLE_PROMPT = """你是財經市場情緒分析師。分析以下新聞：
1. 判斷是否與股票市場、金融投資、總體經濟相關（relevant）
2. 若相關，給出情緒分數

回傳純 JSON（不要有其他文字）：
{"id": <原id>, "relevant": <true/false>, "score": <1-10整數，不相關填5>, "summary": "<15字內，不相關填空字串>", "tags": ["標籤1","標籤2"]}"""


def build_groq_client():
    return Groq(api_key=os.getenv("GROQ_API_KEY"))


def build_system_prompt(irrelevant_examples=None):
    prompt = BASE_SYSTEM_PROMPT
    if irrelevant_examples:
        examples_text = "\n".join(f"- {t}" for t in irrelevant_examples)
        prompt += f"\n\n以下是過去被標記為「不相關」的新聞標題範例，請參考類似模式：\n{examples_text}"
    return prompt


def build_single_prompt(irrelevant_examples=None):
    prompt = BASE_SINGLE_PROMPT
    if irrelevant_examples:
        examples_text = "\n".join(f"- {t}" for t in irrelevant_examples)
        prompt += f"\n\n不相關範例：\n{examples_text}"
    return prompt


def extract_json(text):
    """嘗試從 text 中提取第一個合法 JSON 陣列或物件。"""
    text = text.strip()
    # 去掉 markdown code fence
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]).rstrip("`").strip()
    # 直接 parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 用 regex 找到第一個 [...] 或 {...}
    for pattern in (r'\[.*\]', r'\{.*\}'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None


def analyze_single(client, article, irrelevant_examples=None):
    """單篇分析，作為 batch 失敗的 fallback。"""
    try:
        content_text = (article.get("content") or "")[:200]
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": build_single_prompt(irrelevant_examples)},
                {"role": "user", "content": f'[id={article["id"]}] 標題：{article["title"]} 內容：{content_text}'},
            ],
            temperature=0.1,
        )
        result = extract_json(completion.choices[0].message.content)
        if result and isinstance(result, dict) and "score" in result:
            a = article.copy()
            a["relevant"] = result.get("relevant", True)
            a["score"] = max(1, min(10, int(result.get("score", 5))))
            a["summary"] = result.get("summary", "")
            a["tags"] = result.get("tags", [])
            return a
    except Exception as e:
        logger.warning(f"Single analyze failed id={article['id']}: {e}")
    return None


def analyze_batch(articles, irrelevant_examples=None):
    """Send articles to Groq for sentiment analysis, return enriched list."""
    if not articles:
        return []
    client = build_groq_client()
    try:
        articles_text = "\n".join(
            f'[id={a["id"]}] 標題：{a["title"]} 內容：{(a.get("content") or "")[:200]}'
            for a in articles
        )
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": build_system_prompt(irrelevant_examples)},
                {"role": "user", "content": f"分析以下 {len(articles)} 則新聞：\n{articles_text}"},
            ],
            temperature=0.1,
        )
        results = extract_json(completion.choices[0].message.content)
        if results and isinstance(results, list):
            id_map = {a["id"]: a for a in articles}
            analyzed_ids = set()
            enriched = []
            for r in results:
                article = id_map.get(r.get("id"), {}).copy()
                if not article:
                    continue
                article["relevant"] = r.get("relevant", True)
                article["score"] = max(1, min(10, int(r.get("score", 5))))
                article["summary"] = r.get("summary", "")
                article["tags"] = r.get("tags", [])
                enriched.append(article)
                analyzed_ids.add(article["id"])

            # fallback：batch 裡沒回來的逐篇補分析
            missing = [a for a in articles if a["id"] not in analyzed_ids]
            if missing:
                logger.info(f"Batch missing {len(missing)} articles, falling back to single analyze")
                for a in missing:
                    result = analyze_single(client, a, irrelevant_examples)
                    if result:
                        enriched.append(result)
                    time.sleep(0.5)
            return enriched
        else:
            logger.warning("Batch parse failed, falling back to single analyze for all")
    except Exception as e:
        logger.warning(f"Groq analyze_batch failed: {e}, falling back to single analyze")

    # 整個 batch 失敗 → 逐篇
    enriched = []
    for a in articles:
        result = analyze_single(client, a, irrelevant_examples)
        if result:
            enriched.append(result)
        time.sleep(1)
    return enriched


def analyze_all(articles, irrelevant_examples=None):
    """Split into batches of BATCH_SIZE, analyze each, sleep between batches."""
    results = []
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        enriched = analyze_batch(batch, irrelevant_examples)
        results.extend(enriched)
        if i + BATCH_SIZE < len(articles):
            time.sleep(3)
    return results
