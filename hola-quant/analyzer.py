"""
3-LLM 交叉驗證分析流程：
  Groq llama-3.3-70b → 初步分析
  GPT-5 Mini         → 交叉驗證（挑戰者）
  Gemini 2.5 Flash   → 整合結論
"""
import asyncio
import os
import logging
from dataclasses import dataclass

from groq import AsyncGroq

logger = logging.getLogger(__name__)
from openai import AsyncOpenAI
from google import genai as google_genai


@dataclass
class AnalysisResult:
    symbol: str
    name: str
    direction: str      # 多 / 空 / 觀望
    confidence: int     # 1-10
    summary: str
    risks: str
    groq_view: str
    gpt_view: str
    # 價位資訊（由 pricer 填入，可為 None）
    price_info: object = None


_groq = None
_openai = None
_gemini = None


def _clients():
    global _groq, _openai, _gemini
    if _groq is None:
        _groq = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    if _openai is None:
        _openai = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    if _gemini is None:
        _gemini = google_genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _groq, _openai, _gemini


def _build_context(symbol: str, name: str, news_stats: dict, kbars_summary: str) -> str:
    return f"""
標的：{symbol} {name}
市場新聞情緒（今日）：多頭 {news_stats.get('bullish_pct', 0)}%，空頭 {news_stats.get('bearish_pct', 0)}%，中性 {news_stats.get('neutral_pct', 0)}%（共 {news_stats.get('total', 0)} 篇）
近期 K 線摘要：{kbars_summary or '無資料'}
""".strip()


async def _groq_analysis(context: str) -> str:
    """初步分析：優先用 Groq，失敗時 fallback 到 GPT-5 Mini"""
    groq, openai, _ = _clients()
    try:
        resp = await groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": (
                    "你是台股短線交易分析師。根據提供的資訊給出：方向（多/空/觀望）、信心分數（1-10）、主要理由（2-3句）。"
                    "格式：方向：X\n信心：N\n理由：..."
                )},
                {"role": "user", "content": context},
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"[groq] 失敗，fallback GPT-5 Mini: {e}")
        resp = await openai.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": (
                    "你是台股短線交易分析師。根據提供的資訊給出：方向（多/空/觀望）、信心分數（1-10）、主要理由（2-3句）。"
                    "格式：方向：X\n信心：N\n理由：..."
                )},
                {"role": "user", "content": context},
            ],
            max_completion_tokens=2000,
        )
        return resp.choices[0].message.content.strip()


async def _gpt_validation(context: str, groq_view: str) -> str:
    _, openai, _ = _clients()
    resp = await openai.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": (
                "你是台股交易風控審查員，職責是挑戰初步分析的盲點。"
                "根據原始資料與初步分析，指出同意之處、不同意之處、被忽略的風險。"
                "格式：同意：...\n質疑：...\n補充風險：..."
            )},
            {"role": "user", "content": f"原始資料：\n{context}\n\n初步分析：\n{groq_view}"},
        ],
        max_completion_tokens=2000,
    )
    return resp.choices[0].message.content.strip()


async def _gpt_conclusion(context: str, groq_view: str, gpt_view: str) -> dict:
    _, openai, _ = _clients()
    import json
    prompt = f"""
原始資料：
{context}

初步分析（Groq llama）：
{groq_view}

交叉驗證（GPT）：
{gpt_view}

整合以上兩份分析，給出最終建議。
只回 JSON，不要其他文字：
{{
  "direction": "多|空|觀望",
  "confidence": 1-10的整數,
  "summary": "給投資人看的一段話，說明方向與主要依據",
  "risks": "主要風險點"
}}
"""
    resp = await openai.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": "你是資深台股分析師，整合多方觀點給出最終結構化建議，只輸出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        max_completion_tokens=2000,
    )
    text = resp.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


async def analyze(
    symbol: str,
    name: str,
    news_stats: dict,
    kbars_summary: str = "",
) -> AnalysisResult:
    import os
    from pricer import calc_position

    context = _build_context(symbol, name, news_stats, kbars_summary)

    groq_view = await _groq_analysis(context)
    gpt_view = await _gpt_validation(context, groq_view)
    conclusion = await _gpt_conclusion(context, groq_view, gpt_view)

    direction = conclusion["direction"]
    confidence = conclusion["confidence"]

    price_info = None
    if direction != "觀望":
        capital = float(os.environ.get("TEST_CAPITAL", 50000))
        price_info = await calc_position(symbol, direction, confidence, capital)

    return AnalysisResult(
        symbol=symbol,
        name=name,
        direction=direction,
        confidence=confidence,
        summary=conclusion["summary"],
        risks=conclusion["risks"],
        groq_view=groq_view,
        gpt_view=gpt_view,
        price_info=price_info,
    )
