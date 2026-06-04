#!/usr/bin/env python3
"""盤中均線接近/跌破監測
每 5 分鐘執行，台股交易時間 09:00-13:35
接近 1% 或跌破均線時推 Telegram
"""

import json
from datetime import datetime, time
from pathlib import Path

import requests
import yfinance as yf

TELEGRAM_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
TELEGRAM_CHAT_ID = "7556217543"
STATE_FILE = Path(__file__).parent / "ma_monitor_state.json"
LOG_FILE   = Path(__file__).parent.parent / "logs" / "ma_monitor.log"

STOCKS = {
    "國巨":  "2327.TW",
    "正文":  "4906.TW",
    "鴻海":  "2317.TW",
    "華邦電": "2344.TW",
    "光寶科": "2301.TW",
    "光洋科": "1785.TWO",
}

MA_PERIODS       = [5, 10, 20]
ALERT_THRESHOLD  = 1.0   # 距均線 ≤1% 就通知
CLEAR_THRESHOLD  = 2.0   # 距均線 >2% 才解除通知狀態


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def is_trading_hours():
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return time(9, 0) <= t <= time(13, 35)


def send_telegram(msg):
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


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def analyze(name, ticker_str, state):
    # 歷史日線（3個月，用於計算 MA）
    hist = yf.download(ticker_str, period="3mo", interval="1d",
                       progress=False, auto_adjust=True, multi_level_column=False)
    if hist.empty or len(hist) < 20:
        log(f"{name}: 歷史資料不足")
        return

    closes = hist["Close"].dropna()

    # 盤中即時價（5分K最新一根）
    intra = yf.download(ticker_str, period="1d", interval="5m",
                        progress=False, auto_adjust=True, multi_level_column=False)
    if not intra.empty:
        current = float(intra["Close"].dropna().iloc[-1])
    else:
        current = float(closes.iloc[-1])

    alerts = []
    for n in MA_PERIODS:
        if len(closes) < n:
            continue
        ma_val = float(closes.iloc[-n:].mean())
        key = f"{name}_{n}MA"
        dist = (current - ma_val) / ma_val * 100  # 正=在線上，負=跌破

        if dist > CLEAR_THRESHOLD:
            state.pop(key, None)
            continue

        if dist <= ALERT_THRESHOLD and key not in state:
            if dist < 0:
                label = f"🔴 跌破 {n}MA（{dist:+.2f}%）"
            else:
                label = f"⚠️ 接近 {n}MA（剩 {dist:.2f}%）"
            alerts.append(f"  {label}  現價 {current:.1f} / MA {ma_val:.1f}")
            state[key] = {"at": datetime.now().strftime("%H:%M"), "price": current, "ma": ma_val}

    if alerts:
        icon = "🔴" if any("🔴" in a for a in alerts) else "⚠️"
        msg = f"{icon} {name}（{ticker_str.replace('.TW','')}）\n" + "\n".join(alerts)
        send_telegram(msg)
        log(f"通知送出：{name} {alerts}")


def main():
    if not is_trading_hours():
        log("非交易時間，略過")
        return

    state = load_state()
    for name, ticker in STOCKS.items():
        try:
            analyze(name, ticker, state)
        except Exception as e:
            log(f"{name} 發生錯誤: {e}")
    save_state(state)


if __name__ == "__main__":
    main()
