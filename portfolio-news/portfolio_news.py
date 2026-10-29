#!/usr/bin/env python3
"""
持倉新聞監控 — 每日兩次推播
- 08:30 台股持倉多空判斷
- 21:00 美股持倉多空判斷

多空判定規則（2026-10 調整）：
- 完全沒有新聞 → direction="none"，不計入整體方向；不再用 5 分中性假裝有資訊
- score 是「影響強度」而非多空分數，顯示時一律附上方向（8/10 利空）
- 整體方向 = (多方總分 − 空方總分) ÷ 總分，絕對值 < 15% 判中性，不硬選邊
- 單一標的 score >= 8 時，另外列「重大利多／重大利空」，並標註原因類別
  （財報／法規政策／供應鏈／地緣政治／總經／產品技術／競爭態勢／資金面／其他）
"""
import hashlib
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("portfolio_news")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip())
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "7556217543")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", open(os.path.expanduser("~/CCProject/.secrets/deepseek_key.txt")).read().strip())

MAX_FETCH   = 50   # RSS 最多抓幾則
MAX_SEND    = 20   # 去重後送 DeepSeek 上限
SIMILAR_THR = 0.6  # 標題相似度閾值（超過視為重複）

# ── 設定檔 ──────────────────────────────────────────────────────────────────

HOLDINGS_FILE = Path(__file__).parent.parent / "config" / "portfolio_holdings.json"

# 各語系的 Google News RSS 參數 (hl, gl, ceid)
LOCALES = {
    "zh-TW": ("zh-TW", "TW", "TW:zh-Hant"),
    "en-US": ("en-US", "US", "US:en"),
}


def load_holdings() -> dict:
    """讀取標的清單設定檔（config/portfolio_holdings.json）。

    檔案缺失或格式錯誤時明確報錯，不回傳空清單 —— 否則「設定檔壞了」與
    「今天真的沒新聞」會產生完全相同的輸出，無從分辨。
    """
    if not HOLDINGS_FILE.exists():
        raise FileNotFoundError(f"標的清單設定檔不存在：{HOLDINGS_FILE}")
    try:
        with open(HOLDINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"標的清單設定檔格式錯誤：{HOLDINGS_FILE} — {e}") from e
    for key in ("tw", "us"):
        if not isinstance(data.get(key), list):
            raise ValueError(f"標的清單設定檔缺少 '{key}' 陣列：{HOLDINGS_FILE}")
    return data

# ── 工具函式 ───────────────────────────────────────────────────────────────────

