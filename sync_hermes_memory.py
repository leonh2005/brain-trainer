#!/usr/bin/env python3
"""把 Claude 記憶同步到 Hermes MEMORY.md"""

import os
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')
MEMORY_DIR = os.path.expanduser('~/.claude/projects/-Users-steven-CCProject/memory')
HERMES_MEMORY = os.path.expanduser('~/.hermes/MEMORY.md')

def read_memory_file(fname: str) -> str:
    path = os.path.join(MEMORY_DIR, fname)
    if not os.path.exists(path):
        return ''
    content = open(path).read()
    # 移除 frontmatter
    lines = content.splitlines()
    if lines and lines[0] == '---':
        try:
            end = lines.index('---', 1)
            lines = lines[end+1:]
        except ValueError:
            pass
    return '\n'.join(lines).strip()

def build_memory():
    ts = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
    sections = [f"# Steven 的助理知識庫\n\n> 此檔案由 Claude Code 自動同步。最後更新：{ts}\n"]

    # User
    user = read_memory_file('user_steven.md')
    if user:
        sections.append(f"## 關於 Steven\n\n{user}")

    # Feedback
    for fname, title in [
        ('feedback_preferences.md', '行為偏好'),
        ('feedback_shioaji_only.md', '行情資料規則'),
        ('feedback_browser_login.md', '瀏覽器登入規則'),
    ]:
        content = read_memory_file(fname)
        if content:
            sections.append(f"## {title}\n\n{content}")

    # Projects
    project_files = [
        ('project_line_bot.md', 'Telebot（Oracle VM）'),
        ('project_rabbit_care.md', '兔子照護系統'),
        ('project_stock_tools.md', '台股工具'),
        ('project_news_analyzer.md', '財經新聞情緒儀表板'),
        ('project_market_dashboard.md', '市場恐慌儀表板'),
        ('project_steam_game.md', 'Steam 視覺小說'),
        ('project_investment_portfolio.md', '投資組合'),
        ('project_claude_automation.md', 'Claude 自動化系統'),
        ('project_nest_mini.md', 'Nest Mini'),
    ]
    for fname, title in project_files:
        content = read_memory_file(fname)
        if content:
            sections.append(f"## {title}\n\n{content}")

    return '\n\n---\n\n'.join(sections)

if __name__ == '__main__':
    content = build_memory()
    with open(HERMES_MEMORY, 'w') as f:
        f.write(content)
    print(f"✅ Hermes MEMORY.md 已同步（{len(content)} 字元）")
