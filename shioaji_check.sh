#!/bin/bash
cd /Users/steven/CCProject/daytrade-replay
RESULT=$(venv/bin/python3 -c "
import data
bars = data._sj_stock_1min('2330', '$(date +%Y-%m-%d)')
print(len(bars))
" 2>/dev/null)

TOKEN="8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID="7556217543"

if [ "$RESULT" -gt 0 ] 2>/dev/null; then
  MSG="✅ 永豐 Shioaji API 已恢復正常（2330 今日 ${RESULT} 根 K 棒）"
else
  MSG="⚠️ 永豐 Shioaji API 仍無資料（bars=0），請聯絡客服解除停用（帳號 ID: N124711691）"
fi

curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  -d "chat_id=${CHAT_ID}&text=${MSG}" > /dev/null
