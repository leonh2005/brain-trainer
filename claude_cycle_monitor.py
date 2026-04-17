#!/usr/bin/env python3
"""Claude usage 週期監測 - 每5小時提醒 + 結束前自動同步"""

import os, time, subprocess, requests
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

        r3 = subprocess.run(['git', 'push', 'origin', 'main'], cwd=repo,
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


def handoff_to_gemma() -> str:
    """把當前工作 context 寫入 handoff 檔，用 Hermes 建立 session 繼續"""
    context_parts = []

    # 1. 讀取任務描述檔
    handoff_file = os.path.expanduser('~/CCProject/.handoff_context.md')
    if os.path.exists(handoff_file):
        task_desc = open(handoff_file).read().strip()
        if task_desc and '目前沒有進行中的任務' not in task_desc:
            context_parts.append(f"## 當前任務\n{task_desc}")

    # 2. Git 狀態
    repo = os.path.expanduser('~/CCProject')
    r = subprocess.run(['git', 'status', '--short'], cwd=repo, capture_output=True, text=True)
    if r.stdout.strip():
        context_parts.append(f"## Git 狀態（未提交）\n```\n{r.stdout.strip()}\n```")

    # 3. 近期 commits
    r = subprocess.run(['git', 'log', '--oneline', '-8'], cwd=repo, capture_output=True, text=True)
    if r.stdout.strip():
        context_parts.append(f"## 近期 Commits\n```\n{r.stdout.strip()}\n```")

    # 4. Git diff
    r = subprocess.run(['git', 'diff', 'HEAD'], cwd=repo, capture_output=True, text=True)
    if r.stdout.strip():
        diff_preview = r.stdout.strip()[:3000]
        context_parts.append(f"## 未提交的變更\n```diff\n{diff_preview}\n```")

    if not context_parts:
        return "ℹ️ 無工作 context，跳過 Gemma handoff"

    # 寫入 handoff 檔
    work_dir = os.path.expanduser('~/CCProject/gemma_work')
    os.makedirs(work_dir, exist_ok=True)
    ts = datetime.now(TZ).strftime('%Y%m%d_%H%M')
    context_file = os.path.join(work_dir, f'handoff_{ts}.md')
    with open(context_file, 'w') as f:
        f.write(f"# Claude Handoff {ts}\n\n")
        f.write('\n\n'.join(context_parts))
        f.write("\n\n---\n\n## Hermes 工作記錄\n\n（Hermes 將在此記錄進度）\n")

    # 同時更新 latest 捷徑（Hermes 會在此附加進度，Claude 重啟後讀這個）
    latest = os.path.join(work_dir, 'latest.md')
    import shutil
    shutil.copy(context_file, latest)
    # 加上提示讓 Hermes 知道要附加在哪
    with open(latest, 'a') as f:
        f.write("\n\n<!-- Hermes：請在此處附加你的進度更新 -->\n")

    # 用 Hermes 建立 session（-Q 安靜模式，不等互動）
    query = (
        f"請閱讀並分析這份工作移交文件：{context_file}\n\n"
        "分析完後：\n"
        "1. 用繁體中文說明目前做到哪裡\n"
        "2. 列出具體的下一步行動\n"
        "3. 如果你可以繼續做，請直接動手\n\n"
        "完成後把進度摘要附加到該檔案最後。"
    )

    try:
        r = subprocess.run(
            ['/Users/steven/.local/bin/hermes', 'chat', '-q', query, '--yolo', '-Q'],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, 'PATH': '/usr/local/bin:/opt/homebrew/bin:/Users/steven/.local/bin:/usr/bin:/bin'}
        )
        output = (r.stdout + r.stderr).strip()

        # 從輸出中抓 session ID（hermes 結束時會印出）
        session_id = ''
        for line in output.splitlines():
            if 'session' in line.lower() and ('id:' in line.lower() or 'session_id' in line.lower()):
                session_id = line.strip()
                break

        # 把 session ID 存起來供後續 --continue 使用
        if session_id:
            with open(os.path.join(work_dir, 'last_session.txt'), 'w') as f:
                f.write(session_id)

        preview = output[:400] + ('...' if len(output) > 400 else '')
        return f"🤖 Hermes session 建立完成\n可用 `hermes chat -c` 繼續\n\n{preview}"
    except subprocess.TimeoutExpired:
        return f"⏱️ Hermes 分析中（背景執行）\n可用 `hermes chat -c` 繼續"
    except Exception as e:
        return f"❌ Hermes handoff 失敗：{str(e)[:100]}"


def auto_update_memory() -> str:
    """用 Gemini 分析近期 git 變更，自動更新記憶檔案"""
    import google.genai as genai
    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        return "⚠️ 無 GEMINI_API_KEY，跳過記憶更新"

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

    prompt = (
        "你是 Steven 的 Claude Code 助手，負責維護記憶系統。\n"
        "以下是最近的 git commit 記錄：\n\n"
        + '\n\n'.join(logs) +
        "\n\n現有記憶索引：\n" + index +
        "\n\n請判斷這些 commit 中是否有需要更新記憶的重要事項"
        "（專案狀態、架構變更、重要決策等）。"
        "若有，直接輸出需要新增或修改的記憶內容（Markdown 格式，含 frontmatter）；"
        "若無新事項，輸出「無需更新」即可。不要輸出其他說明。"
    )

    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model='gemini-2.5-flash', contents=prompt
        )
        result = resp.text.strip()
        if '無需更新' in result:
            return "✅ 記憶無需更新"
        fname = os.path.join(memory_dir, f'auto_{datetime.now(TZ).strftime("%Y%m%d_%H%M")}.md')
        with open(fname, 'w') as f:
            f.write(result)
        return f"✅ 記憶已更新：{os.path.basename(fname)}"
    except Exception as e:
        import traceback
        print(f"[記憶更新失敗] {traceback.format_exc()}", flush=True)
        return f"⚠️ 記憶更新失敗：{str(e)}"


def run_auto_session_end():
    """自動執行登出流程：Gemma handoff → 記憶更新 → commit/push → VM rsync"""
    results = []

    # 0. 同步 Hermes 記憶
    r = subprocess.run(['/opt/homebrew/bin/python3.13', os.path.expanduser('~/CCProject/sync_hermes_memory.py')],
                       capture_output=True, text=True)
    results.append(r.stdout.strip() or f"⚠️ Hermes 記憶同步失敗：{r.stderr[:80]}")

    # 1. Gemma handoff（移交未完成任務）
    results.append(handoff_to_gemma())

    # 1. 記憶更新（Gemini API）
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
