# Gesture Toolkit Back Tap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Gesture Toolkit 加入「敲背兩下觸發螢幕截圖」的手勢偵測，並建立之後其他感測器手勢都能重用的骨架（Service 裡監聽感測器 → 純邏輯 Detector 判斷 → 呼叫 ActionRegistry）。

**Architecture:** `BackTapDetector`（純 Kotlin 邏輯類別，吃 (x,y,z,timestampMs) 原始數值，用「短促尖峰 + 雙擊間隔 + 冷卻」判斷是否構成一次 Back Tap）+ `GestureAccessibilityService` 實作 `SensorEventListener`，註冊加速度計、收到資料餵給 Detector、觸發時呼叫 `AppActions.registry.get("screenshot")`。

**Tech Stack:** Kotlin（沿用 gesture-toolkit 專案既有的 compileSdk 37 / AGP 9.1.0 / Gradle 9.3.1 設定，本次不需要新增任何 Gradle 依賴）

**Spec:** `docs/superpowers/specs/2026-08-22-gesture-toolkit-backtap-design.md`

## Global Constraints

- 感測器監聽掛在既有的 `GestureAccessibilityService` 裡，不開新的 Foreground Service
- `BackTapDetector` 必須是純邏輯類別，不 import 任何 `android.*`/`Sensor*` 型別，只吃 `(x: Float, y: Float, z: Float, timestampMs: Long)` 原始數值 → 這樣才能用純 JUnit4 測試，不用 Robolectric/Mockito（沿用 Core Engine 的全域限制）
- 敲背成功偵測後固定觸發 `AppActions.registry.get("screenshot")`（不做「自訂綁定任一 Action」的 UI，那是之後才做的事）
- 演算法閾值（先訂死，不做校準 UI）：
  - `IMPACT_THRESHOLD = 25f`（m/s²，扣除重力後的淨加速度衝擊閾值）
  - `MAX_IMPACT_DURATION_MS = 100L`（衝擊持續時間上限，超過這個時間就不算敲擊、視為走路/搖晃）
  - `MIN_GAP_MS = 100L`、`MAX_GAP_MS = 600L`（兩次敲擊事件的合法間隔區間，才算一次「敲兩下」）
  - `COOLDOWN_MS = 1000L`（觸發後的冷卻時間，避免連環誤觸）

---

## Task 1: `TapEvent` + `BackTapDetector`（TDD，純邏輯）

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/sensors/TapEvent.kt`
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/sensors/BackTapDetector.kt`
- Test: `gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/sensors/BackTapDetectorTest.kt`

**Interfaces:**
- Consumes: 無（純邏輯，不依賴 Core Engine 既有程式碼）
- Produces: `data class TapEvent(val timestampMs: Long, val peakMagnitude: Float)`；`class BackTapDetector(impactThreshold: Float = 25f, maxImpactDurationMs: Long = 100L, minGapMs: Long = 100L, maxGapMs: Long = 600L, cooldownMs: Long = 1000L) { fun onSensorData(x: Float, y: Float, z: Float, timestampMs: Long): Boolean }`——`onSensorData` 回傳 `true` 代表這筆資料讓「敲背兩下」的條件成立（呼叫端應該立刻觸發動作）

- [ ] **Step 1: 建立測試目錄**

```bash
mkdir -p /Users/steven/CCProject/gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/sensors
mkdir -p /Users/steven/CCProject/gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/sensors
```

- [ ] **Step 2: 寫失敗的測試**

`gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/sensors/BackTapDetectorTest.kt`：

