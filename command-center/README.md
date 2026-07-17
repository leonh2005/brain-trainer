# AI 指揮中心（command-center）

Steven 的統一入口儀表板：聚合 20+ 個自建服務的訊號與狀態，提供一鍵動作與 AI 聊天。

- 後端：FastAPI，port 5950，對所有既有服務**只讀**
- 前端：單頁深色儀表板（Jinja2 + vanilla JS）
- AI：Claude Agent SDK（SSE 串流聊天）

## 頁面五大區塊

1. **今日訊號牆**：當沖候選（09:30）、隔日沖候選（14:00）、盤中主力訊號、均線交叉警報、籌碼異動、新聞情緒多空比、市場恐慌指標（VIX/F&G/CAPE 等 7 項）
2. **服務狀態**：20+ 服務健康燈號（proxy 既有 dashboard:5600）、排程最後執行時間、錯誤摘要
3. **Skill 按鈕區**：重跑隔日沖／重跑當沖／更新籌碼／重建市場儀表板／查個股（帶輸入框）
4. **AI 聊天**：跨服務整合分析（「3006 最近怎樣」會同時讀籌碼＋新聞＋選股紀錄）、追問偵錯、動口執行、彙整報告
5. **投組與生活區**：投資組合現況（VWRA/006208/00881/00864B）、兔子照護今日狀態、技能樹進度

## 資料源（全部唯讀）

| 訊號 | 來源 |
|------|------|
| 當沖候選 | /tmp/daytrade_candidates.json |
| 隔日沖候選 | /tmp/swing_candidates.json |
| 盤中訊號 | logs/intraday_monitor.log + vol_rank cache |
| 均線警報 | scripts/ma_monitor_state.json |
| 籌碼 | chip-tracker/chip.db |
| 新聞情緒 | news-analyzer/news.db |
| 市場恐慌 | market-dashboard/*_cache.json |
| 投組 | portfolio-analyzer:5800 /api/data |
| 健康 | dashboard:5600 /api/status + log_scan_report.json |
| 兔子 | rabbit-care/rabbit.db |
| 技能樹 | skill-tree/skill_tree.db |

## API

- `GET /api/signals/{daytrade|swing|intraday|ma|chips|news|market-fear}`
- `GET /api/portfolio`、`GET /api/health-all`、`GET /api/life/{rabbit|skilltree}`
- `POST /api/jobs/<id>/run`（白名單）、`GET /api/jobs/<id>/status`
- `POST /api/chat`（SSE）

## 設計需求（給 Claude Design 參考）

- 深色主題、繁體中文介面、卡片式版面
- 訊號牆是主角（佔最大版面），聊天欄常駐右側或可收合
- 手機可用（responsive）
