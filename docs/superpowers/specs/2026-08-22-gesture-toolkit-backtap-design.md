# Gesture Toolkit — 敲背手勢（Back Tap）設計稿

## 背景

Core Engine（Accessibility Service + 動作執行器 + 8 個系統動作）已完成並實機驗收通過（`docs/superpowers/specs/2026-08-22-gesture-toolkit-core-engine-design.md`）。這是感應器手勢偵測階段的第一個子專案：**敲背 Back Tap**——選它當第一個是因為只需要加速度計、不需要陀螺儀融合，演算法最單純，適合拿來建立「感測器架構 + 手勢偵測」這一整套之後其他手勢（甩動點手電筒/轉腕開相機/翻轉靜音/貼耳擴音）都能重用的骨架。

**這一輪決議的範圍收斂**：
- ✅ 敲背兩下 → 固定觸發「螢幕截圖」動作（先驗證機制有效，之後才做「自訂綁定任一 Action」的 UI）
- ✅ 防誤觸濾波（區分敲擊 vs 走路震動）——沒有這個 Back Tap 會誤觸發到不能用，是必要項不是加分項
- ❌ 校準精靈（讓使用者實測收集樣本自動調閾值）——先用寫死的固定閾值，等固定閾值版本實測有效後才考慮要不要做校準 UI
- ❌ 其他 4 種手勢——留給各自獨立的下一個子專案

## 架構決策

**感測器監聽掛在 `GestureAccessibilityService` 裡，不開新的 Foreground Service。**
Why：Android 背景持續監聽感測器技術上不強制要 Foreground Service，但長時間背景監聽在較新 Android 版本上容易被系統限制頻率；Accessibility Service 本身就有背景常駐豁免權，且 Core Engine 的動作執行也是靠它，把感測器監聽也放進同一個 Service，不用多開一個服務、不用多一個常駐通知、更省電。

**資料流**：
```
GestureAccessibilityService 內的 SensorEventListener
  → 收到 TYPE_ACCELEROMETER 資料，累積進 BackTapDetector
  → BackTapDetector 判斷「敲擊事件」（見下方演算法）
  → 判斷「兩次敲擊構成一次 Back Tap」
  → 呼叫 AppActions.registry.get("screenshot")?.execute(context)
```

## 元件

```
app/src/main/kotlin/com/steven/gesturetoolkit/
├── service/
│   └── GestureAccessibilityService.kt   — 修改：onServiceConnected 時註冊 SensorEventListener，onDestroy 時取消註冊
├── sensors/
│   ├── TapEvent.kt                       — 新增：data class，代表一次「敲擊事件」(timestamp, peakMagnitude)
│   └── BackTapDetector.kt                — 新增：純邏輯類別，吃加速度計 (x,y,z,timestamp) 序列，吐出「這是不是一次 Back Tap」的布林結果
```

**`BackTapDetector` 是純邏輯、不依賴 Android framework**（不直接吃 `SensorEvent`，而是吃 `(x: Float, y: Float, z: Float, timestampNanos: Long)` 這種原始數字），這樣才能用純 JUnit4 寫測試，符合 Core Engine 定下的「不用 Robolectric/Mockito」全域限制——可以直接餵一串模擬數值（例如「兩次尖峰」vs「持續搖晃」）驗證判斷邏輯對不對，不需要真的觸發 Android 感測器事件。

## 演算法（`BackTapDetector`）

- **合成加速度**：`magnitude = sqrt(x² + y² + z²)`，扣掉靜止時的重力基準（約 9.81 m/s²）得到「淨加速度」
- **敲擊事件判定**：淨加速度超過 `IMPACT_THRESHOLD`（先訂 25 m/s²，約 2.5g）算一次候選衝擊；持續超過閾值的時間必須 **短於 `MAX_IMPACT_DURATION_MS`（100ms）**——這是區分「敲擊」（窄、急）跟「走路/搖晃」（寬、緩）的關鍵：走路震動即使幅度夠大，持續時間通常遠超過 100ms
- **雙擊判定**：兩次符合條件的敲擊事件，間隔落在 `MIN_GAP_MS`(100ms) ~ `MAX_GAP_MS`(600ms) 之間，才算一次「Back Tap」——太快可能是同一次衝擊的震盪餘波，太慢就不算連續兩下
- **冷卻**：一次 Back Tap 觸發後，`COOLDOWN_MS`(1000ms) 內不再觸發下一次，避免手震或連續敲擊被算成好幾次

## 錯誤處理

- 手機沒有加速度計（理論上不會發生，但防禦性檢查）：`SensorManager.getDefaultSensor(TYPE_ACCELEROMETER)` 回傳 null 時記錄但不崩潰，Back Tap 功能自動停用，其餘 Core Engine 動作不受影響
- `BackTapDetector` 內部只做數值判斷，不會拋例外；`GestureAccessibilityService` 呼叫 `execute()` 觸發動作若失敗（例如無障礙服務暫時斷線），沿用 Core Engine 既有的 `isAvailable()` 保護（`execute()` 前應檢查 `isAvailable()`，避免呼叫到失效狀態）

## 測試

- **單元測試（純 JUnit4，`BackTapDetector` 本身）**：
  - 餵一串「兩個窄尖峰、間隔 300ms」的模擬數值序列 → 應判定為 Back Tap
  - 餵「單一尖峰」→ 不該判定為 Back Tap（只有一次敲擊）
  - 餵「持續 300ms 的搖晃」（幅度超過閾值但持續太久）→ 不該判定為敲擊事件
  - 餵「兩次尖峰但間隔 900ms」（超過 MAX_GAP_MS）→ 不該判定為 Back Tap
  - 冷卻期內第二組敲擊 → 不該重複觸發
- **手動 QA（實機）**：安裝到 Pixel 5，開啟無障礙服務，實際敲手機背面兩下，確認螢幕截圖真的產生；走路/搖晃手機測試不應誤觸發（沒有絕對量化標準，人工感受「明顯不會亂觸發」即可）

## 下一步

本 spec 通過後用 `writing-plans` 產生實作計畫。之後每個新手勢（甩動點手電筒/轉腕開相機/翻轉靜音/貼耳擴音）都是各自獨立的子專案，重用這裡建立的「Service 裡註冊 SensorEventListener → 純邏輯 Detector 判斷 → 呼叫 ActionRegistry」骨架。
