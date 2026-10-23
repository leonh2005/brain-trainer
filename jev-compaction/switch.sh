#!/bin/bash
# 手動切換 ccr 路由：Abacus / DeepSeek / 自動
# 用法：./switch.sh status | ds | abacus | auto
set -e
DIR="$HOME/.claude-code-router"
PIN="$DIR/route-pin.json"

write_pin() {
  local tmp="$PIN.tmp"
  printf '{"provider":"%s"}\n' "$1" > "$tmp"
  mv "$tmp" "$PIN"
}

case "${1:-status}" in
  ds|deepseek) write_pin deepseek; echo "已釘住 DeepSeek（直連），下一則訊息起生效" ;;
  abacus)      write_pin abacus;   echo "已釘住 Abacus，下一則訊息起生效（額度用完時 8791 仍會自動改走 DeepSeek 單次）" ;;
  auto)        rm -f "$PIN";       echo "已恢復自動切換" ;;
  status)      ;;
  *) echo "用法: $0 status | ds | abacus | auto"; exit 1 ;;
esac

echo "釘選：$(cat "$PIN" 2>/dev/null || echo '無（自動）')"
for f in abacus-health.json ds-health.json; do
  [ -f "$DIR/$f" ] && echo "冷卻中：$f $(cat "$DIR/$f")"
done
exit 0