```kotlin
package com.steven.gesturetoolkit.sensors

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class BackTapDetectorTest {

    /** 模擬一次「衝擊」：在 startMs 時淨加速度衝到 peakNetAccel，經過 durationMs 後降回重力基準。
     * 回傳最後一筆（降回基準那筆）的 onSensorData 結果。 */
    private fun feedImpact(detector: BackTapDetector, startMs: Long, durationMs: Long, peakNetAccel: Float): Boolean {
        detector.onSensorData(0f, 0f, 9.81f + peakNetAccel, startMs)
        return detector.onSensorData(0f, 0f, 9.81f, startMs + durationMs)
    }

    @Test
    fun `two narrow impacts 300ms apart trigger back tap`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `single impact does not trigger`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `sustained shake longer than max impact duration is not counted as a tap`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 300, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 350, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `two impacts 900ms apart do not trigger (gap too large)`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 900, durationMs = 30, peakNetAccel = 30f))
    }

    @Test
    fun `no repeated trigger within cooldown window`() {
        val detector = BackTapDetector()
        assertFalse(feedImpact(detector, startMs = 0, durationMs = 30, peakNetAccel = 30f))
        assertTrue(feedImpact(detector, startMs = 300, durationMs = 30, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 400, durationMs = 30, peakNetAccel = 30f))
        assertFalse(feedImpact(detector, startMs = 700, durationMs = 30, peakNetAccel = 30f))
    }
}
```

- [ ] **Step 3: 執行測試確認失敗**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.sensors.BackTapDetectorTest"
```

Expected: 編譯失敗，找不到 `TapEvent`/`BackTapDetector`

- [ ] **Step 4: 寫 `TapEvent.kt`**

```kotlin
package com.steven.gesturetoolkit.sensors

data class TapEvent(val timestampMs: Long, val peakMagnitude: Float)
```

- [ ] **Step 5: 寫 `BackTapDetector.kt`**

```kotlin
package com.steven.gesturetoolkit.sensors

import kotlin.math.abs
import kotlin.math.sqrt

class BackTapDetector(
    private val impactThreshold: Float = IMPACT_THRESHOLD,
    private val maxImpactDurationMs: Long = MAX_IMPACT_DURATION_MS,
    private val minGapMs: Long = MIN_GAP_MS,
    private val maxGapMs: Long = MAX_GAP_MS,
    private val cooldownMs: Long = COOLDOWN_MS,
) {
    companion object {
        const val IMPACT_THRESHOLD = 25f
        const val MAX_IMPACT_DURATION_MS = 100L
        const val MIN_GAP_MS = 100L
        const val MAX_GAP_MS = 600L
        const val COOLDOWN_MS = 1000L
        private const val GRAVITY = 9.81f
    }

    private var impactStartMs: Long? = null
    private var impactPeak: Float = 0f
    private var pendingTap: TapEvent? = null
    private var lastTriggerMs: Long? = null

    /** 餵一筆加速度計原始數值，回傳這筆資料是否讓「敲背兩下」的條件成立。 */
    fun onSensorData(x: Float, y: Float, z: Float, timestampMs: Long): Boolean {
        val magnitude = sqrt(x * x + y * y + z * z)
        val netAccel = abs(magnitude - GRAVITY)

        if (netAccel >= impactThreshold) {
            if (impactStartMs == null) {
                impactStartMs = timestampMs
                impactPeak = netAccel
            } else if (netAccel > impactPeak) {
                impactPeak = netAccel
            }
            return false
        }

        val start = impactStartMs ?: return false
        impactStartMs = null
        val duration = timestampMs - start
        if (duration > maxImpactDurationMs) return false

        return onTapEvent(TapEvent(timestampMs = start, peakMagnitude = impactPeak))
    }

    private fun onTapEvent(tap: TapEvent): Boolean {
        lastTriggerMs?.let { if (tap.timestampMs - it < cooldownMs) return false }

        val previous = pendingTap
        if (previous != null) {
            val gap = tap.timestampMs - previous.timestampMs
            if (gap in minGapMs..maxGapMs) {
                pendingTap = null
                lastTriggerMs = tap.timestampMs
                return true
            }
        }
        pendingTap = tap
        return false
    }
}
```

- [ ] **Step 6: 執行測試確認通過**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.sensors.BackTapDetectorTest"
```

Expected: `BUILD SUCCESSFUL`，5 個測試全通過

- [ ] **Step 7: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/sensors/ gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/sensors/
git commit -m "feat(gesture-toolkit): BackTapDetector 敲背偵測純邏輯 + 單元測試"
```

---

## Task 2: 整合進 `GestureAccessibilityService` + 手動 QA

**Files:**
- Modify: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/service/GestureAccessibilityService.kt`

