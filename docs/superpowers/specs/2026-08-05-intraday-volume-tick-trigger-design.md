# 盤中主力訊號：量能指標改即時 tick 觸發

日期：2026-08-05
檔案：`finmind/intraday_monitor.py`

## 背景

`intraday_monitor.py` 目前每 60 秒跑一次 `main()`：抓 1 分鐘 K 棒、算 13 項多空指標、
訊號數 ≥4（含至少 1 個量能訊號）就推播。使用者想讓更新更快，但調查後發現：

- VWAP / OBV / KD / MACD / RSI / MACD 背離（6項）本質上需要「K棒收盤」才有意義，
  用未收盤的當前 K 棒算會讓訊號在同一分鐘內反覆跳動，容易誤判。
- 量能/委買賣類指標（7項：預估量爆增、外盤內盤比、掛單失衡、昨量單K、均量倍數、
  超越開盤量、量爆）本質上是「當下累積值 vs 門檻」的比較，可以不等 K 棒收盤，
  用 tick 即時累積判斷。

因此目標縮小為：量能類指標即時觸發，技術指標維持 1 分鐘 K 棒收盤才算。

## 架構

`detect_signals(df, snap, avg5, yday_vol)` 拆成兩個函式：

- `detect_technical_signals(df) -> (long, short)`：原本的 1、2、3、4、5、13 項
  （VWAP突破、OBV領先、KD鈍化、MACD金叉死叉、RSI穿越、MACD背離）。邏輯與門檻完全
  不變，只是抽出獨立函式。
- `detect_volume_signals(df, snap, avg5, yday_vol) -> (long, short)`：原本的
  6、7、8、9、10、11、12 項（預估量爆增、外盤內盤比、掛單失衡、昨量單K、均量倍數、
  超越開盤量、量爆）。邏輯與門檻不變，一樣讀 `df`（`get_1min_kbars()` resample 出來
  的當日 K 棒，含當前尚未收盤那一根），只是能被更高頻率呼叫。

主迴圈改寫：

```python
_last_bar_signals = {}  # {code: {'long': [...], 'short': [...]}}

while True:
    now = datetime.now()
    if now.second < 5:  # 每分鐘整點附近跑一次技術指標收盤重算
        recompute_technical_cache()   # 對每檔股票更新 _last_bar_signals[code]

    volume_check()   # 每次迴圈（約5秒一次）都跑：量能訊號 + 快取的技術訊號 → 合併判斷推播

    time.sleep(5)
```

`volume_check()` 對每檔非冷卻中的股票：
1. 用 `get_1min_kbars()` 重新算 `detect_volume_signals()`（用當前累積中的K棒，tick已經
   持續累積在 `_tick_store`，不需要額外訂閱或呼叫）
2. 跟 `_last_bar_signals[code]` 的技術訊號合併成 `long_sigs` / `short_sigs`
3. 沿用現有判斷：`len(signals) >= SIGNAL_THRESHOLD and has_vol_signal(signals)`
4. 觸發就照舊 `build_message()` → `send_telegram()` → 寫入 cooldown

技術指標收盤重算（`recompute_technical_cache`）沿用原本 `main()` 裡呼叫
`detect_technical_signals()` 的邏輯，只是把結果存進快取而不是立刻判斷推播。

## 資料流

```
Shioaji tick callback → _tick_store（原有，不動）
                              │
                              ├─ 每 60 秒：get_1min_kbars() → detect_technical_signals()
                              │            → 存入 _last_bar_signals[code]
                              │
                              └─ 每 5 秒：get_1min_kbars() → detect_volume_signals()
                                           + _last_bar_signals[code]
                                           → 合併判斷 → 推播 / 略過
```

## 邊界情況

- **開盤暖機**：程式剛啟動時 `_last_bar_signals` 是空字典，`volume_check()` 對還沒有
  快取的股票直接跳過（不判斷推播），等第一次 `recompute_technical_cache()` 跑完才開始
  正常運作。跟現行「暖機完成才進迴圈」的行為一致，不會誤推播。
- **command-center log 解析相容**：`command-center/sources.py` 的 `intraday()` 用正則
  `檢查 (\w+) (\S+)` 和 `→ (多方|空方)推播已送出（信心 (\d+)%）`（`re.search`，不要求
  比對到行尾）解析 log。這兩行 print 格式維持不變，`sources.py` 不用改。
- **冷卻機制不變**：`COOLDOWN_MINUTES = 30` 沿用，同一檔股票推播後 30 分鐘內
  `volume_check()` 直接跳過，不會因為改成每 5 秒檢查就推播更頻繁。
- **推播訊息加註來源**：`build_message()` 尾端加一行極小字「⚡ tick即時觸發」，方便
  事後分辨這則推播是即時觸發還是等到收盤才觸發（技術指標湊滿4項的情況維持原樣，不加註）。
  純附加文字，不影響 log 正則比對。

## 不動的部分

- `get_snapshot()`、`get_1min_kbars()`、`_setup_tick_stream()`、`_backfill_today_ticks()`：
  資料層完全不動。
- `calc_confidence()`、`has_vol_signal()`、`build_message()`：吃最終合併後的 signals
  list，不管訊號來源，不用改。
- `SIGNAL_THRESHOLD`、`VOL_SIGNAL_KEYS`、`COOLDOWN_MINUTES`：門檻數值不變。
- `command-center` 前端與 `sources.py`：不用改。

## 驗證方式

1. `python3 -c "import intraday_monitor"`：語法/import 過關。
2. 非交易時段用暖機回補的當日 tick 資料，手動呼叫
   `recompute_technical_cache()` 和 `volume_check()`，確認產出的訊號 list、
   log 格式跟推播訊息內容正確（不需要真的送出 Telegram，用假推播函式攔截檢查文字）。
3. 隔天開盤觀察 `logs/intraday_monitor.log`，確認：
   - 一般收盤觸發的推播格式不變
   - 有機會看到帶「⚡ tick即時觸發」字樣的即時推播
   - `/svc/5400/` 卡片（command-center）顯示正常，沒有解析錯誤
