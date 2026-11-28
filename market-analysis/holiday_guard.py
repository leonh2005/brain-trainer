#!/usr/bin/env python3
"""連假前檢查（T-3/T-2/T-1）：質押壓力測試 + 假期間事件掃描 → Telegram。

每個交易日 13:00（收盤前半小時）由 LaunchAgent 觸發；
不是 T-1~T-3 就安靜結束，不做任何事。

資料來源：
- 庫存：gateway /positions（需 API 帳務權限），失敗則讀 config/pledge_portfolio.json
- 報價：gateway /snapshot（整批失敗時逐檔重抓）
- 休假：data/tw_market_holidays.json
- 事件：市場分析服務 5350 /api/econ

結論三態：紅燈（需補擔保品）／資料不完整／全部通過。
"""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "pledge_portfolio.json"
HOLIDAYS_FILE = ROOT / "data" / "tw_market_holidays.json"
STATE_FILE = ROOT / "data" / "holiday_guard_state.json"
GATEWAY = "http://127.0.0.1:5455"
ECON_API = "http://127.0.0.1:5350/api/econ"
TG_TOKEN_FILE = ROOT / ".secrets" / "telegram_token.txt"
TG_CHAT = "7556217543"

STRESS_DROP = 0.097          # 2015-2026 連假後最大單日跌幅（2025-04-07 關稅）
CHECK_WINDOW = 3             # T-3 ~ T-1
HOLIDAY_MIN_GAP = 4          # 交易日間隔 >= 4 天視為連假
LOOKAHEAD = 25

# 事件類型 → 觀察重點（利多/利空取決於實際結果，不斷言方向）
EVENT_HINTS = {
    "非農": "數據強於預期 → 降息預期降溫 → 偏空；弱於預期 → 偏多",
    "Fed會議": "降息 → 偏多；維持或升息 → 偏空（看會後聲明措辭）",
    "FOMC": "降息 → 偏多；維持或升息 → 偏空（看會後聲明措辭）",
    "CPI": "高於預期 → 通膨黏著 → 偏空；低於預期 → 偏多",
    "PPI": "高於預期 → 成本壓力 → 偏空",
    "PCE": "核心高於預期 → 偏空；低於預期 → 偏多",
    "結算日": "波動加大，方向中性",
    "三巫日": "波動加大，方向中性偏空",
    "法說會": "財報與展望優於預期 → 偏多；低於預期 → 偏空",
    "元首會面": "看會後聯合聲明；破局 → 偏空",
}


def http_json(url: str, timeout: int = 20) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def load_holidays() -> tuple[set[str], set[int]]:
    """回傳 (休市日集合, 涵蓋年份)。缺年份時呼叫端要警告，不可靜默。"""
    try:
        raw = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), set()
    days = {d for year in raw.values() for d in year.get("days", [])}
    years = {int(y) for y in raw if str(y).isdigit()}
    return days, years


def is_trading_day(day: date, holidays: set[str]) -> bool:
    return day.weekday() < 5 and day.isoformat() not in holidays


def next_trading_day(day: date, holidays: set[str], limit: int = 30) -> date | None:
    cur = day + timedelta(days=1)
    for _ in range(limit):
        if is_trading_day(cur, holidays):
            return cur
        cur += timedelta(days=1)
    return None


def find_t1(today: date, holidays: set[str], limit: int = LOOKAHEAD) -> date | None:
    """從今天往後找第一個「之後休市 >= HOLIDAY_MIN_GAP 天」的交易日。"""
    cur = today
    for _ in range(limit):
        if not is_trading_day(cur, holidays):
            cur += timedelta(days=1)
            continue
        nxt = next_trading_day(cur, holidays)
        if nxt and (nxt - cur).days >= HOLIDAY_MIN_GAP:
            return cur
        cur = nxt or cur + timedelta(days=1)
    return None


def trading_offset(today: date, t1: date, holidays: set[str]) -> int:
    """今天是 T-幾（T-1 回傳 1）。"""
    count = 0
    cur = today
    while cur < t1:
        if is_trading_day(cur, holidays):
            count += 1
        cur += timedelta(days=1)
    return count + 1


