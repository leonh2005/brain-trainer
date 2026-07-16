# last30days-web

薄殼 Flask 包 [last30days](https://github.com/mvanhorn/last30days-skill) 引擎的本機網頁版。

- **Port**: 5750（http://localhost:5750/）
- **LaunchAgent**: `com.steven.last30days-web`（KeepAlive，log 在 `webui.log`）
- **報告庫**: `~/Documents/Last30Days/`（與 CLI / Claude Code 跑的研究共用）
- **引擎路徑**: 動態 glob `~/.claude/plugins/cache/last30days-skill/last30days/*/…`，plugin 更新不用改
- **報告格式**: `--emit brief` 產完整合成報告（Markdown），檔名 = 中文主題 + 時間戳（引擎的 `--emit html` 只是給 LLM 宿主的統計外殼，別用）
- **台灣新聞語料庫**: 每次跑研究前從 news-analyzer 的 `news.db` 匯出近 35 天文章到 `corpus/`（每 6 小時刷新），接引擎 `--corpus`——台股主題靠這個 + YouTube 台灣財經節目
- **引擎 LLM planner**: `~/.config/last30days/.env` 的 `OPENAI_API_KEY`（news key）。沒有它，中文具名主題在 headless 模式會搜不到東西
- **翻譯**: Groq `llama-3.3-70b-versatile`，key 讀 `~/.config/last30days/.env` 的 `GROQ_API_KEY`；譯文存成 `<原檔名>.zh.md`，不重翻

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
