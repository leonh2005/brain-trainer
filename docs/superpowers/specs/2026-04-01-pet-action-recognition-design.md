# 墨墨動作辨識系統設計文件

**日期**：2026-04-01
**整合目標**：rabbit-care（`~/CCProject/rabbit-care/`）

---

## 概述

在 rabbit-care 系統中新增動作辨識功能：透過 IP 攝影機的 RTSP 串流，偵測墨墨的移動片段，擷取關鍵影格後送 Gemini 2.5 Flash Vision 分析，辨識結果自動寫入 daily_log，首頁顯示今日行為統計。

**目標辨識動作**：吃飯、喝水、伸懶腰

---

## 架構

```
IP 攝影機 (RTSP)
    ↓
motion_watcher.py（launchd 背景服務）
    ↓ 偵測到移動（畫面差異超過門檻）
frame_extractor（擷取 3~5 張關鍵影格）
    ↓
Gemini 2.5 Flash Vision
    ↓ JSON 回傳動作分類
rabbit-care API（POST /api/log-action）
    ↓
daily_log.actions（JSON 陣列）
```

---

## 元件設計

### 1. motion_watcher.py

**路徑**：`~/CCProject/rabbit-care/motion_watcher.py`

- 透過 OpenCV 連接 RTSP 串流（`cv2.VideoCapture(rtsp_url)`）
- 每秒比較相鄰影格差異，超過門檻（可設定）視為移動開始
- 移動結束後（連續 3 秒靜止），取這段期間等距抽出 4 影格
- 送 Gemini 分析（見下節）
- 冷卻機制：同一次移動事件只送一次，避免重複記錄
- 設定項目（`.env` 或 `config.py`）：
  - `RTSP_URL`
  - `MOTION_THRESHOLD`（畫面差異門檻，預設 500）
  - `COOLDOWN_SECONDS`（兩次辨識最短間隔，預設 60 秒）

### 2. Gemini Vision 分析

- 使用現有 Gemini API key（`~/CCProject/.secrets/`）
- 每次送 4 張 JPEG 影格 + prompt：

```
這是寵物兔子的監控截圖（依時間順序）。
請判斷牠正在做什麼，從以下選項中選一個：
- eating（在吃飯）
- drinking（在喝水）
- stretching（在伸懶腰）
- other（其他）

只回傳 JSON，格式：{"action": "eating", "confidence": 0.9}
```

- 若 `action == "other"` 或 `confidence < 0.7`，不寫入日誌

### 3. rabbit-care 整合

#### 資料庫變更

`daily_log` 表新增欄位：
```sql
ALTER TABLE daily_log ADD COLUMN actions TEXT DEFAULT '[]';
```
`actions` 儲存 JSON 陣列，例如：
```json
[
  {"action": "eating", "time": "09:23", "confidence": 0.92},
  {"action": "drinking", "time": "10:45", "confidence": 0.88}
]
```

#### 新 API Endpoint

`POST /api/log-action`
```json
{
  "action": "eating",
  "confidence": 0.92,
  "timestamp": "2026-04-01T09:23:00"
}
```
- 找到今日的 `daily_log` 記錄（若無則自動建立）
- 將動作 append 進 `actions` 陣列

#### 首頁顯示

在 `/`（首頁）的現有資料卡下方新增「今日行為」區塊：
```
🍽 吃飯：3 次（最後一次 12:30）
💧 喝水：2 次（最後一次 11:15）
🐇 伸懶腰：1 次（最後一次 09:23）
```

### 4. 服務管理

`~/Library/LaunchAgents/com.steven.motion-watcher.plist`
- 開機自動啟動，KeepAlive crash 後自動重啟
- Log：`~/CCProject/rabbit-care/motion-watcher.log`
- 與 rabbit-care 相同管理模式

---

## 設定項目

| 項目 | 說明 | 預設值 |
|------|------|--------|
| `RTSP_URL` | 攝影機 RTSP 網址 | 需設定 |
| `MOTION_THRESHOLD` | 移動偵測門檻（畫面差異像素數） | 500 |
| `COOLDOWN_SECONDS` | 兩次辨識最短間隔（秒） | 60 |
| `GEMINI_MIN_CONFIDENCE` | 最低信心值，低於此不記錄 | 0.7 |

---

## 相依套件

- `opencv-python`（RTSP 串流 + 影格擷取）
- `google-generativeai`（Gemini Vision，rabbit-care 已有）
- `Pillow`（影格轉 JPEG）
- `requests`（呼叫 rabbit-care API）

---

## 錯誤處理

- RTSP 斷線：自動重試，每 30 秒嘗試重連
- Gemini API 失敗：記錄 log，不影響 rabbit-care 正常運作
- rabbit-care API 失敗：暫存到本地 queue，下次重試

---

## 不在本次範圍

- 多隻寵物支援
- 歷史行為趨勢圖
- Telegram 推播（日後可擴充）
- 自訂動作類別