def get_positions(config: dict, state: dict) -> tuple[list[dict], str]:
    """優先 API，失敗退回設定檔。

    401 會觸發 Shioaji SDK 內部主動斷線重登（log: "Received 401, proactively
    disconnecting Solace session"），每次呼叫都會讓共用 session 中斷約 1 秒。
    因此 401 後記下日期，7 天內不再嘗試；權限開通後最多 7 天自動切換回 API。
    """
    forbidden_at = state.get("positions_forbidden_at")
    if forbidden_at:
        try:
            if (date.today() - date.fromisoformat(forbidden_at)).days < 7:
                return config.get("holdings", []), "設定檔（API 權限未開通）"
        except ValueError:
            pass
    try:
        resp = http_json(f"{GATEWAY}/positions", timeout=25)
        if resp.get("ok") and resp.get("data") is not None:
            state.pop("positions_forbidden_at", None)
            return resp["data"], "Shioaji API"
        reason = str(resp.get("error", ""))[:50]
        if "401" in reason or "permission" in reason.lower():
            state["positions_forbidden_at"] = date.today().isoformat()
    except Exception as exc:  # noqa: BLE001 - 任何失敗都退回設定檔
        reason = str(exc)[:50]
    return config.get("holdings", []), f"設定檔（API 不可用：{reason}）"


def collect_codes(positions: list[dict], config: dict) -> list[str]:
    """報價代碼取聯集：API 庫存 ∪ 設定檔庫存 ∪ 質押部位。

    只取 positions 會讓「只填 pledged、holdings 留空」的情境完全拿不到價。
    """
    codes = {str(p.get("code", "")) for p in positions if p.get("code")}
    for key in ("holdings", "pledged"):
        for p in config.get(key, []):
            if p.get("code"):
                codes.add(str(p["code"]))
    return sorted(codes)


def get_prices(codes: list[str]) -> tuple[dict, list[str]]:
    """回傳 (prices, 取價失敗代碼)。整批失敗時逐檔重抓，一個壞代碼不會全滅。"""
    if not codes:
        return {}, []
    try:
        resp = http_json(f"{GATEWAY}/snapshot?codes={','.join(codes)}", timeout=20)
        if resp.get("ok"):
            data = resp.get("data", {})
            return data, [c for c in codes if c not in data]
    except Exception:  # noqa: BLE001 - 落到逐檔
        pass
    prices: dict = {}
    for code in codes:
        try:
            resp = http_json(f"{GATEWAY}/snapshot?codes={code}", timeout=8)
            if resp.get("ok"):
                prices.update(resp.get("data", {}))
        except Exception:  # noqa: BLE001
            continue
    return prices, [c for c in codes if c not in prices]


def compute_pledge(config: dict, prices: dict) -> dict:
    """回傳 {status: ok|missing|unset, value, ratio, worst_ratio, call_pct, missing}。"""
    loan = float(config.get("loan_amount", 0) or 0)
    call_pct = float(config.get("maintenance_call_pct", 130) or 130)
    pledged = config.get("pledged", [])
    if loan <= 0 or not pledged:
        return {"status": "unset", "call_pct": call_pct, "missing": []}

    value = 0.0
    missing = []
    for pos in pledged:
        code = str(pos.get("code", ""))
        close = prices.get(code, {}).get("close")
        if close is None:
            missing.append(code)
        else:
            value += float(close) * int(pos.get("quantity", 0))
    if missing:
        return {"status": "missing", "value": value, "call_pct": call_pct,
                "missing": missing}

    ratio = value / loan * 100
    worst_ratio = value * (1 - STRESS_DROP) / loan * 100
    return {"status": "ok", "value": value, "ratio": ratio,
            "worst_ratio": worst_ratio, "call_pct": call_pct, "missing": []}


def get_calendar(days: int = 30) -> tuple[list[dict], str]:
    """接下來 N 天的國際／本地事件，附觀察重點。回傳 (events, status)。"""
    try:
        payload = http_json(ECON_API, timeout=30)["data"]
    except Exception:  # noqa: BLE001
        return [], "error"
    today = date.today()
    end = today + timedelta(days=days)
    out = []
    for ev in payload.get("events", []):
        try:
            ev_date = date.fromisoformat(str(ev.get("date", ""))[:10])
        except ValueError:
            continue
        if today <= ev_date <= end:
            category = str(ev.get("category", ""))
            out.append({
                "date": ev_date.isoformat(),
                "days": (ev_date - today).days,
                "title": str(ev.get("title", "")),
                "category": category,
                "hint": EVENT_HINTS.get(category, "無特定方向"),
            })
    out.sort(key=lambda x: x["date"])
    return out, "ok"


