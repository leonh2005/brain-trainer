#!/bin/bash
cd "$(dirname "$0")" || exit 1

# tutor 以 `--restricted` 呼叫 Claude CLI，會略過 ~/.claude/settings.json 的**整個**
# env 區塊（15 個鍵，只以 --settings 補回 apiKeyHelper），而 LaunchAgent 也不繼承
# shell 環境。這裡只還原目前確知必要的那一個：
#   - 少了 ANTHROPIC_BASE_URL，子行程會拿 ccr 的 key 去打 api.anthropic.com，
#     回 "Invalid API key"，智識地圖一律生成失敗（已實測）。
#   - 已知殘留差異：少了 ANTHROPIC_MODEL，服務請求的模型名與互動 session 不同
#     （實測 service=claude-opus-5-5[1m] vs interactive=deepseek/deepseek-flash[1m]，
#     兩者皆 200 且輸出正常）。補上 ANTHROPIC_MODEL 可對齊（已實測），但尚未決定
#     是否跟進，故不在此擅自擴大還原範圍。
export ANTHROPIC_BASE_URL=http://127.0.0.1:3456

exec ./venv/bin/python -c "
from app import create_app
create_app().run(host='127.0.0.1', port=5990, debug=False, threaded=True)
"
