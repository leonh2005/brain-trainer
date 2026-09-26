# learn-system

以「概念」為中心的 AI 學習系統，可套用任何領域。

設計文件：`../docs/superpowers/specs/2026-09-26-learn-system-design.md`
實作計畫：`../docs/superpowers/plans/2026-09-26-learn-system.md`

## 啟動

```bash
./run.sh          # 前景執行
```

常駐由 LaunchAgent `com.steven.learn-system` 管理，port 5990。
設定檔：本目錄的 `com.steven.learn-system.plist`（安裝時複製到 `~/Library/LaunchAgents/`）。

```bash
mkdir -p logs                                                  # plist 的 log 路徑在此目錄下
cp com.steven.learn-system.plist ~/Library/LaunchAgents/       # 安裝
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.steven.learn-system.plist
launchctl kickstart -k gui/$(id -u)/com.steven.learn-system   # 重啟
curl http://127.0.0.1:5990/api/health                          # 健康檢查
```

`run.sh` 會 `export ANTHROPIC_BASE_URL=http://127.0.0.1:3456`：tutor 以
`--restricted` 呼叫 Claude CLI，會略過 `~/.claude/settings.json` 的 `env`，而
LaunchAgent 不繼承 shell 環境，少了這行子行程會拿到 "Invalid API key"。

## 測試

```bash
./venv/bin/pytest -v
```
