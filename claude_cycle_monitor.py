#!/usr/bin/env python3
"""Claude usage 週期監測 - 每5小時提醒 + 結束前自動同步"""

import os, time, subprocess, requests, anthropic
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv(os.path.expanduser('~/youtube-monitor/.env'))

TZ = ZoneInfo('Asia/Taipei')
CYCLE = 5 * 3600  # 5小時

# 參考重置點（2026-03-28 21:00，之後每5小時遞推）
REFERENCE = datetime(2026, 3, 28, 21, 0, 0, tzinfo=TZ)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk')
CHAT_ID   = os.getenv('TELEGRAM_CHAT_ID', '7556217543')

REPOS = [
    os.path.expanduser('~/CCProject'),
    os.path.expanduser('~/youtube-monitor'),
]
VM_USER = 'ubuntu'
VM_HOST = '161.33.6.190'
VM_KEY  = os.path.expanduser('~/.ssh/oracle_line_bot')


def send_telegram(msg: str):
    try:
        requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
            json={'chat_id': CHAT_ID, 'text': msg},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram 失敗: {e}")


def get_cycle_times(now: datetime):
    elapsed = (now - REFERENCE).total_seconds() % CYCLE
    cycle_start = now - timedelta(seconds=elapsed)
    midpoint   = cycle_start + timedelta(hours=2, minutes=30)
    end_warn   = cycle_start + timedelta(hours=4, minutes=40)
    next_reset = cycle_start + timedelta(hours=5)
    return midpoint, end_warn, next_reset


def check_services() -> str:
    lines = []
    r = subprocess.run(['pgrep', '-f', 'youtube_monitor.py'], capture_output=True)
    lines.append("✅ youtube-monitor 運行中" if r.returncode == 0 else "❌ youtube-monitor 未運行")

    today = datetime.now(TZ).strftime('%Y-%m-%d')
    summary_dir = os.path.expanduser(f'~/youtube-monitor/summaries/{today}')
    if os.path.exists(summary_dir):
        count = len([f for f in os.listdir(summary_dir)
                     if f.endswith('.txt') and not f.endswith('.transcript.txt')])
        lines.append(f"📄 今日摘要：{count} 支")

    try:
        r = subprocess.run(
            ['ssh', '-i', VM_KEY, '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes',
             f'{VM_USER}@{VM_HOST}', 'echo ok'],
            capture_output=True, timeout=10
        )
        lines.append("✅ Oracle VM 正常" if r.returncode == 0 else "❌ Oracle VM 無回應")
    except Exception:
        lines.append("❌ Oracle VM 連線失敗")

    return '\n'.join(lines)


def git_commit_push(repo: str) -> str:
    """自動 commit 所有變更並 push"""
    name = os.path.basename(repo)
    try:
        # 有無 uncommitted 變更
        r = subprocess.run(['git', 'status', '--porcelain'], cwd=repo,
                           capture_output=True, text=True)
        changed = r.stdout.strip()
        if not changed:
            # 檢查是否有 unpushed commits
            r2 = subprocess.run(['git', 'log', '@{u}..HEAD', '--oneline'],
                                cwd=repo, capture_output=True, text=True)
            if not r2.stdout.strip():
                return f"✅ {name}：無變更"

        if changed:
            ts = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
            subprocess.run(['git', 'add', '-A'], cwd=repo, check=True)
            subprocess.run(
                ['git', 'commit', '-m', f'chore: 自動同步 {ts}'],
                cwd=repo, check=True, capture_output=True
            )

        r3 = subprocess.run(['git', 'push'], cwd=repo,
                            capture_output=True, text=True)
        if r3.returncode == 0:
            return f"✅ {name}：push 完成"
        else:
            return f"⚠️ {name}：push 失敗 {r3.stderr.strip()[:80]}"
    except Exception as e:
        return f"❌ {name}：{str(e)[:80]}"


def rsync_vm() -> str:
    """同步 summaries 到 VM"""
    try:
        r = subprocess.run([
            'rsync', '-az', '--delete',
            '-e', f'ssh -i {VM_KEY} -o StrictHostKeyChecking=no',
            os.path.expanduser('~/youtube-monitor/summaries/'),
            f'{VM_USER}@{VM_HOST}:/home/ubuntu/summaries/'
        ], capture_output=True, timeout=60)
        return "✅ VM rsync 完成" if r.returncode == 0 else f"⚠️ rsync 失敗"
    except Exception as e:
        return f"❌ rsync 例外：{str(e)[:60]}"


