#!/usr/bin/env python3
"""盤中均線接近/跌破監測
每 5 分鐘執行，台股交易時間 09:00-13:35
接近 1% 或跌破均線時推 Telegram
"""
import os

import json
from datetime import datetime, date, timedelta, time
from pathlib import Path

import requests
import shioaji as sj

TELEGRAM_TOKEN  = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
TELEGRAM_CHAT_ID = "7556217543"
SHIOAJI_API_KEY    = "hj7FsrPYHW9nNiHrcDB2DLHu6LhH3uYvjpR2NdK23E9"
SHIOAJI_SECRET_KEY = "A8CRXZEvWePQgvdZdmCUjzNWwP4xtLf7AdzYE8Cz3Vig"
STATE_FILE = Path(__file__).parent / "ma_monitor_state.json"
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


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_trading_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
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


def login_shioaji() -> sj.Shioaji:
    api = sj.Shioaji(simulation=False)
    api.login(api_key=SHIOAJI_API_KEY, secret_key=SHIOAJI_SECRET_KEY)
    return api


def get_contract(api: sj.Shioaji, sid: str, exchange: str):
    store = api.Contracts.Stocks.TSE if exchange == "TSE" else api.Contracts.Stocks.OTC
    return store.get(sid)


def get_daily_closes(api: sj.Shioaji, contract, days: int = 30) -> list[float]:
    """取最近 days 個交易日收盤價（最多抓 60 天日曆日確保足夠筆數）。"""
    end   = date.today()
    start = end - timedelta(days=max(days * 2, 60))
    try:
        kbars = api.kbars(
            contract=contract,
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
        )
        closes = [float(c) for c in kbars.Close if c]
        return closes
    except Exception as e:
        log(f"kbars 取得失敗 {contract.code}: {e}")
        return []


def get_current_price(api: sj.Shioaji, contract) -> float | None:
    try:
        snaps = api.snapshots([contract])
        if snaps:
            return float(snaps[0].close)
    except Exception as e:
        log(f"snapshot 取得失敗 {contract.code}: {e}")
    return None


def analyze(api: sj.Shioaji, sid: str, exchange: str, name: str, state: dict) -> None:
    contract = get_contract(api, sid, exchange)
    if contract is None:
        log(f"{name}({sid}): 找不到合約")
        return

    closes = get_daily_closes(api, contract, days=25)
    if len(closes) < 20:
        log(f"{name}: 歷史資料不足（{len(closes)} 筆）")
        return

    current = get_current_price(api, contract)
    if current is None:
        current = closes[-1]

    alerts = []
    for n in MA_PERIODS:
        if len(closes) < n:
            continue
        ma_val = sum(closes[-n:]) / n
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

    try:
        api = login_shioaji()
    except Exception as e:
        log(f"Shioaji 登入失敗: {e}")
        return

    state = load_state()
    for sid, (exchange, name) in STOCKS.items():
        try:
            analyze(api, sid, exchange, name, state)
        except Exception as e:
            log(f"{name}({sid}) 發生錯誤: {e}")
    save_state(state)

    try:
        api.logout()
    except Exception:
        pass


if __name__ == "__main__":
    main()