def get_events(start: date, end: date) -> tuple[list[str], str]:
    """假期間的事件（字串清單，供 Telegram 訊息使用）。"""
    calendar, status = get_calendar(60)
    if status != "ok":
        return [], "error"
    picked = [e for e in calendar
              if start <= date.fromisoformat(e["date"]) <= end]
    return [f"{e['date'][5:]} {e['title']}" for e in picked], "ok"


def get_war_news() -> list[dict]:
    """近期地緣消息（Google News RSS）。"""
    try:
        payload = http_json(ECON_API, timeout=30)["data"]
    except Exception:  # noqa: BLE001
        return []
    return [{"date": str(w.get("date", "")), "title": str(w.get("title", ""))[:120],
             "link": str(w.get("link", ""))}
            for w in payload.get("war", [])[:6] if w.get("title")]


JEV_CACHE = ROOT / "data" / "holiday_guard_jev_cache.json"
JEV_TTL = 6 * 3600          # 快取 6 小時，一天最多 4 次呼叫
JEV_MAX_EVENTS = 3          # 只判斷最近 3 個事件
JEV_URL = "https://api.typesafe.ai/v1/systemone"
NEWS_TTL = 3 * 3600         # 新聞搜尋快取 3 小時
NEWS_PER_EVENT = 5          # 每個事件取前 5 則標題


FINNHUB_KEYWORDS = {
    "Fed會議": ["fomc", "powell", "fed hike", "fed cut", "fed rate", "rate decision"],
    "FOMC": ["fomc", "powell", "fed hike", "fed cut", "fed rate", "rate decision"],
    "CPI": ["cpi", "consumer price", "inflation data"],
    "PPI": ["ppi", "producer price"],
    "PCE": ["pce", "core inflation"],
    "非農": ["nonfarm", "payroll", "unemployment rate", "jobs report"],
}