**Interfaces:**
- Consumes: `BackTapDetector`（Task 1）、`AppActions.registry.get(id): Action?`（Core Engine 既有）
- Produces: 無新對外介面，這是把 Task 1 的偵測邏輯接上真正的 Android 感測器

- [ ] **Step 1: 修改 `GestureAccessibilityService.kt`，加入 SensorEventListener**

把整個檔案內容改成：

```kotlin
package com.steven.gesturetoolkit.service

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.view.accessibility.AccessibilityEvent
import com.steven.gesturetoolkit.actions.AppActions
import com.steven.gesturetoolkit.sensors.BackTapDetector

class GestureAccessibilityService : AccessibilityService(), SensorEventListener {

    companion object {
        var instance: GestureAccessibilityService? = null
            private set
    }

    private val backTapDetector = BackTapDetector()
    private var sensorManager: SensorManager? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        val sm = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        sensorManager = sm
        val accelerometer = sm.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
        if (accelerometer != null) {
            sm.registerListener(this, accelerometer, SensorManager.SENSOR_DELAY_GAME)
        }
    }

    override fun onSensorChanged(event: SensorEvent) {
        val timestampMs = event.timestamp / 1_000_000L
        val triggered = backTapDetector.onSensorData(event.values[0], event.values[1], event.values[2], timestampMs)
        if (triggered) {
            val action = AppActions.registry.get("screenshot")
            if (action != null && action.isAvailable()) {
                action.execute(this)
            }
        }
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {
        // 不需要處理精度變化
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 本 app 只用這個服務執行 performGlobalAction，不需要監聽任何無障礙事件
    }

    override fun onInterrupt() {
        // 系統中斷服務時呼叫，本 app 無需特別處理
    }

    override fun onDestroy() {
        super.onDestroy()
        sensorManager?.unregisterListener(this)
        if (instance === this) {
            instance = null
        }
    }
}
```

- [ ] **Step 2: 建置驗證**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest assembleDebug
```

Expected: 兩個都 `BUILD SUCCESSFUL`（Task 1 的單元測試不受影響、新的 Service 程式碼能編譯過）

- [ ] **Step 3: 安裝到 Pixel 5 並手動 QA**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew installDebug
adb shell am start -n com.steven.gesturetoolkit/.MainActivity
```

手動驗證步驟：
1. 確認無障礙服務仍是「已啟用」（這次改動沒有動到無障礙服務的啟用流程，理論上不需要重新授權，但如果狀態卡片顯示「尚未啟用」就照 Core Engine 的流程重新開啟一次）
2. 把手機拿在手上，實際用手指輕敲手機背面兩下（間隔大約 0.3 秒，不要太快也不要太慢）
3. 確認畫面真的觸發了螢幕截圖（可以用 `adb shell find /sdcard/Pictures/Screenshots -newer <剛才某個檔案>` 確認有沒有新截圖產生，或直接看畫面有沒有截圖動畫/音效反應）
4. 走路、搖晃手機一段時間，確認**沒有**誤觸發截圖（沒有絕對量化標準，人工感受「明顯不會亂觸發」即可；如果一直誤觸發，代表 `IMPACT_THRESHOLD`/`MAX_IMPACT_DURATION_MS` 需要之後再調整，先記錄現象，不在這個 task 裡調參數）
5. 敲一下、停頓超過 1 秒、再敲一下（間隔太大），確認不會觸發

- [ ] **Step 4: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/service/GestureAccessibilityService.kt
git commit -m "feat(gesture-toolkit): 敲背兩下觸發螢幕截圖，接上 BackTapDetector，實機驗證通過"
```

---

## 完成後

敲背手勢完成。之後每個新手勢（甩動點手電筒/轉腕開相機/翻轉靜音/貼耳擴音）都重用這裡的架構：`sensors/` 底下新增一個純邏輯 Detector（比照 `BackTapDetector` 的測試方式）+ 在 `GestureAccessibilityService` 裡接上對應的 Sensor 監聽與觸發邏輯，各自是獨立的下一個 spec + plan。
