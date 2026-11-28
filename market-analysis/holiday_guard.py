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

import json
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


def get_events(start: date, end: date) -> tuple[list[str], str]:
    """回傳 (事件清單, status: ok|error)。抓取失敗不可偽裝成「有事件」。"""
    try:
        payload = http_json(ECON_API, timeout=30)["data"]
    except Exception:  # noqa: BLE001
        return [], "error"
    events = []
    for ev in payload.get("events", []):
        try:
            ev_date = date.fromisoformat(str(ev.get("date", ""))[:10])
        except ValueError:
            continue
        if start <= ev_date <= end:
            events.append(f"{ev_date.month}/{ev_date.day} {ev.get('title', '')}")
    return events, "ok"


def get_war_news() -> list[str]:
    """近期地緣消息（非特指假期間，只在 T-1 附上）。"""
    try:
        payload = http_json(ECON_API, timeout=30)["data"]
    except Exception:  # noqa: BLE001
        return []
    return [str(w.get("title", ""))[:60] for w in payload.get("war", [])[:2] if w.get("title")]


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
            lines.extend(f"    {w}" for w in ctx["war_news"])
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
