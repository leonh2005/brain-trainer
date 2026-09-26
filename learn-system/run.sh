#!/bin/bash
cd "$(dirname "$0")" || exit 1

# tutor 以 `--restricted` 呼叫 Claude CLI，會略過 ~/.claude/settings.json 的**整個**
# env 區塊（15 個鍵，只以 --settings 補回 apiKeyHelper），而 LaunchAgent 也不繼承
# shell 環境。這裡只還原目前確知必要的鍵：
#   - ANTHROPIC_BASE_URL：少了它，子行程會拿 ccr 的 key 去打 api.anthropic.com，
#     回 "Invalid API key"，智識地圖一律生成失敗（已實測）。寫死是因為它必須
#     無條件存在——服務能否運作不該取決於讀 settings.json 成不成功。
#   - ANTHROPIC_MODEL：少了它服務不會壞，但會**靜默改用別的模型**（實測
#     service=claude-opus-5-5[1m] vs interactive=deepseek/deepseek-flash[1m]，
#     成本可能差很多）。模型名會隨 ccr 設定漂移，故從 settings.json 讀，不寫死。
export ANTHROPIC_BASE_URL=http://127.0.0.1:3456

MODEL=$("./venv/bin/python" -c "
import json, pathlib
try:
    print(json.loads((pathlib.Path.home() / '.claude' / 'settings.json').read_text())['env']['ANTHROPIC_MODEL'])
except Exception:
    pass
" 2>/dev/null)
if [ -n "$MODEL" ]; then
  export ANTHROPIC_MODEL="$MODEL"
else
  echo "warning: 讀不到 settings.json 的 env.ANTHROPIC_MODEL，將沿用 CLI 預設模型（可能與互動 session 不同）" >&2
fi

exec ./venv/bin/python -c "
from app import create_app
create_app().run(host='127.0.0.1', port=5990, debug=False, threaded=True)
"
