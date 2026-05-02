"""強制執行一次完整分析循環（忽略時段和情緒門檻，用於測試）"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from analyzer import analyze, AnalysisResult
from bot import HolaQuantBot, SignalMessage

async def main():
    from screener import load_watchlist
    watchlist = load_watchlist()
    print(f"[screener] 監控 {len(watchlist)} 檔：{[s['symbol'] for s in watchlist]}\n")

    import aiohttp
    async with aiohttp.ClientSession() as session:
        import os
        url = os.environ["NEWS_ANALYZER_URL"] + "/api/stats?period=day"
        async with session.get(url) as resp:
            news_stats = await resp.json()

    print(f"[news] 多 {news_stats['bullish_pct']}%  空 {news_stats['bearish_pct']}%  共 {news_stats['total']} 篇\n")

    # 階段一：Groq 快速初篩全部 20 檔（免費）
    from analyzer import _groq_analysis, _build_context, _gpt_validation, _gpt_conclusion
    pre_tasks = [
        _groq_analysis(_build_context(s["symbol"], s["name"], news_stats, ""))
        for s in watchlist
    ]
    pre_results = await asyncio.gather(*pre_tasks, return_exceptions=True)

    # 解析 Groq 信心分數，取前 5
    import re
    scored = []
    for stock, raw in zip(watchlist, pre_results):
        if isinstance(raw, Exception):
            continue
        m = re.search(r"信心[：:]\s*(\d+)", raw)
        score = int(m.group(1)) if m else 0
        d = re.search(r"方向[：:]\s*(多|空|觀望)", raw)
        direction = d.group(1) if d else "觀望"
        scored.append((stock, raw, score, direction))

    scored.sort(key=lambda x: x[2], reverse=True)
    top5 = [(s, r, sc, d) for s, r, sc, d in scored if sc >= 5][:5]
    print(f"\n[篩選] Groq 信心 ≥ 5：{[(s['symbol'], sc) for s, _, sc, _ in top5]}\n")

    # 階段二：GPT-5 Mini 深度分析前 5
    tasks = [
        analyze(s["symbol"], s["name"], news_stats)
        for s, _, _, _ in top5
    ]
    results: list[AnalysisResult] = await asyncio.gather(*tasks, return_exceptions=True)

    print("=== 分析結果總覽 ===")
    for r in results:
        if isinstance(r, AnalysisResult):
            print(f"  {r.symbol} {r.name:8s} | {r.direction} {r.confidence}/10 | {r.summary[:40]}...")
        else:
            print(f"  ERROR: {r}")

    high_conf = [
        r for r in results
        if isinstance(r, AnalysisResult) and r.confidence >= 6
    ]

    if not high_conf:
        print("\n[main] 無高信心訊號，不推播")
        return

    bot = HolaQuantBot()
    await bot.start()

    for result in high_conf:
        print(f"\n[{result.symbol}] {result.direction} {result.confidence}/10 — {result.summary[:60]}...")
        sig = SignalMessage(
            symbol=result.symbol,
            name=result.name,
            direction=result.direction,
            confidence=result.confidence,
            summary=result.summary,
            risks=result.risks,
            price_info=result.price_info,
        )
        ev = asyncio.Event()
        res = {}

        async def cb(ok, _ev=ev, _res=res):
            _res["ok"] = ok
            _ev.set()

        await bot.send_signal(sig, cb)
        print(f"[{result.symbol}] TG 訊號已送出，等待確認（30秒逾時）...")
        try:
            await asyncio.wait_for(ev.wait(), timeout=30)
            print(f"[{result.symbol}] {'確認下單' if res['ok'] else '已拒絕'}")
        except asyncio.TimeoutError:
            print(f"[{result.symbol}] 逾時，視為拒絕")

    await bot.stop()

asyncio.run(main())