def auto_update_memory() -> str:
    """用 Claude API 分析近期 git 變更，自動更新記憶檔案"""
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return "⚠️ 無 ANTHROPIC_API_KEY，跳過記憶更新"

    # 收集最近兩個 repo 的 git log
    logs = []
    for repo in REPOS:
        name = os.path.basename(repo)
        r = subprocess.run(
            ['git', 'log', '--oneline', '-10', '--no-merges'],
            cwd=repo, capture_output=True, text=True
        )
        if r.stdout.strip():
            logs.append(f"[{name}]\n{r.stdout.strip()}")

    if not logs:
        return "ℹ️ 無近期 commit，跳過記憶更新"

    memory_dir = os.path.expanduser(
        '~/.claude/projects/-Users-steven-CCProject/memory/'
    )
    try:
        index = open(os.path.join(memory_dir, 'MEMORY.md')).read()
    except Exception:
        index = ''

    client = anthropic.Anthropic(api_key=api_key)
    prompt = (
        "你是 Steven 的 Claude Code 助手，負責維護記憶系統。\n"
        "以下是最近的 git commit 記錄：\n\n"
        + '\n\n'.join(logs) +
        "\n\n現有記憶索引：\n" + index +
        "\n\n請判斷這些 commit 中是否有需要更新記憶的重要事項（專案狀態、架構變更、重要決策等）。"
        "若有，直接輸出需要新增或修改的記憶內容（Markdown 格式，含 frontmatter）；"
        "若無新事項，輸出「無需更新」即可。不要輸出其他說明。"
    )

    try:
        resp = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1000,
            messages=[{'role': 'user', 'content': prompt}]
        )
        result = resp.content[0].text.strip()
        if result == '無需更新':
            return "✅ 記憶無需更新"
        # 寫入記憶檔案
        fname = os.path.join(memory_dir, f'auto_{datetime.now(TZ).strftime("%Y%m%d_%H%M")}.md')
        with open(fname, 'w') as f:
            f.write(result)
        return f"✅ 記憶已更新：{os.path.basename(fname)}"
    except Exception as e:
        return f"⚠️ Claude API 記憶更新失敗：{str(e)[:60]}"


def run_auto_session_end():
    """自動執行登出流程：記憶更新 → commit/push → VM rsync"""
    results = []

    # 1. 記憶更新（Claude API）
    results.append(auto_update_memory())

    # 2. Commit & push 各 repo
    for repo in REPOS:
        results.append(git_commit_push(repo))

    # 3. VM rsync
    results.append(rsync_vm())

    return '\n'.join(results)


def main():
    print(f"[{datetime.now(TZ).strftime('%H:%M')}] Claude 週期監測啟動")

    while True:
        now = datetime.now(TZ)
        midpoint, end_warn, next_reset = get_cycle_times(now)

        if now < midpoint - timedelta(seconds=30):
            target, action = midpoint, 'midpoint'
        elif now < end_warn - timedelta(seconds=30):
            target, action = end_warn, 'end_warn'
        else:
            target, action = midpoint + timedelta(hours=5), 'midpoint'

        wait = (target - now).total_seconds()
        print(f"[{now.strftime('%H:%M')}] 下一事件：{action} @ {target.strftime('%H:%M')}（{wait/60:.0f} 分鐘後）")
        time.sleep(max(wait, 1))

        now = datetime.now(TZ)
        _, _, next_reset = get_cycle_times(now)

        if action == 'midpoint':
            remaining_min = int((next_reset - now).total_seconds() / 60)
            send_telegram(
                f"⏰ Claude Usage 中點提醒\n"
                f"距重置還有 {remaining_min // 60}h{remaining_min % 60:02d}m\n"
                f"重置時間：{next_reset.strftime('%H:%M')}"
            )

        elif action == 'end_warn':
            send_telegram(f"🔄 Claude Usage 剩 20 分鐘，開始自動同步...")
            sync_result = run_auto_session_end()
            service_status = check_services()
            send_telegram(
                f"✅ 自動同步完成（重置：{next_reset.strftime('%H:%M')}）\n\n"
                f"【同步結果】\n{sync_result}\n\n"
                f"【服務狀態】\n{service_status}"
            )
            print(f"[{now.strftime('%H:%M')}] 自動同步完成")

        time.sleep(60)


if __name__ == '__main__':
    main()
