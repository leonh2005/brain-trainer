#!/bin/bash
# 每天 00:00 透過無線 adb (Wireless debugging) 重開 Pixel 5，緩解長時間不重開機累積的記憶體壓力（含App推播連鎖卡死ANR）。
# 手機需與 Mac 在同一區網、開啟 Wi-Fi、Wireless debugging 已在 Developer options 開啟（一次性設定，重開機後仍持續有效）。
LOG=/Users/steven/CCProject/logs/phone_reboot.log
ADB=/Users/steven/Library/Android/sdk/platform-tools/adb

echo "$(date '+%Y-%m-%d %H:%M:%S') 開始嘗試重開手機" >> "$LOG"
"$ADB" start-server >> "$LOG" 2>&1
sleep 5

TARGET=$("$ADB" devices -l | grep "_adb-tls-connect._tcp" | awk '{print $1}')
if [ -z "$TARGET" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') 找不到無線 adb 裝置，跳過（手機可能離線或不在同一網路）" >> "$LOG"
    exit 1
fi

"$ADB" -s "$TARGET" shell reboot >> "$LOG" 2>&1
echo "$(date '+%Y-%m-%d %H:%M:%S') 已送出重開機指令：$TARGET" >> "$LOG"
