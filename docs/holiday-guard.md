# 連假守衛（holiday-guard）設定

服務位置：`market-analysis/holiday_guard.py`
排程：每個交易日 13:00（收盤前半小時）
觸發條件：今天是連假前 T-3、T-2、T-1（連假 = 休市 ≥ 3 天）
通知：Telegram

## 你需要在 config/pledge_portfolio.json 填的四項

```json
{
  "loan_amount": 0,            ← 質押借款金額（元），例：1000000
  "maintenance_call_pct": 130, ← 券商追繳線（%），不知道就維持 130
  "pledged": [],               ← 質押給券商的部位（算維持率用）
  "holdings": []               ← 全部台股庫存（API 權限開通前的備援）
}
```

### pledged 格式（質押部位）

```json
"pledged": [
  {"code": "006208", "quantity": 10000},
  {"code": "00881", "quantity": 20000}
]
```

`quantity` 單位是**股**（1 張 = 1000 股）。

### holdings 格式（全部台股庫存）

```json
"holdings": [
  {"code": "006208", "quantity": 20000},
  {"code": "00881", "quantity": 20000},
  {"code": "00864B", "quantity": 10000}
]
```

## 檢查內容

| 時機 | 動作 |
|---|---|
| T-3 | 質押壓力測試（現價 × 0.903 算最壞維持率） |
| T-2 | 掃假期間事件（FOMC／財報／法說會／地緣） |
| T-1 | 三項總確認再通知一次 |

## 資料來源切換

1. 優先呼叫 gateway `/positions`（Shioaji API）
2. 失敗（目前是 401 權限不足）→ 讀 `holdings`
3. 兩者都沒有 → 通知會標「未設定」

**401 冷卻機制**：Shioaji SDK 收到 401 會主動斷開 Solace session 並重登（約 1 秒），
所以 401 後會記在 `data/holiday_guard_state.json`，**7 天內不再嘗試**，直接用設定檔。
權限開通後最多 7 天會自動切回 API，不需要改任何設定。

## 已知限制

- 假日檔 `data/tw_market_holidays.json` 目前只有 2025、2026。缺當年資料時服務會發 Telegram 警告並停止判定（不會靜默）。
- 只涵蓋台股，VWRA 等海外部位不納入。
- 質押維持率用「擔保品市值 ÷ 借款金額」，若你的券商公式不同，需調整 `holiday_guard.py` 的計算。
- 同一天只會通知一次（`last_key` 去重）；若機器在 13:00 睡眠，喚醒後補跑仍會通知。
