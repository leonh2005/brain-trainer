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
        "individual": True,
        "queries": [
            "國巨 MLCC",
            "Yageo passive components",
        ],
    },
    {
        "code": "2408",
        "name": "南亞科",
        "individual": True,
        "queries": [
            "南亞科 DRAM",
            "Nanya Technology memory",
            "Micron MU DRAM memory chip",
        ],
    },
    {
        "code": "1785",
        "name": "光洋科",
        "individual": True,
        "queries": [
            "光洋科",
            "Koway precious metal recycling",
        ],
    },
    {
        "code": "2317",
        "name": "鴻海",
        "individual": True,
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


def _parse_lots(v: str) -> int:
    """TWSE 股數字串轉張數（÷1000），支援負數。"""
    v = v.replace(",", "").strip()
    if not v or v in ("-", "--"):
        return 0
    try:
        return int(v) // 1000
    except ValueError:
        return 0


def fetch_institutional_data(stock_code: str) -> dict | None:
    """TWSE T86：三大法人買賣超（張數），往前找最近三個交易日。"""
    for days_back in range(3):
        date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")
        url = (
            "https://www.twse.com.tw/rwd/zh/fund/T86"
            f"?response=json&date={date}&selectType=ALLBUT0999"
        )
        try:
            r = requests.get(url, timeout=10)
            payload = r.json()
            if payload.get("stat") != "OK" or not payload.get("data"):
                continue
            for row in payload["data"]:
                if row[0].strip() == stock_code:
                    return {
                        "date":         payload.get("date", date),
                        "foreign_lots": _parse_lots(row[4]),
                        "trust_lots":   _parse_lots(row[10]),
                        "total_lots":   _parse_lots(row[18]),
                    }
        except Exception as e:
            logger.warning("T86 失敗 %s day=%s：%s", stock_code, date, e)
    return None


def fetch_big_holder_ratio(stock_code: str) -> float | None:
    """集保股權分散表：大戶（400張=400,000股以上，級距 12-15）持股比率 (%)。"""
    today = datetime.now()
    days_back = (today.weekday() - 4) % 7   # 0 若今天是週五
    last_friday = (today - timedelta(days=days_back)).strftime("%Y%m%d")

    try:
        r = requests.post(
            "https://www.tdcc.com.tw/smWeb/QryStockAjax.do",
            data={"scaDt": last_friday, "stkNo": stock_code, "qryType": "1"},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=10,
        )
        # 嘗試 JSON
        try:
            data = r.json()
            rows = data.get("aaData") or data.get("rows") or []
            big_pct = sum(
                float(str(row[3]).replace(",", ""))
                for row in rows
                if isinstance(row, list) and len(row) >= 4
                and 12 <= int(str(row[0]).strip()) <= 15
            )
            return round(big_pct, 2) if big_pct > 0 else None
        except (ValueError, KeyError):
            pass

        # fallback：HTML 解析
        import re as _re
        big_pct = 0.0
        for m in _re.finditer(
            r'<td[^>]*>(\d+)</td>(?:.*?<td[^>]*>){2}.*?<td[^>]*>([\d,]+\.\d+)</td>',
            r.text, _re.DOTALL
        ):
            try:
                if 12 <= int(m.group(1)) <= 15:
                    big_pct += float(m.group(2).replace(",", ""))
            except ValueError:
                continue
        return round(big_pct, 2) if big_pct > 0 else None

    except Exception as e:
        logger.warning("集保大戶資料失敗 %s：%s", stock_code, e)
    return None


def fetch_foreign_holding(stock_code: str) -> float | None:
    """TWSE MI_QFIIS：外資及陸資持股比率 (%)，最新一日，best-effort。"""
    try:
        r = requests.get(
            "https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS"
            "?response=json&selectType=MS",
            timeout=8,
        )
        payload = r.json()
        if payload.get("stat") != "OK" or not payload.get("data"):
            return None
        for row in payload["data"]:
            if str(row[0]).strip() == stock_code:
                try:
                    return float(str(row[7]).replace("%", "").replace(",", "").strip())
                except ValueError:
                    return None
    except Exception as e:
        logger.debug("MI_QFIIS 失敗 %s：%s", stock_code, e)
    return None


def analyze(holding: dict, articles: list[dict],
            institutional: dict | None = None,
            big_holder: float | None = None) -> dict:
    """用 DeepSeek V3 分析一個持倉的多空情緒。"""
    if not articles:
        return {
            "direction": "neutral",
            "score": 5,
            "summary": "過去 24 小時無相關新聞",
            "push": False,
        }

    news_list = "\n".join(f"- {a['title']}" for a in articles)

    # 法人買賣超補充段落
    inst_section = ""
    if institutional:
        def fmt_lots(n: int) -> str:
            sign = "+" if n >= 0 else ""
            return f"{sign}{n:,}張"
        inst_section = (
            f"\n\n【法人買賣超（{institutional['date']}）】\n"
            f"外資：{fmt_lots(institutional['foreign_lots'])}　"
            f"投信：{fmt_lots(institutional['trust_lots'])}　"
            f"合計：{fmt_lots(institutional['total_lots'])}"
        )
        if institutional.get("foreign_pct") is not None:
            inst_section += f"\n外資持股比率：{institutional['foreign_pct']:.2f}%"

    # 大戶持股率補充段落
    bh_section = ""
    if big_holder is not None:
        bh_section = f"\n\n【集保大戶（400張以上）持股比率】{big_holder:.2f}%"

    prompt = f"""你是專業台灣投資組合分析師。以下是「{holding['name']}（{holding['code']}）」過去 24 小時的相關新聞標題（共 {len(articles)} 則）：

{news_list}{inst_section}{bh_section}

請根據上述資訊（新聞＋法人動向＋大戶持股）對此標的做出多空判斷。

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

def _is_monday() -> bool:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Taipei")).weekday() == 0


def run_session(holdings: list[dict], session_label: str):
    """執行一個時段（台股 or 美股）的分析與推播。"""
    logger.info("開始分析 %s 時段，共 %d 個標的", session_label, len(holdings))

    monday = _is_monday()
    results = []
    lines = []

    for h in holdings:
        articles = fetch_news(h["queries"])
        deduped  = deduplicate(articles)
        logger.info("%s：抓到 %d 則，去重後 %d 則", h["name"], len(articles), len(deduped))

        institutional = None
        big_holder    = None
        if h.get("individual"):
            institutional = fetch_institutional_data(h["code"])
            if institutional:
                logger.info("%s 法人買賣超：%+d 張", h["name"], institutional["total_lots"])
            foreign_pct = fetch_foreign_holding(h["code"])
            if foreign_pct is not None:
                logger.info("%s 外資持股：%.2f%%", h["name"], foreign_pct)
                if institutional is None:
                    institutional = {}
                institutional["foreign_pct"] = foreign_pct
            if monday:
                big_holder = fetch_big_holder_ratio(h["code"])
                if big_holder is not None:
                    logger.info("%s 大戶持股率：%.2f%%", h["name"], big_holder)

        result = analyze(h, deduped, institutional, big_holder)
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
