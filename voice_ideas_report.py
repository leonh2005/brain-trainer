#!/usr/bin/env python3
"""
每日語音想法報告
從 Oracle VM 抓取未處理的語音想法，用 claude -p 分析後寄到 Gmail
"""

import json
import subprocess
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# 設定
SSH_KEY = "/Users/steven/.ssh/oracle_line_bot"
VM_USER = "ubuntu"
VM_HOST = "161.33.6.190"
VM_IDEAS_FILE = "/home/ubuntu/line-claude-bot/data/voice_ideas.json"
GMAIL_USER = "leonh2005@gmail.com"
GMAIL_APP_PASSWORD = "hadbpswsblbcdzsk"
CLAUDE_BIN = "/Users/steven/.local/bin/claude"


def ssh_run(cmd):
    result = subprocess.run(
        ["ssh", "-i", SSH_KEY, f"{VM_USER}@{VM_HOST}", cmd],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def get_pending_ideas():
    raw = ssh_run(f"cat {VM_IDEAS_FILE}")
    if not raw:
        return [], []
    ideas = json.loads(raw)
    pending = [i for i in ideas if not i.get("processed", False)]
    return ideas, pending


def analyze_with_claude(pending):
    combined = "\n\n".join([f"[{i['time']}] {i['text']}" for i in pending])
    prompt = (
        f"以下是我今天記錄的語音想法（共 {len(pending)} 則）：\n\n{combined}\n\n"
        "請針對每一則想法提供分析，格式如下：\n\n"
        "**想法 N：**（摘要標題）\n"
        "📋 摘要：\n"
        "✅ 可行性：（優勢、挑戰、評分 1-10）\n"
        "🗓 建議下一步：（2-3 個具體行動）\n\n"
        "最後加上【整體總結】，把所有想法串連起來，提供優先順序建議。"
    )
    result = subprocess.run(
        [CLAUDE_BIN, "-p", prompt],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude 執行失敗: {result.stderr}")
    return result.stdout.strip()


def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def mark_processed(all_ideas):
    for i in all_ideas:
        i["processed"] = True
    updated = json.dumps(all_ideas, ensure_ascii=False, indent=2)
    # 用 heredoc 方式寫回 VM
    cmd = f"cat > {VM_IDEAS_FILE} << 'ENDJSON'\n{updated}\nENDJSON"
    ssh_run(cmd)


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] 開始語音想法每日分析...")

    all_ideas, pending = get_pending_ideas()

    if not pending:
        print("沒有未處理的語音想法，結束。")
        return

    print(f"找到 {len(pending)} 則待處理想法，開始分析...")

    analysis = analyze_with_claude(pending)

    today = datetime.now().strftime("%Y-%m-%d")
    combined = "\n\n".join([f"[{i['time']}] {i['text']}" for i in pending])
    subject = f"💡 {today} 語音想法每日分析（{len(pending)} 則）"
    body = (
        f"📅 {today} 語音想法彙整報告\n"
        f"共 {len(pending)} 則想法\n"
        f"{'='*50}\n\n"
        f"【原始記錄】\n{combined}\n\n"
        f"{'='*50}\n\n"
        f"【Claude 分析】\n{analysis}"
    )

    send_email(subject, body)
    print("郵件已寄出。")

    mark_processed(all_ideas)
    print("已標記為已處理。完成！")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"錯誤: {e}", file=sys.stderr)
        sys.exit(1)
