#!/bin/bash
# 狀態列：先顯示 claude-hud，再換行顯示 ccr 實際路由的模型
input=$(cat)

HUD_DIR=$(ls -d "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/plugins/cache/claude-hud/claude-hud/*/ 2>/dev/null \
  | awk -F/ '{ print $(NF-1) "\t" $0 }' | sort -t. -k1,1n -k2,2n -k3,3n -k4,4n | tail -1 | cut -f2-)

if [ -n "$HUD_DIR" ]; then
  printf '%s' "$input" | /opt/homebrew/bin/node "${HUD_DIR}dist/index.js"
  echo
fi

printf '%s' "$input" | /opt/homebrew/bin/node "$HOME/CCProject/statusline/route-status.js"