def _finnhub_key() -> str:
    try:
        return (ROOT / ".secrets" / "finnhub_key.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def search_finnhub_news(event: dict, limit: int = 4) -> list[str]:
    """Finnhub general news 依事件關鍵字過濾（英文一手，Reuters/CNBC 等）。"""
    keywords = FINNHUB_KEYWORDS.get(str(event.get("category", "")))
    if not keywords:
        return []
    key = _finnhub_key()
    if not key:
        return []
    try:
        data = http_json(f"https://finnhub.io/api/v1/news?category=general&token={key}",
                         timeout=15)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        headline = str(item.get("headline", ""))
        if headline and any(k in headline.lower() for k in keywords):
            if headline[:100] not in out:
                out.append(headline[:100])
        if len(out) >= limit:
            break
    return out


def search_event_news(event: dict, limit: int = NEWS_PER_EVENT) -> list[str]:
    """事件相關新聞：Google News RSS（中文）+ Finnhub（英文）。

    免費、與 5350 的地緣消息同源；只取標題當 Jev 的判斷素材。
    """
    queries = [event["title"]]
    category = str(event.get("category", ""))
    if category and category not in event["title"]:
        queries.append(category)

    titles: list[str] = []
    for q in queries[:2]:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
               + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
        try:
            xml = urllib.request.urlopen(url, timeout=15).read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 新聞抓不到就退回純文字提示
            continue
        found = re.findall(r"<item>.*?<title>(.*?)</title>", xml, re.S)
        for raw in found:
            title = re.sub(r"<.*?>", "", raw).replace("&amp;", "&").strip()
            if title and title[:100] not in titles:
                titles.append(title[:100])
        if len(titles) >= limit:
            break

    # 中文與英文各留固定名額，避免英文被中文淹沒
    zh = titles[:limit]
    en = [h for h in search_finnhub_news(event, limit=3) if h not in zh]
    return zh + en


def _jev_key() -> str:
    """TypeSafe key 從 Claude 的 MCP 設定讀取（單一來源，不另外落地）。"""
    try:
        cfg = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
        return cfg["mcpServers"]["jev"]["env"]["TYPESAFE_API_KEY"]
    except (OSError, KeyError, ValueError, TypeError):
        return ""


def _http_post_json(url: str, body: dict, headers: dict, timeout: int = 60) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    base = {"Content-Type": "application/json", "Accept": "application/json"}
    req = urllib.request.Request(url, data=payload, headers={**base, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def jev_event_bias(events: list[dict]) -> dict:
    """搜尋事件相關新聞，交給 Jev 判斷利多/利空。失敗回空 dict（上層退回文字提示）。"""
    if not events:
        return {}
    picked = events[:JEV_MAX_EVENTS]

    news = {f"{e['date']}|{e['title']}": search_event_news(e) for e in picked}
    key_src = json.dumps(
        {"e": [[e["date"], e["title"]] for e in picked], "n": news},
        ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()[:16]
    try:
        cache = json.loads(JEV_CACHE.read_text(encoding="utf-8"))
        if cache.get("key") == cache_key and time.time() - cache.get("ts", 0) < JEV_TTL:
            return cache.get("bias", {})
    except (OSError, ValueError):
        pass

    api_key = _jev_key()
    if not api_key:
        return {}

    lines = ["台股加權指數交易環境。接下來的事件與相關新聞："]
    for i, event in enumerate(picked):
        titles = news.get(f"{event['date']}|{event['title']}", [])
        lines.append(f"\n事件 {i + 1}：{event['date']} {event['title']}")
        lines.extend(f"  - {t}" for t in titles) if titles else lines.append("  - （無相關新聞）")
    state = "\n".join(lines)

    questions = {
        f"e{i}": {
            "type": "choice",
            "instructions": f"根據上述新聞，{e['title']} 對台股未來一週的影響方向",
            "criteria": {
                "利多": "新聞內容對台股有正面推動",
                "利空": "新聞內容對台股有負面壓力",
                "中性": "影響有限或方向不明",
            },
        }
        for i, e in enumerate(picked)
    }
    try:
        resp = _http_post_json(JEV_URL,
                               {"state": state, "questions": questions, "model": "jev-latest"},
                               {"Authorization": f"Bearer {api_key}"}, timeout=90)
    except Exception:  # noqa: BLE001 - Jev 失敗不可影響主流程
        return {}

    bias: dict = {}
    answers = resp.get("answers", {})
    for i, event in enumerate(picked):
        ans = answers.get(f"e{i}", {})
        if isinstance(ans, dict) and ans.get("type") == "choice":
            bias[f"{event['date']}|{event['title']}"] = {
                "choice": ans.get("choice"),
                "confidence": ans.get("confidence"),
                "probabilities": ans.get("probabilities", {}),
                "news": news.get(f"{event['date']}|{event['title']}", []),
            }
    try:
        JEV_CACHE.write_text(
            json.dumps({"key": cache_key, "ts": time.time(), "bias": bias}, ensure_ascii=False),
            encoding="utf-8")
    except OSError:
        pass
    return bias


def send_telegram(text: str) -> bool:
    try:
        token = TG_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.load(resp).get("ok", False)
    except Exception:  # noqa: BLE001
        return False


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def build_verdict(pledge: dict, events: list[str], events_status: str) -> tuple[str, bool]:
    """回傳 (建議文字, 是否紅燈)。三態：紅燈／緩衝不足／資料不完整／通過。"""
    if pledge["status"] == "ok":
        if pledge["worst_ratio"] < pledge["call_pct"]:
            return "🚨 需補擔保品或部分還款", True
        if pledge["worst_ratio"] < pledge["call_pct"] + 20:
            return "🟡 緩衝不足，考慮補擔保品", False
    if pledge["status"] in ("missing", "unset") or events_status != "ok":
        return "⚪ 資料不完整，無法確認", False
    if events:
        return "🟡 有事件，T-1 再確認一次", False
    return "✅ 不需動作", False


def build_message(ctx: dict) -> str:
    lines = [
        f"⚠️ 連假前檢查 T-{ctx['offset']}（{ctx['today'].isoformat()}）",
        f"休市到 {ctx['resume'].isoformat()} 才開盤 ｜ 資料：{ctx['source']}",
        "",
    ]

    pledge = ctx["pledge"]
    if pledge["status"] == "ok":
        lines += [
            "📊 質押狀況",
            f"擔保品市值：{pledge['value']:,.0f}",
            f"借款金額：{ctx['loan']:,.0f}",
            f"維持率：{pledge['ratio']:.0f}%（追繳線 {pledge['call_pct']:.0f}%）",
            "",
            f"📉 壓力測試（跌 {STRESS_DROP * 100:.1f}%）",
            f"最壞維持率：{pledge['worst_ratio']:.0f}%",
        ]
        if pledge["worst_ratio"] < pledge["call_pct"]:
            lines.append(f"🚨 最壞情況會跌破追繳線（差 {pledge['call_pct'] - pledge['worst_ratio']:.0f} 個百分點）")
        elif pledge["worst_ratio"] < pledge["call_pct"] + 20:
            lines.append("🟡 緩衝不足 20 個百分點，考慮補擔保品")
        else:
            lines.append("✅ 仍高於追繳線")
        credit = ctx["credit_limit"]
        if credit > ctx["loan"]:
            lines.append(f"借滿額度 {credit:,.0f} 時維持率：{pledge['value'] / credit * 100:.0f}%")
        lines.append("")
    elif pledge["status"] == "missing":
        lines += [
            "📊 質押狀況",
            f"⚠️ 缺價代碼：{', '.join(pledge['missing'])}",
            "無法計算維持率，請自行至券商 App 確認",
            "",
        ]
    else:
        lines += ["📊 質押狀況：未設定（config/pledge_portfolio.json）", ""]

    positions = ctx["positions"]
    if positions:
        lines.append("💼 台股部位")
        total = 0.0
        for pos in positions:
            code = str(pos.get("code", ""))
            qty = int(pos.get("quantity", 0) or 0)
            close = ctx["prices"].get(code, {}).get("close")
            if close is None:
                lines.append(f"  {code} {qty:,} 股（取價失敗）")
                continue
            market_value = float(close) * qty
            total += market_value
            lines.append(f"  {code} {qty:,} 股 × {float(close):.2f} = {market_value:,.0f}")
        lines += [f"  合計：{total:,.0f}", ""]

    if ctx["events_status"] != "ok":
        lines += ["📅 假期間事件", "⚪ 事件服務無回應，未能確認", ""]
    else:
        lines.append("📅 假期間事件")
        lines.extend(f"  {e}" for e in ctx["events"]) if ctx["events"] else lines.append("  無重大國定事件")
        if ctx["offset"] <= 1 and ctx["war_news"]:
            lines.append("  近期地緣消息：")
            lines.extend(f"    {w['title']}" for w in ctx["war_news"])
        lines.append("")

    lines.append(ctx["verdict"])
    if ctx["failed_codes"]:
        lines.append(f"（取價失敗：{', '.join(ctx['failed_codes'])}）")
    return "\n".join(lines)


def main() -> None:
    holidays, years = load_holidays()
    today = date.today()

    if today.year not in years:
        send_telegram(f"⚠️ holiday-guard：假日檔缺 {today.year} 年資料，連假判定停用，請更新 data/tw_market_holidays.json")
        print(f"假日檔缺 {today.year} 年資料")
        return

    if not is_trading_day(today, holidays):
        return

    t1 = find_t1(today, holidays)
    if t1 is None:
        return
    offset = trading_offset(today, t1, holidays)
    if offset > CHECK_WINDOW:
        return

    state = load_state()
    sent_key = f"{today.isoformat()}-T{offset}"
    if state.get("last_key") == sent_key:
        print(f"{sent_key} 已通知過，跳過")
        return

    resume = next_trading_day(t1, holidays) or t1
    config: dict = {}
    if CONFIG.exists():
        try:
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            config = {}

    positions, source = get_positions(config, state)
    codes = collect_codes(positions, config)
    prices, failed_codes = get_prices(codes)
    pledge = compute_pledge(config, prices)
    events, events_status = get_events(t1 + timedelta(days=1), resume)
    war_news = get_war_news() if offset <= 1 else []

    verdict, _is_red = build_verdict(pledge, events, events_status)
    ctx = {
        "today": today, "t1": t1, "resume": resume, "offset": offset,
        "source": source, "positions": positions, "prices": prices,
        "failed_codes": failed_codes, "pledge": pledge,
        "loan": float(config.get("loan_amount", 0) or 0),
        "credit_limit": float(config.get("credit_limit", 0) or 0),
        "events": events, "events_status": events_status,
        "war_news": war_news, "verdict": verdict,
    }
    message = build_message(ctx)
    print(message)
    if send_telegram(message):
        state["last_key"] = sent_key
    # 無條件存檔：positions_forbidden_at 的冷卻記錄不能因為推播失敗而遺失
    save_state(state)


if __name__ == "__main__":
    main()
