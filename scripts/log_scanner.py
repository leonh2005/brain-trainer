#!/usr/bin/env python3
"""
每日 03:00 掃描所有服務 log，找 ERROR/Traceback/失敗
每日 14:00 把昨晚報告推播 Telegram 並附解決方案建議
用法：
  python3 log_scanner.py          # 掃描並存檔（03:00 cron 用）
  python3 log_scanner.py --report # 推播昨晚報告（14:00 cron 用）
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
REPORT_FILE = HOME / "CCProject/logs/log_scan_report.json"
BOT_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
CHAT_ID = "7556217543"

# ── 服務 → log 路徑清單 ──────────────────────────────────────────────────────
SERVICES = {
    "市場儀表板":      [HOME / "CCProject/logs/market-dashboard.log"],
    "兔子照護":        [HOME / "CCProject/rabbit-care/rabbit-care.log"],
    "motion-watcher":  [HOME / "CCProject/rabbit-care/motion-watcher.log"],
    "Cloudflare Tunnel":[HOME / "CCProject/rabbit-care/tunnel.log"],
    "財經新聞分析":    [HOME / "CCProject/news-analyzer/pipeline.log",
                       HOME / "CCProject/news-analyzer/flask.log"],
    "川普監測":        [HOME / "CCProject/news-analyzer/trump_monitor.log"],
    "持倉新聞(台股)":  [HOME / "CCProject/portfolio-news/tw.log"],
    "持倉新聞(美股)":  [HOME / "CCProject/portfolio-news/us.log"],
    "HolaQuant":       [HOME / "CCProject/hola-quant/hola-quant.log"],
    "凱利倉位計算":    [HOME / "CCProject/kelly-fibonacci/server.log"],
    "選股AI":          [HOME / "CCProject/stock-screener-ai/screener-ai.log"],
    "當沖回測":        [HOME / "CCProject/logs/daytrade-replay.log"],
    "當沖推播":        [HOME / "CCProject/logs/daytrade.log"],
    "隔日沖推播":      [HOME / "CCProject/logs/swing.log"],
    "盤中監控":        [HOME / "CCProject/logs/intraday_monitor.log"],
    "蝦皮軟纖監測":    [HOME / "CCProject/logs/shopee_stock.log",
                       HOME / "CCProject/logs/shopee_keepalive.log"],
    "自動簽到":        [HOME / "CCProject/logs/94i_signin.log",
                       HOME / "CCProject/logs/pressplay_signin.log"],
    "夜間健康檢查":    [HOME / "CCProject/logs/nightly_check.log"],
    "Claude週期監測":  [HOME / "CCProject/claude_cycle_monitor.log"],
    "Threads每日":     [HOME / "CCProject/threads-daily/cron.log"],
    # 服務已改成 stock_analysis_YYYYMMDD.log（檔名在啟動時固定，靠檔案大小輪替非跨日），
    # app.log 是舊命名、5/22後已不再寫入，改用 glob 抓最新修改時間的檔案
    "daily-stock-analysis": [HOME / "CCProject/daily-stock-analysis/logs/stock_analysis_*.log"],
}

# 沒有 log 的服務（僅標記）
NO_LOG_SERVICES = ["youtube-monitor（LaunchAgent stdout 未重導向）"]

ERROR_PATTERNS = re.compile(
    r"(ERROR|Traceback|Exception|FAILED|失敗|錯誤|error|critical|CRITICAL|abort)",
    re.IGNORECASE,
)

# ctime 風格＋時區縮寫（如 Cloudflare Tunnel 的 [Sat May 23 09:13:13 CST 2026]）
CTIME_TZ_RE = re.compile(r"\w{3} (\w{3} +\d{1,2} \d{2}:\d{2}:\d{2}) \w+ (\d{4})")

# 已知錯誤 → 建議解法
KNOWN_FIXES = [
    (re.compile(r"geckodriver.*not.*compatible|unable.*locate.*element", re.I),
     "Selenium selector 失效（蝦皮改版）→ 更新 geckodriver 或調整 XPATH"),
    (re.compile(r"session.*expired|401|403", re.I),
     "Session/Token 過期 → 重新登入對應網站或更新 API Key"),
    (re.compile(r"Connection.*refused|port.*use|address.*use", re.I),
     "Port 被佔用或服務未啟動 → 檢查 LaunchAgent 是否正常載入"),
    (re.compile(r"No such file|FileNotFoundError|ModuleNotFoundError", re.I),
     "檔案或套件遺失 → 確認路徑或重新安裝 pip 套件"),
    (re.compile(r"timeout|timed out|ReadTimeout", re.I),
     "網路逾時 → 確認目標服務可達，或增加 timeout 設定"),
    (re.compile(r"ImportError|cannot import", re.I),
     "Python import 失敗 → 確認 venv 是否啟用且套件已安裝"),
    (re.compile(r"OSError.*Too many open files", re.I),
     "FD 上限 → LaunchAgent 加入 SoftResourceLimits NumberOfFiles: 4096"),
    (re.compile(r"disk.*full|no space|ENOSPC", re.I),
     "磁碟已滿 → 清理舊 log 或刪除暫存檔"),
]


def scan_log(path: Path, since: datetime) -> dict:
    """掃描單一 log 檔，回傳錯誤統計與摘要"""
    if not path.exists():
        return {"exists": False, "errors": 0, "last_error": None, "fix": None}

    errors = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except Exception as e:
        return {"exists": True, "errors": 0, "last_error": f"無法讀取：{e}", "fix": None}

    last_ts = None  # 延續上一筆解析成功的時間戳，供無時間戳的接續行（如 Traceback 內文）使用
    for line in lines:
        # 嘗試從行首取時間戳（多種格式）
        ts = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "[%Y-%m-%d %H:%M:%S]"):
            try:
                ts = datetime.strptime(line[:len(fmt) + 2].strip("[] "), fmt.strip("[]"))
                break
            except ValueError:
                pass

        if ts is None:
            m = CTIME_TZ_RE.search(line)
            if m:
                try:
                    ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%b %d %H:%M:%S %Y")
                except ValueError:
                    pass

        if ts is not None:
            last_ts = ts
        else:
            ts = last_ts  # 沒有自己的時間戳，沿用上一筆有時間戳的行（同一段輸出視為同時間）

        if ts is None:
            continue  # 從檔案開頭到第一個有效時間戳之間，無從判斷新舊，一律不計入
        if ts < since:
            continue  # 24 小時以外的忽略

        if ERROR_PATTERNS.search(line):
            errors.append(line.strip())

    if not errors:
        return {"exists": True, "errors": 0, "last_error": None, "fix": None}

    last = errors[-1][:300]
    fix = next(
        (suggestion for pattern, suggestion in KNOWN_FIXES if pattern.search(last)),
        "尚無對應建議，需人工判斷"
    )
    return {"exists": True, "errors": len(errors), "last_error": last, "fix": fix}


def _resolve_path(p: Path) -> Path:
    """路徑含 * 視為 glob pattern，回傳最新修改時間的檔案；否則原樣回傳"""
    s = str(p)
    if "*" not in s:
        return p
    matches = sorted(Path(s).parent.glob(Path(s).name), key=lambda f: f.stat().st_mtime, reverse=True)
    return matches[0] if matches else Path(s)


def scan_all() -> dict:
    since = datetime.now() - timedelta(hours=24)
    results = {}
    for service, paths in SERVICES.items():
        merged = {"exists": False, "errors": 0, "last_error": None, "fix": None}
        for p in paths:
            r = scan_log(_resolve_path(p), since)
            if r["exists"]:
                merged["exists"] = True
                merged["errors"] += r["errors"]
                if r["last_error"]:
                    merged["last_error"] = r["last_error"]
                    merged["fix"] = r["fix"]
        results[service] = merged

    return {
        "scanned_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "since": since.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
        "no_log": NO_LOG_SERVICES,
    }


def send_telegram(text: str):
    # Telegram 單訊息上限 4096 字，超過則切段
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        subprocess.run([
            "curl", "-s", "-X", "POST",
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            "-d", f"chat_id={CHAT_ID}",
            "--data-urlencode", f"text={chunk}",
        ], capture_output=True)


def build_report(data: dict) -> str:
    results = data["results"]
    scanned_at = data["scanned_at"]
    since = data["since"]

    error_services = {k: v for k, v in results.items() if v["errors"] > 0}
    missing_log    = {k: v for k, v in results.items() if not v["exists"]}
    clean_services = {k: v for k, v in results.items() if v["exists"] and v["errors"] == 0}

    lines = [
        f"📋 每日 Log 掃描報告",
        f"掃描時間：{scanned_at}",
        f"涵蓋範圍：{since} 後",
        "",
    ]

    if error_services:
        lines.append(f"🔴 發現錯誤（{len(error_services)} 個服務）")
        for svc, info in error_services.items():
            lines.append(f"\n【{svc}】錯誤 {info['errors']} 筆")
            lines.append(f"  最近：{info['last_error'][:150]}")
            lines.append(f"  建議：{info['fix']}")
    else:
        lines.append("✅ 所有服務無錯誤")

    if missing_log:
        lines.append(f"\n⚪ Log 不存在（{len(missing_log)} 個，可能從未執行）")
        for svc in missing_log:
            lines.append(f"  · {svc}")

    lines.append(f"\n🟢 正常：{len(clean_services)} 個服務")
    lines.append("\n以上發現問題請回覆是否需要處理。")

    return "\n".join(lines)


def main():
    if "--report" in sys.argv:
        if not REPORT_FILE.exists():
            send_telegram("⚠️ Log 掃描報告不存在，03:00 掃描可能未執行。")
            return
        data = json.loads(REPORT_FILE.read_text())
        report = build_report(data)
        send_telegram(report)
        print("報告已推播")
    else:
        print(f"[{datetime.now():%H:%M:%S}] 開始掃描 log...")
        data = scan_all()
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        error_count = sum(1 for v in data["results"].values() if v["errors"] > 0)
        print(f"[{datetime.now():%H:%M:%S}] 掃描完成，{error_count} 個服務有錯誤 → {REPORT_FILE}")


if __name__ == "__main__":
    main()
