#!/bin/bash
cd "$(dirname "$0")" || exit 1

# tutor 以 `--restricted` 呼叫 Claude CLI，會略過 ~/.claude/settings.json 的 env
# 區塊（只以 --settings 補回 apiKeyHelper），而 LaunchAgent 不繼承 shell 環境。
# 少了這行，子行程會拿 ccr 的 key 去打 api.anthropic.com，回 "Invalid API key"。
export ANTHROPIC_BASE_URL=http://127.0.0.1:3456

exec ./venv/bin/python -c "
from app import create_app
create_app().run(host='127.0.0.1', port=5990, debug=False, threaded=True)
"
