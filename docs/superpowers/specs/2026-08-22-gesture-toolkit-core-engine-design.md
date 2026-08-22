# Gesture Toolkit — Core Engine 設計稿

## 背景

參考 Google Play 上的 DroidLab（`com.lightsourcelabs.droidlab`，Lightsource Labs 開發），一個透過感應器手勢＋系統動作的手機控制工具。原始 app 涵蓋 5 大子系統：感應器手勢、螢幕懸浮工具、多指手勢綁定、系統動作庫、校準濾波系統。

**經跟 Steven 討論收斂後的最終範圍**（2026-08-22 決議）：
- ❌ 螢幕懸浮工具（Scroll Assist / TouchPad Pointer / Edge Sense / FloatPin / SnapText）— 取消
- ❌ 多指手勢自訂綁定 — 取消（技術上只能靠全螢幕透明浮層攔截觸控才能做到任意手勢，但這樣會干擾其他 App 正常操作，體驗太差；Accessibility Service 內建手勢又只支援有限的固定手勢組合，價值不高，Steven 決定不做）
- ✅ 感應器手勢（敲背 Back Tap、甩動點手電筒 Chop-Chop、轉腕開相機、翻轉螢幕靜音來電、貼耳自動切換擴音）
- ✅ 系統動作庫（截圖、音量調整、鎖定裝置、啟動 App、Home/Back/Recents）
- ✅ 校準精靈 + 防誤觸濾波（Peak-Width Guards）
- 🔲 Tasker 整合（可選，排在最後）

專案拆成多個子專案依序做，各自獨立 spec → plan → implementation：
1. **Core Engine**（本文件範圍）：Accessibility Service + 動作執行器 + 設定畫面骨架
2. 感應器手勢偵測（下一個 spec）
3. Tasker 整合（可選，最後）

**用途**：Steven 個人 Pixel 5 使用，側載安裝（APK 直接裝），不上架 Play Store，因此不需要隱私政策、Accessibility 用途審核文件等上架流程。

**技術**：Native Kotlin（Android Studio 已安裝），非 Flutter——核心功能幾乎全是 Android 專屬 API（AccessibilityService、SensorManager、Camera2 Torch、前景服務），Flutter 只會多包一層 platform channel，沒有實質好處。

**專案位置**：`~/CCProject/gesture-toolkit/`（新目錄，Gradle 專案，applicationId `com.steven.gesturetoolkit`）

---

## 本文件範圍：Core Engine

### 目標

蓋好「動作執行」這一層基礎設施：使用者能在設定畫面看到所有支援的系統動作，手動點擊測試每個動作是否正確執行。之後的感應器手勢模組只需要「偵測到手勢 → 呼叫某個 Action」，不用碰執行細節。

### 架構

```
app/
├── MainActivity.kt                — 設定畫面 shell，導引開啟無障礙權限
├── service/
│   └── GestureAccessibilityService.kt  — AccessibilityService 實作，真正執行系統動作
├── actions/
│   ├── Action.kt                  — sealed class/interface，定義 execute()
│   ├── ActionRegistry.kt          — 列舉所有可用 Action，供 UI 顯示 + 之後手勢模組查詢
│   └── impl/
│       ├── ScreenshotAction.kt
│       ├── VolumeAction.kt
│       ├── LockScreenAction.kt
│       ├── LaunchAppAction.kt
│       └── SystemNavAction.kt     — Home/Back/Recents
└── ui/
    └── ActionListScreen.kt        — Compose UI：動作清單 + 每項「測試」按鈕 + 無障礙服務狀態顯示
```

### 元件說明

**`GestureAccessibilityService`**
- 繼承 `AccessibilityService`，`onServiceConnected()` 時記錄自身為 singleton 供 Action 呼叫（Android 無法直接 `new` 一個 AccessibilityService instance，須透過已連線的 service 執行 `performGlobalAction()`）
- 提供 `companion object` 存目前連線中的 service 實例（nullable，未開啟服務時為 null）
- 執行：`performGlobalAction(GLOBAL_ACTION_TAKE_SCREENSHOT)`、`GLOBAL_ACTION_LOCK_SCREEN`、`GLOBAL_ACTION_HOME`、`GLOBAL_ACTION_BACK`、`GLOBAL_ACTION_RECENTS`（皆為 API 28+ 支援，Pixel 5 跑 Android 11+ 沒問題）

**`Action` 介面**
```kotlin
interface Action {
    val id: String
    val label: String
    fun isAvailable(context: Context): Boolean  // 例如無障礙服務未開啟時回傳 false
    fun execute(context: Context)
}
```

**`ActionRegistry`**
- 單例，持有 `List<Action>`，供 UI 列表 + 未來手勢模組透過 `id` 查詢對應 Action
- 新增動作只需實作 `Action` 介面並加進 registry list，不用改其他地方（開放封閉原則）

**個別 Action 實作**
- `ScreenshotAction` / `LockScreenAction` / `SystemNavAction`：透過 `GestureAccessibilityService` 的 companion instance 呼叫 `performGlobalAction`
- `VolumeAction`：直接用 `AudioManager.adjustStreamVolume()`，不需要無障礙服務
- `LaunchAppAction`：用 `PackageManager.getLaunchIntentForPackage()` + `FLAG_ACTIVITY_NEW_TASK` 啟動；設定畫面提供 App 選擇器（`PackageManager.getInstalledApplications()` 篩出有 launcher intent 的 app）

**`MainActivity` + `ActionListScreen`**
- Jetpack Compose 單頁畫面
- 頂部顯示無障礙服務狀態（已啟用/未啟用），未啟用時顯示「前往設定開啟」按鈕，用 `Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)` 導去系統設定（Android 無法用程式直接開啟無障礙服務，只能導使用者去手動勾選）
- 下方列出 `ActionRegistry` 所有動作，每項一個「測試」按鈕，點擊呼叫 `action.execute(context)`

### 資料流

```
使用者點「測試」按鈕
  → ActionListScreen 呼叫 action.execute(context)
  → Action 內部呼叫 GestureAccessibilityService.instance?.performGlobalAction(...)
     （或直接系統 API，如 VolumeAction）
  → 系統執行對應動作（螢幕截圖/鎖定/切換App等）
```

之後感應器手勢模組的資料流會是：
```
SensorManager 偵測到手勢（如敲背兩下）
  → GestureDetector 判定手勢類型
  → 查詢使用者設定的「手勢→Action id」綁定
  → ActionRegistry.get(id).execute(context)
```

### 錯誤處理
- `GestureAccessibilityService.instance` 為 null（服務未開啟）時，`Action.isAvailable()` 回傳 false，UI 上該按鈕disable並顯示提示文字「請先開啟無障礙權限」
- `LaunchAppAction` 找不到對應 app（可能被移除）時，捕捉例外並 Toast 提示，不 crash

### 測試
- **手動 QA**（無障礙服務類功能無法完全自動化測試，需實機）：安裝 APK → 開啟無障礙權限 → 逐一點擊每個「測試」按鈕，確認實際效果（螢幕截圖真的存進相簿、音量真的調整、桌面真的鎖定、選定的 App 真的被開啟、Home/Back/Recents 真的有反應）
- **Unit test**：`ActionRegistry` 內容（每個 Action 的 id 不重複、label 非空）、`Action.isAvailable()` 邏輯（mock 無障礙服務未連線時回傳 false）

---

## 下一步

本 spec 通過後，用 `writing-plans` skill 產生 Core Engine 的實作計畫（含 Android 專案初始化、Gradle 設定、逐步實作＋每步驗證）。感應器手勢偵測留給下一份獨立 spec。
