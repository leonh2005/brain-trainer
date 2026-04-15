#!/bin/bash
# Omi 自動右滑腳本
# 用法：
#   啟動：./omi-swipe.sh start [間隔秒數，預設0.5]
#   停止：./omi-swipe.sh stop

FLAG=/tmp/omi_swipe_run
LOG=/tmp/omi_swipe.log
INTERVAL=${2:-0.5}

case "$1" in
  start)
    if [ -f "$FLAG" ]; then
      echo "已在執行中，先執行 stop"
      exit 1
    fi

    # 確認 ADB 連線
    if ! adb devices | grep -q "device$"; then
      echo "找不到 ADB 裝置，請先執行："
      echo "  adb connect 192.168.68.104:5555"
      exit 1
    fi

    # 喚醒螢幕
    adb shell input keyevent KEYCODE_WAKEUP

    touch "$FLAG"
    nohup bash -c "while [ -f $FLAG ]; do adb shell input swipe 300 1000 900 1000 200; sleep $INTERVAL; done" > "$LOG" 2>&1 &
    echo "✓ 啟動（每 ${INTERVAL}s 右滑一次），PID: $!"
    echo "  停止：$0 stop"
    ;;

  stop)
    rm -f "$FLAG" /tmp/omi_heart_run
    pkill -f "input swipe" 2>/dev/null
    pkill -f "omi_swipe_run\|omi_heart_run" 2>/dev/null
    echo "✓ 已停止"
    ;;

  *)
    echo "用法：$0 {start|stop} [間隔秒數]"
    echo "範例："
    echo "  $0 start       # 預設 0.5 秒"
    echo "  $0 start 1     # 1 秒一滑"
    echo "  $0 stop"
    ;;
esac
