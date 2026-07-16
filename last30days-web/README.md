# last30days-web

薄殼 Flask 包 [last30days](https://github.com/mvanhorn/last30days-skill) 引擎的本機網頁版。

- **Port**: 5750（http://localhost:5750/）
- **LaunchAgent**: `com.steven.last30days-web`（KeepAlive，log 在 `webui.log`）
- **報告庫**: `~/Documents/Last30Days/`（與 CLI / Claude Code 跑的研究共用）
- **引擎路徑**: 動態 glob `~/.claude/plugins/cache/last30days-skill/last30days/*/…`，plugin 更新不用改
- **翻譯**: Groq `llama-3.3-70b-versatile`，key 讀 `~/.config/last30days/.env` 的 `GROQ_API_KEY`；譯文存成 `<原檔名>.zh.html`，不重翻

## 功能

1. 輸入主題 → 背景跑引擎（quick/deep）→ 輪詢進度 → 完成自動開報告
2. 同時只跑一個研究（409 拒絕重複提交）
3. 報告頁頂注入工具列：回列表 / 翻成中文 / 中英切換
4. 首頁列出歷史報告，可關鍵字過濾

## 維運

```bash
launchctl kickstart -k gui/501/com.steven.last30days-web   # 重啟
curl http://localhost:5750/api/health                       # 健康檢查
tail -f ~/CCProject/last30days-web/webui.log                # 看 log
```

依賴：系統 python3（flask、requests）＋已安裝的 last30days plugin。
