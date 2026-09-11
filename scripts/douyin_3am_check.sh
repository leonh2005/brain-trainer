#!/bin/bash
# 一次性任務：凌晨3點比對抖音網路狀況是否比白天好，靜音執行（不推播通知）
LOG=/Users/steven/CCProject/logs/douyin_3am_check.log
{
  echo "===== 抖音凌晨3點診斷報告 $(date '+%Y-%m-%d %H:%M:%S') ====="
  echo ""
  echo "--- 1. Ping 延遲（台灣直連 douyin.com）---"
  ping -c 5 -t 6 douyin.com
  echo ""
  echo "--- 2. 下載速度測試（douyin.com 首頁）---"
  curl -o /dev/null -s -w "Time:%{time_total}s Speed:%{speed_download} bytes/s\n" --max-time 15 https://www.douyin.com/
  echo ""
  echo "--- 3. 對照組：YouTube 下載速度 ---"
  curl -o /dev/null -s -w "Time:%{time_total}s Speed:%{speed_download} bytes/s\n" --max-time 15 https://www.youtube.com/
  echo ""
  echo "--- 4. 系統當下資源狀態 ---"
  echo "記憶體："
  vm_stat | head -6
  echo ""
  echo "CPU/負載："
  top -l 1 -n 5 -o cpu | tail -10
  echo ""
  echo "--- 5. 白天已測基準值（供比對）---"
  echo "白天(尖峰)：Ping 130ms / 下載 236KB/s（douyin） vs YouTube 2.9MB/s"
  echo ""
  echo "===== 報告結束 ====="
} >> "$LOG" 2>&1

# 靜默結案：不發 Telegram，只寫檔

# 一次性任務，跑完自己從 crontab 移除這一行
crontab -l 2>/dev/null | grep -v "douyin_3am_check.sh" | crontab -