def fetch_news(queries: list[str], locale: str = "zh-TW", hours: int = 24) -> list[dict]:
    """從 Google News RSS 抓文章，過濾 hours 小時內，最多 MAX_FETCH 則。

    locale 決定 Google News 的地區版本。拿英文關鍵字去搜台灣版，索引到的
    來源極少且偏舊（實測最新一則常在 100 小時前），24 小時窗一過濾就全空。
    """
    hl, gl, ceid = LOCALES.get(locale, LOCALES["zh-TW"])
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    articles = []
    seen_ids = set()

    for query in queries:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
        )
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                pub = e.get("published_parsed") or e.get("updated_parsed")
                if not pub:
                    continue  # 無日期的文章跳過，無法確認是否為近期
                pub_dt = datetime(*pub[:6], tzinfo=UTC)
                if pub_dt < cutoff:
                    continue
                aid = hashlib.md5(e.title.encode()).hexdigest()
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
                articles.append({
                    "title": e.title,
                    "url": e.get("link", ""),
                    "pub": pub_dt,
                })
                if len(articles) >= MAX_FETCH:
                    break
        except Exception as ex:
            logger.warning("RSS 抓取失敗：%s — %s", query, ex)

    # 按時間排序（最新優先）
    articles.sort(key=lambda a: a["pub"], reverse=True)
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
        # direction="none" 表示「沒有資訊」，不是「中性」—— 不能拿去投票，
        # 否則 7 檔沒新聞的 ETF 各給 5 分，會把真正有利多的標的抵銷掉。
        return {
            "direction": "none",
            "score": 0,
            "category": "",
            "summary": "過去 24 小時無相關新聞",
            "push": False,
        }

    now_utc = datetime.now(UTC)
    def _age_str(a: dict) -> str:
        if not a.get("pub"):
            return ""
        delta = now_utc - a["pub"]
        h = int(delta.total_seconds() // 3600)
        return f"（{h}小時前）" if h < 24 else f"（{delta.days}天前）"

    news_list = "\n".join(f"- {a['title']}{_age_str(a)}" for a in articles)

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

注意：
1. direction 是方向，score 是「影響強度」（1=幾乎無影響，10=重大事件），兩者互相獨立。
   財報大幅不如預期、單日暴跌、重大訂單等事件請給 8 分以上。
2. category 是「最主要的驅動原因」，只能從下列選一個：
   財報、法規政策、供應鏈、地緣政治、總經、產品技術、競爭態勢、資金面、其他
   （財報＝營收獲利指引；法規政策＝監管/補貼/關稅；供應鏈＝缺料/漲價/斷鏈；
     地緣政治＝戰爭/衝突；總經＝利率/經濟數據；產品技術＝新產品/AI 技術；
     競爭態勢＝市占/價格戰；資金面＝法人買賣/評等調整）

回傳純 JSON，格式：
{{
  "direction": "bullish" | "bearish" | "neutral",
  "score": <影響強度 1-10，10 最強>,
  "category": "<上面九個類別之一，逐字照抄>",
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
            max_tokens=350,
        )
        text = re.sub(r"```[a-z]*\n?", "", resp.choices[0].message.content.strip()).strip("`").strip()
        return _normalize_result(json.loads(text))
    except Exception as e:
        logger.error("DeepSeek 分析失敗 %s：%s", holding["code"], e)
        return {
            "direction": "error",
            "score": 0,
            "category": "",
            "summary": "分析失敗",
            "key_factor": "",
            "push": False,
        }


# 重大事件的驅動原因分類（模型只能從這幾個挑，寫錯就歸「其他」）
CATEGORIES = ("財報", "法規政策", "供應鏈", "地緣政治", "總經",
              "產品技術", "競爭態勢", "資金面", "其他")

_CATEGORY_ALIASES = {
    "財報": "財報", "業績": "財報", "獲利": "財報", "營收": "財報", "指引": "財報",
    "法規": "法規政策", "政策": "法規政策", "監管": "法規政策", "關稅": "法規政策",
    "供應鏈": "供應鏈", "缺料": "供應鏈", "漲價": "供應鏈", "斷鏈": "供應鏈", "訂單": "供應鏈",
    "地緣": "地緣政治", "戰爭": "地緣政治", "衝突": "地緣政治", "制裁": "地緣政治",
    "總經": "總經", "經濟": "總經", "利率": "總經", "通膨": "總經", "匯率": "總經",
    "產品": "產品技術", "技術": "產品技術", "新品": "產品技術", "AI": "產品技術",
    "競爭": "競爭態勢", "市占": "競爭態勢", "價格戰": "競爭態勢",
    "資金": "資金面", "籌碼": "資金面", "法人": "資金面", "評等": "資金面",
}


def normalize_category(value) -> str:
    """把模型可能寫歪的類別名對回固定清單，對不上就歸「其他」。"""
    if not value:
        return "其他"
    text = str(value).strip()
    if text in CATEGORIES:
        return text
    for key, cat in _CATEGORY_ALIASES.items():
        if key in text:
            return cat
    return "其他"


def _normalize_result(data: dict) -> dict:
    """模型輸出的欄位防呆：方向／分數／類別三欄都可能寫壞。"""
    if data.get("direction") not in ("bullish", "bearish", "neutral"):
        data["direction"] = "neutral"
    try:
        score = int(data.get("score", 0))
    except (TypeError, ValueError):
        score = 0
    data["score"] = max(0, min(10, score))
    data["category"] = normalize_category(data.get("category"))
    return data


def direction_emoji(d: str) -> str:
    return {"bullish": "📈", "bearish": "📉", "neutral": "➡️",
            "none": "➡️", "error": "⚠️"}.get(d, "➡️")


def score_label(result: dict) -> str:
    """分數加上方向，避免「8/10」被讀成看多 8 分。"""
    d = result.get("direction")
    score = result.get("score", 0)
    if d == "none":
        return "無新聞"
    if d == "error":
        return "分析失敗"
    if d == "neutral":
        return f"{score}/10 中性"
    return f"{score}/10 {'利多' if d == 'bullish' else '利空'}"


def display_name(holding: dict) -> str:
    """名稱若已含代碼就不再重複括號（例如「日本股市（EWJ）」）。"""
    name = holding["name"]
    suffix = f"（{holding['code']}）"
    return name[:-len(suffix)] if name.endswith(suffix) else name


# 淨值佔總分的比例低於此值就判中性 —— 差距太小不該硬選一邊
OVERALL_NEUTRAL_BAND = 0.15


def overall_direction(results: list[dict]) -> tuple[str, dict]:
    """以「有號分數」決定整體方向，並回傳計算明細。

    只看真正有資訊的標的（跳過 none/error），再用
    (多方總分 − 空方總分) / 總分 判斷強度；差距小於 OVERALL_NEUTRAL_BAND 判中性。
    """
    sums = {"bullish": 0, "bearish": 0, "neutral": 0}
    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for r in results:
        d = r.get("direction")
        if d not in sums:
            continue                    # none / error 沒有資訊，不投票
        sums[d] += r.get("score", 0) or 0
        counts[d] += 1

    total = sum(sums.values())
    net = sums["bullish"] - sums["bearish"]
    stats = {"sums": sums, "counts": counts, "net": net, "total": total}

    if total == 0:
        return "none", stats
    ratio = net / total
    if ratio >= OVERALL_NEUTRAL_BAND:
        return "bullish", stats
    if ratio <= -OVERALL_NEUTRAL_BAND:
        return "bearish", stats
    return "neutral", stats


def send_telegram(text: str):
    if os.getenv("PORTFOLIO_NEWS_DRY") == "1":
        logger.info("DRY_RUN：不送出 Telegram，以下為訊息內容")
        print(text)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if not r.ok:
            logger.error("Telegram 推播失敗 %s：%s", r.status_code, r.text[:200])
            # HTML parse 失敗時，改用純文字重試
            r2 = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": text, "parse_mode": ""},
                timeout=10,
            )
            if not r2.ok:
                logger.error("純文字重試也失敗 %s：%s", r2.status_code, r2.text[:200])
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
    entries = []
    lines = []

    for h in holdings:
        articles = fetch_news(h["queries"], h.get("locale", "zh-TW"))
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
        entries.append((h, result))

        emoji = direction_emoji(result["direction"])
        summary = result["summary"]
        key = result.get("key_factor", "")

        # 最新一篇文章的發布時間（沒有新聞就沒有時間可標）
        news_age = ""
        if deduped and deduped[0].get("pub") and result["direction"] != "none":
            delta = datetime.now(UTC) - deduped[0]["pub"]
            hrs = int(delta.total_seconds() // 3600)
            news_age = f" <i>（最新：{hrs}小時前）</i>" if hrs < 24 else f" <i>（最新：{delta.days}天前）</i>"

        line = (f"{emoji} <b>{display_name(h)}（{h['code']}）</b> "
                f"{score_label(result)}{news_age}")
        if result["direction"] != "none":
            line += f"\n   {summary}"
        if key:
            line += f"\n   └ {key}"
        lines.append(line)

    overall, stats = overall_direction(results)
    overall_emoji = direction_emoji(overall)
    overall_label = {"bullish": "偏多", "bearish": "偏空",
                     "neutral": "中性", "none": "無資料"}.get(overall, "中性")
    logger.info("整體方向 %s（多 %d／空 %d／中 %d，淨值 %+d）",
                overall, stats["sums"]["bullish"], stats["sums"]["bearish"],
                stats["sums"]["neutral"], stats["net"])

    # 重大單一事件不該被平均掉，單獨列出來並標上原因類別
    alerts = []
    for label, want in (("⚠️ <b>重大利空</b>", "bearish"), ("🚀 <b>重大利多</b>", "bullish")):
        hits = [(h, r) for h, r in entries
                if r.get("direction") == want and r.get("score", 0) >= 8]
        if not hits:
            continue
        groups: dict[str, list[str]] = {}
        for h, r in hits:
            cat = r.get("category") or "其他"
            groups.setdefault(cat, []).append(f"{display_name(h)} {r['score']}/10")
        for cat, items in groups.items():
            alerts.append(f"{label}｜{cat}：{'、'.join(items)}")

    now_str = datetime.now().strftime("%m/%d %H:%M")
    header = f"📊 <b>{session_label}持倉情報</b>（{now_str}）\n\n"
    body   = "\n\n".join(lines)
    footer = (f"\n\n{overall_emoji} <b>整體方向：{overall_label}</b>"
              f"（多方 {stats['sums']['bullish']}／空方 {stats['sums']['bearish']}"
              f"／中性 {stats['sums']['neutral']}，淨值 {stats['net']:+d}）")
    if alerts:
        footer += "\n" + "\n".join(alerts)

    send_telegram(header + body + footer)
    logger.info("%s 時段推播完成", session_label)


def run():
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Taipei"))
    hour, minute = now.hour, now.minute

    # 08:25–08:35 → 台股時段
    if 8 <= hour <= 8 and 25 <= minute <= 35:
        session_key, label = "tw", "台股開盤前"
    # 21:00–21:10 → 美股時段
    elif hour == 21 and minute <= 10:
        session_key, label = "us", "美股盤前"
    else:
        logger.info("非推播時段（%02d:%02d），略過", hour, minute)
        return

    try:
        holdings = load_holdings()
    except (FileNotFoundError, ValueError) as e:
        logger.error("無法載入標的清單：%s", e)
        sys.exit(1)
    run_session(holdings[session_key], label)


def probe(spec: dict) -> dict:
    """同時以 en-US 與 zh-TW 抓取同一組關鍵字，回傳兩地區的結果。

    供 command-center 的測試鈕使用：改完關鍵字立刻能看出該用哪個地區、
    這組詞夠不夠力，不必等隔天推播才發現還是空白。
    """
    now = datetime.now(UTC)
    result = {}
    for locale in LOCALES:
        rows = [
            {
                "title": a["title"],
                "age_hours": round((now - a["pub"]).total_seconds() / 3600, 1),
            }
            for a in fetch_news(spec["queries"], locale, hours=72)
        ]
        result[locale] = {
            "within_24h": sum(1 for r in rows if r["age_hours"] <= 24),
            "within_48h": sum(1 for r in rows if r["age_hours"] <= 48),
            "titles": rows[:3],
        }
    return result


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else None
    if cmd == "probe":
        print(json.dumps(probe(json.load(sys.stdin)), ensure_ascii=False))
    elif cmd in ("tw", "us"):
        try:
            holdings = load_holdings()
        except (FileNotFoundError, ValueError) as e:
            logger.error("無法載入標的清單：%s", e)
            sys.exit(1)
        run_session(holdings[cmd], "台股開盤前" if cmd == "tw" else "美股盤前")
    else:
        run()
