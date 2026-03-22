#!/bin/bash
cd /Users/steven/CCProject
/Users/steven/.local/bin/claude -p "請幫我執行以下任務：
1. SSH 到 Oracle VM (ubuntu@161.33.6.190，key: ~/.ssh/oracle_line_bot)，讀取 ~/line-claude-bot/data/voice_ideas.json
2. 找出所有 processed: false 的想法
3. 如果有未處理的想法，用你自己的能力分析每一則（摘要、可行性、建議下一步）
4. 用 GMAIL 寄報告到 leonh2005@gmail.com（GMAIL_APP_PASSWORD 在 VM 的 ~/line-claude-bot/.env）
5. 把已處理的想法標記為 processed: true 並寫回 JSON
如果沒有未處理的想法，就不需要做任何事。" --allowedTools "Bash" 2>&1 >> /tmp/voice_ideas_report.log
