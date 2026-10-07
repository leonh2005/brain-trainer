#!/usr/bin/env python3
"""盤中均線接近/跌破監測
每 5 分鐘執行，台股交易時間 09:00-13:35
接近 1% 或跌破均線時推 Telegram
"""
import fcntl
import json
import math
import os
from datetime import datetime, time
from pathlib import Path

import requests

TELEGRAM_TOKEN  = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
TELEGRAM_CHAT_ID = "7556217543"
GATEWAY = "http://127.0.0.1:5455"   # shioaji-gateway：共用單一 Shioaji 連線
STATE_FILE = Path(__file__).parent / "ma_monitor_state.json"
LOCK_FILE  = Path(__file__).parent / "ma_monitor.lock"
LOG_FILE   = Path(__file__).parent.parent / "logs" / "ma_monitor.log"

WATCHLIST_FILE = Path(__file__).parent.parent / "config" / "ma_watchlist.json"

MA_PERIODS      = [5, 10, 20]
ALERT_THRESHOLD = 1.0   # 距均線 ≤1% 就通知
CLEAR_THRESHOLD = 2.0   # 距均線 >2% 才解除通知狀態
MAX_MA          = max(MA_PERIODS)
HISTORY_DAYS    = MAX_MA + 1 + 10   # 需 MAX_MA 根已收盤日K + 今日，再留緩衝

# 2026 台灣國定假日（週間休市）。來源：行政院人事行政總處行事曆
# （ruyut/TaiwanCalendar 開放資料），每年初需更新。
# 假日整批不執行：休市期間 snapshot 是前收舊價，對歷史均線比對會誤判。
TW_HOLIDAYS = {
    "2026-01-01",  # 開國紀念日
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",  # 農曆春節
    "2026-02-27",  # 補假
    "2026-04-03", "2026-04-06",  # 清明節／補假
    "2026-05-01",  # 勞動節
    "2026-06-19",  # 端午節
    "2026-09-25",  # 中秋節
    "2026-09-28",  # 教師節
    "2026-10-09", "2026-10-26",  # 國慶日／補假
    "2026-12-25",  # 行憲紀念日
}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_trading_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if now.date().isoformat() in TW_HOLIDAYS:
        return False
    t = now.time()
    return time(9, 0) <= t <= time(13, 35)


def send_telegram(msg: str) -> None:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
        if not r.ok:
            log(f"Telegram 發送失敗 HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log(f"Telegram 發送失敗: {e}")


def load_watchlist() -> dict[str, tuple[str, str]]:
    """讀共用設定檔 config/ma_watchlist.json，回傳 {code: (exchange, name)}。"""
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        return {s["code"]: (s.get("exchange", "TSE"), s["name"]) for s in data["stocks"]}
    except Exception as e:
        log(f"讀取 watchlist 失敗（{WATCHLIST_FILE}）：{e}")
        return {}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def gw_bars(sid: str, days: int = 30) -> list[dict]:
    """向 gateway 取「日K」bars（/daily_ohlcv，非分K），每筆含 date/close。"""
    try:
        j = requests.get(f"{GATEWAY}/daily_ohlcv", params={"code": sid, "days": days}, timeout=30).json()
        if not j.get("ok"):
            log(f"gateway daily_ohlcv 非 ok {sid}: {j.get('error')}")
            return []
        return j.get("bars", [])
    except Exception as e:
        log(f"gateway daily_ohlcv 失敗 {sid}: {e}")
        return []


def gw_price(sid: str) -> float | None:
    """向 gateway 取現價(snapshot close)；非正數或 NaN 視為無效。"""
    try:
        j = requests.get(f"{GATEWAY}/snapshot", params={"codes": sid}, timeout=15).json()
        if j.get("ok") and sid in j.get("data", {}):
            p = float(j["data"][sid]["close"])
            if p > 0 and not math.isnan(p):
                return p
            log(f"{sid}: snapshot 價格異常（{p}）")
    except Exception as e:
        log(f"gateway snapshot 失敗 {sid}: {e}")
    return None


def closed_closes(bars: list[dict], today: str) -> list[float]:
    """取「已收盤」日K的收盤價，排除 date >= today（gateway 用 yfinance 補的今日合成K）。

    用日期而非位置切片，因此不論 gateway 有沒有補今日那根，結果都一致。
    """
    return [float(b["close"]) for b in bars if b.get("date", "") < today]


def analyze(sid: str, name: str, state: dict) -> bool:
    """回傳 True 表示本檔正常處理；False 表示資料或取價失敗。"""
    today = datetime.now().date().isoformat()
    bars = gw_bars(sid, days=HISTORY_DAYS)
    hist = closed_closes(bars, today)
    if len(hist) < MAX_MA:
        log(f"{name}: 歷史資料不足（{len(hist)} 筆）")
        return False

    current = gw_price(sid)
    if current is None:
        log(f"{name}: snapshot 取價失敗，跳過")
        return False

    alerts = []
    for n in MA_PERIODS:
        ma_val = sum(hist[-n:]) / n
        key    = f"{sid}_{n}MA"
        dist   = (current - ma_val) / ma_val * 100  # 正=在線上，負=跌破

        if dist > CLEAR_THRESHOLD:
            state.pop(key, None)
            continue

        if dist <= ALERT_THRESHOLD and key not in state:
            if dist < 0:
                label = f"🔴 跌破 {n}MA（{dist:+.2f}%）"
            else:
                label = f"⚠️ 接近 {n}MA（剩 {dist:.2f}%）"
            alerts.append(f"  {label}  現價 {current:.1f} / MA {ma_val:.1f}")
            state[key] = {
                "at": datetime.now().strftime("%H:%M"),
                "price": current,
                "ma": ma_val,
            }

    if alerts:
        icon = "🔴" if any("🔴" in a for a in alerts) else "⚠️"
        msg  = f"{icon} {name}（{sid}）\n" + "\n".join(alerts)
        send_telegram(msg)
        log(f"通知送出：{name} {alerts}")
    return True


def main() -> None:
    if not is_trading_hours():
        log("非交易時間，略過")
        return

    lock_f = open(LOCK_FILE, "a")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("上一輪尚未結束，略過本輪")
        lock_f.close()
        return
    try:
        watchlist = load_watchlist()
        if not watchlist:
            log("watchlist 為空或讀取失敗，跳過本輪")
            return
        state = load_state()
        total = ok = 0
        for sid, (exchange, name) in watchlist.items():
            total += 1
            try:
                if analyze(sid, name, state):
                    ok += 1
            except Exception as e:
                log(f"{name}({sid}) 發生錯誤: {e}")
        save_state(state)
        if total and ok == 0:
            log(f"本輪 {total} 檔全部取價/資料失敗")
            send_telegram(f"⚠️ ma_monitor：{total} 檔全部取價失敗，監控可能已失效（檢查 shioaji-gateway）")
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    main()
