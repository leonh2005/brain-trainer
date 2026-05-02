"""
Hola-Quant 主進程
  - 每週一更新監控標的（週成交量前 20）
  - 盤中每 30 分鐘檢查新聞情緒
  - 情緒顯著偏多/空時，對所有標的執行 3-LLM 分析
  - 信心 >= 7 才推播 TG 等待確認
"""
import asyncio
import logging
import os
from datetime import datetime, time as dtime

import aiohttp
from dotenv import load_dotenv

load_dotenv()

from analyzer import analyze, AnalysisResult
from bot import HolaQuantBot, SignalMessage
from screener import fetch_weekly_top20, load_watchlist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

MARKET_OPEN  = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)
SCAN_INTERVAL_MIN = 30
CONFIDENCE_THRESHOLD = 7
NEWS_BULLISH_THRESHOLD = 65   # bullish_pct > 65% → 偏多訊號
NEWS_BEARISH_THRESHOLD = 40   # bearish_pct > 40% → 偏空訊號


def _in_market_hours() -> bool:
    now = datetime.now().time()
    return MARKET_OPEN <= now <= MARKET_CLOSE


def _is_monday() -> bool:
    return datetime.today().weekday() == 0


async def _fetch_news_stats(session: aiohttp.ClientSession) -> dict:
    url = os.environ["NEWS_ANALYZER_URL"] + "/api/stats?period=day"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            return await resp.json()
    except Exception as e:
        logger.warning(f"[news] 無法取得情緒資料: {e}")
        return {}


def _sentiment_triggered(stats: dict) -> bool:
    if not stats or stats.get("total", 0) < 10:
        return False
    return (
        stats.get("bullish_pct", 0) >= NEWS_BULLISH_THRESHOLD
        or stats.get("bearish_pct", 0) >= NEWS_BEARISH_THRESHOLD
    )


async def _run_analysis_cycle(watchlist: list[dict], news_stats: dict, bot: HolaQuantBot):
    import re
    from analyzer import _groq_analysis, _build_context

    # 階段一：Groq 免費初篩全部標的
    logger.info(f"[main] Groq 初篩 {len(watchlist)} 檔")
    pre_tasks = [
        _groq_analysis(_build_context(s["symbol"], s["name"], news_stats, ""))
        for s in watchlist
    ]
    pre_results = await asyncio.gather(*pre_tasks, return_exceptions=True)

    scored = []
    for stock, raw in zip(watchlist, pre_results):
        if isinstance(raw, Exception):
            continue
        m = re.search(r"信心[：:]\s*(\d+)", raw)
        score = int(m.group(1)) if m else 0
        scored.append((stock, score))

    top5 = [s for s, sc in sorted(scored, key=lambda x: x[1], reverse=True) if sc >= 5][:5]
    logger.info(f"[main] 進入深度分析：{[s['symbol'] for s in top5]}")

    # 階段二：GPT-5 Mini 深度分析前 5
    tasks = [
        analyze(symbol=s["symbol"], name=s["name"], news_stats=news_stats)
        for s in top5
    ]
    results: list[AnalysisResult] = await asyncio.gather(*tasks, return_exceptions=True)

    high_conf = [
        r for r in results
        if isinstance(r, AnalysisResult)
        and r.direction != "觀望"
        and r.confidence >= CONFIDENCE_THRESHOLD
    ]
    high_conf.sort(key=lambda r: r.confidence, reverse=True)

    logger.info(f"[main] 高信心訊號 {len(high_conf)} 個")
    for result in high_conf[:3]:  # 最多推 3 個
        sig = SignalMessage(
            symbol=result.symbol,
            name=result.name,
            direction=result.direction,
            confidence=result.confidence,
            summary=result.summary,
            risks=result.risks,
            price_info=result.price_info,
        )

        confirmed_event = asyncio.Event()
        confirmed_result = {"value": False}

        async def on_confirm(confirmed: bool, ev=confirmed_event, res=confirmed_result):
            res["value"] = confirmed
            ev.set()

        await bot.send_signal(sig, on_confirm)
        await confirmed_event.wait()

        if confirmed_result["value"]:
            logger.info(f"[main] 使用者確認 {result.symbol}，等待執行層（Phase 4）")
        else:
            logger.info(f"[main] 使用者拒絕 {result.symbol}")


async def _monitor_loop(bot: HolaQuantBot):
    watchlist = load_watchlist()
    logger.info(f"[main] 監控標的：{[s['symbol'] for s in watchlist]}")

    async with aiohttp.ClientSession() as session:
        while True:
            now = datetime.now()

            # 每週一更新標的
            if _is_monday() and now.hour == 8 and now.minute < SCAN_INTERVAL_MIN:
                logger.info("[main] 週一更新監控標的")
                watchlist = fetch_weekly_top20()

            # 盤中掃描
            if _in_market_hours():
                stats = await _fetch_news_stats(session)
                logger.info(
                    f"[news] 多 {stats.get('bullish_pct', 0)}%  "
                    f"空 {stats.get('bearish_pct', 0)}%  "
                    f"共 {stats.get('total', 0)} 篇"
                )
                if _sentiment_triggered(stats):
                    await _run_analysis_cycle(watchlist, stats, bot)
            else:
                logger.debug("[main] 非交易時段，跳過")

            await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


async def main():
    bot = HolaQuantBot()
    await bot.start()
    try:
        await _monitor_loop(bot)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
