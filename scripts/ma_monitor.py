#!/usr/bin/env python3
"""盤中均線接近/跌破監測
每 5 分鐘執行，台股交易時間 09:00-13:35
接近 1% 或跌破均線時推 Telegram
"""
import os

import fcntl
import json
from datetime import datetime, time
from pathlib import Path

import requests

TELEGRAM_TOKEN  = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
TELEGRAM_CHAT_ID = "7556217543"
GATEWAY = "http://127.0.0.1:5455"   # shioaji-gateway：共用單一 Shioaji 連線
STATE_FILE = Path(__file__).parent / "ma_monitor_state.json"
LOCK_FILE  = Path(__file__).parent / "ma_monitor.lock"
LOG_FILE   = Path(__file__).parent.parent / "logs" / "ma_monitor.log"

# stock_id → (exchange, name)
STOCKS = {
    "2327": ("TSE", "國巨"),
    "4906": ("TSE", "正文"),
    "2317": ("TSE", "鴻海"),
    "2344": ("TSE", "華邦電"),
    "2301": ("TSE", "光寶科"),
    "1785": ("OTC", "光洋科"),
}

MA_PERIODS      = [5, 10, 20]
ALERT_THRESHOLD = 1.0   # 距均線 ≤1% 就通知
CLEAR_THRESHOLD = 2.0   # 距均線 >2% 才解除通知狀態
MAX_MA          = max(MA_PERIODS)
HISTORY_DAYS    = MAX_MA + 1 + 10   # 需 MAX_MA 根已收盤日K + 今日，再留緩衝

# 2026 台灣國定假日（市場休市）。假日 gateway 會用 yfinance 補一根幽靈日K，
# 必須擋掉才不會產生假訊號。與 daytrade-replay/data.py 的 _TW_HOLIDAYS 同步。
TW_HOLIDAYS = {
    "2026-01-01", "2026-01-26", "2026-01-27", "2026-01-28",
    "2026-01-29", "2026-01-30", "2026-02-28", "2026-04-03",
    "2026-04-04", "2026-04-05", "2026-05-01", "2026-06-19",
    "2026-09-04", "2026-10-09", "2026-10-10",
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
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram 發送失敗: {e}")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def gw_closes(sid: str, days: int = 30) -> list[float]:
    """向 gateway 取「日K」收盤序列（/daily_ohlcv，非分K）。"""
    try:
        j = requests.get(f"{GATEWAY}/daily_ohlcv", params={"code": sid, "days": days}, timeout=30).json()
        if not j.get("ok"):
            log(f"gateway daily_ohlcv 非 ok {sid}: {j.get('error')}")
            return []
        return [float(b["close"]) for b in j.get("bars", [])]
    except Exception as e:
        log(f"gateway daily_ohlcv 失敗 {sid}: {e}")
        return []


def gw_price(sid: str) -> float | None:
    """向 gateway 取現價(snapshot close)。"""
    try:
        j = requests.get(f"{GATEWAY}/snapshot", params={"codes": sid}, timeout=15).json()
        if j.get("ok") and sid in j.get("data", {}):
            return float(j["data"][sid]["close"])
    except Exception as e:
        log(f"gateway snapshot 失敗 {sid}: {e}")
    return None


def analyze(sid: str, name: str, state: dict) -> None:
    closes = gw_closes(sid, days=HISTORY_DAYS)
    # 最後一根是 gateway 補的「今日」（盤中可能來自 yfinance）；現價改用 Shioaji
    # snapshot，故 MA 只取已收盤的歷史日K，兩者同源、不混用
    hist = closes[:-1]
    if len(hist) < MAX_MA:
        log(f"{name}: 歷史資料不足（{len(hist)} 筆）")
        return

    current = gw_price(sid)
    if current is None:
        current = closes[-1]

    alerts = []
    for n in MA_PERIODS:
        if len(hist) < n:
            continue
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


def main() -> None:
    if not is_trading_hours():
        log("非交易時間，略過")
        return

    lock_f = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("上一輪尚未結束，略過本輪")
        lock_f.close()
        return
    try:
        state = load_state()
        for sid, (exchange, name) in STOCKS.items():
            try:
                analyze(sid, name, state)
            except Exception as e:
                log(f"{name}({sid}) 發生錯誤: {e}")
        save_state(state)
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    main()
