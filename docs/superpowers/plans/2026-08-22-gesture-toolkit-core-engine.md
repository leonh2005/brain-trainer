# Gesture Toolkit Core Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 Gesture Toolkit（DroidLab 手勢工具 clone）的核心引擎：一個可安裝在 Pixel 5 上的 Android app，具備 Accessibility Service + 一組可測試、可擴充的系統動作（截圖/鎖定/Home/Back/Recents/音量/啟動App），並有一個設定畫面能手動驗證每個動作。

**Architecture:** `GestureAccessibilityService`（背景服務，透過 `performGlobalAction` 執行系統層動作）+ `Action` 介面（每個系統動作各自實作，含 `isAvailable()`/`execute()`）+ `ActionRegistry`（持有動作清單，供 UI 查詢）+ `ActionListScreen`（Jetpack Compose 設定畫面，列出所有動作並提供「測試」按鈕）。之後的感應器手勢模組（下一個 plan）只需要查 `ActionRegistry` 拿到對應 `Action` 並呼叫 `execute()`，不需要碰這裡的任何實作細節。

**Tech Stack:** Kotlin 2.3.20、Android Gradle Plugin 9.1.0、Gradle 9.3.1、Jetpack Compose（compose-bom 2026.08.00）、minSdk 28 / targetSdk・compileSdk 37、JUnit4（純 JVM 單元測試，不用 Robolectric/Mockito——見下方全域限制）

**Spec:** `docs/superpowers/specs/2026-08-22-gesture-toolkit-core-engine-design.md`

## Global Constraints

- 專案位置：`~/CCProject/gesture-toolkit/`，applicationId/namespace `com.steven.gesturetoolkit`
- 側載安裝，不上架 Play Store，不需要隱私政策/上架審核文件
- 單元測試一律用純 JVM JUnit4，**不引入 Robolectric 或 Mockito**——凡是需要真正呼叫 Android 框架行為（AccessibilityService/AudioManager/PackageManager 實際效果）的部分，一律留給手動 QA 驗證，不硬测試
- `Action` 介面簽名為 `isAvailable(): Boolean`（不帶 `Context` 參數）——比 spec 原文少一個參數，因為所有「是否可用」判斷實際上只需要檢查 `GestureAccessibilityService.instance` 是否為 null，不需要 Context；這樣單元測試不用假造 Context 物件
- 每個 Action 的 `id` 全域唯一，`ActionRegistry` 建構時會檢查並在重複時丟例外
- JAVA_HOME 使用 Android Studio 內建 JBR：`/Applications/Android Studio.app/Contents/jbr/Contents/Home`（已確認此路徑存在且 `java -version` 正常）
- **AGP 9.0+ 已內建 Kotlin 支援**：`app/build.gradle.kts` 不套用 `org.jetbrains.kotlin.android` plugin（套用會直接報錯，Gradle 官方訊息說 AGP 9.0 起不再需要），也不用 `kotlinOptions{}` DSL；`compileOptions{sourceCompatibility/targetCompatibility}` 已足夠。根目錄 `build.gradle.kts` 保留 `id("org.jetbrains.kotlin.android") version "2.3.20" apply false` 純聲明不影響（`apply false` 不會實際套用）
- **compileSdk/targetSdk = 37、AGP = 9.1.0、Gradle = 9.3.1**（原計畫是 36/9.0.1/9.1.0）：實作時發現 compose-bom 2026.08.00（Compose 1.12.0 系列）與 lifecycle-runtime-ktx 2.11.0 都要求 compileSdk≥37+AGP≥9.1.0，逐一降版下游函式庫會一直撞到同一波要求（2026年8月前後發布的 AndroidX 函式庫幾乎都一起把最低需求提高到 37），改為直接升級到 37/9.1.0 才是一次到位的解法。AGP 9.1.0 官方文件（developer.android.com/build/releases/agp-9-1-0-release-notes）明確要求 Gradle 最低/預設版本都是 **9.3.1**，所以 Gradle wrapper 也要跟著升到 9.3.1（不是 9.1.0）。本機已用 `sdkmanager "platforms;android-37.0"` 安裝好 android-37 platform（非 beta，`ro.build.version.sdk=37`，穩定版）。AGP 9.1.0 與 Gradle 9.3.1 本機都無快取，首次建置需要網路下載，屬預期行為

---

## Task 1: Gradle 專案骨架，建置出可安裝的空白畫面

**Files:**
- Create: `gesture-toolkit/settings.gradle.kts`
- Create: `gesture-toolkit/build.gradle.kts`
- Create: `gesture-toolkit/gradle.properties`
- Create: `gesture-toolkit/.gitignore`
- Create: `gesture-toolkit/app/build.gradle.kts`
- Create: `gesture-toolkit/app/src/main/AndroidManifest.xml`
- Create: `gesture-toolkit/app/src/main/res/values/strings.xml`
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/MainActivity.kt`

**Interfaces:**
- Produces: `MainActivity`（`ComponentActivity`）作為 app 進入點，之後任務會替換其 `setContent` 內容

- [ ] **Step 1: 建立專案目錄**

```bash
mkdir -p /Users/steven/CCProject/gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit
mkdir -p /Users/steven/CCProject/gesture-toolkit/app/src/main/res/values
mkdir -p /Users/steven/CCProject/gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit
```

- [ ] **Step 2: 寫 `settings.gradle.kts`**

```kotlin
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "gesture-toolkit"
include(":app")
```

- [ ] **Step 3: 寫根目錄 `build.gradle.kts`**

```kotlin
plugins {
    id("com.android.application") version "9.1.0" apply false
    id("org.jetbrains.kotlin.android") version "2.3.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20" apply false
}
```

- [ ] **Step 4: 寫 `gradle.properties`**

```properties
org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
kotlin.code.style=official
```

- [ ] **Step 5: 寫 `.gitignore`**

```
*.iml
.gradle/
/local.properties
/.idea/
.DS_Store
/build
/captures
.externalNativeBuild
.cxx
app/build/
```

- [ ] **Step 6: 寫 `app/build.gradle.kts`**

```kotlin
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.steven.gesturetoolkit"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.steven.gesturetoolkit"
        minSdk = 28
        targetSdk = 37
        versionCode = 1
        versionName = "0.1"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
    }

    sourceSets {
        getByName("main") {
            kotlin.srcDirs("src/main/kotlin")
        }
        getByName("test") {
            kotlin.srcDirs("src/test/kotlin")
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2026.08.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.core:core-ktx:1.18.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.11.0")

    testImplementation("junit:junit:4.13.2")
}
```

- [ ] **Step 7: 寫 `app/src/main/AndroidManifest.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <application
        android:allowBackup="true"
        android:icon="@android:drawable/ic_menu_manage"
        android:label="@string/app_name"
        android:theme="@android:style/Theme.Material.Light.NoActionBar">

        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

    </application>

</manifest>
```

- [ ] **Step 8: 寫 `app/src/main/res/values/strings.xml`**

```xml
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">Gesture Toolkit</string>
</resources>
```

- [ ] **Step 9: 寫 `MainActivity.kt`（先放暫時性畫面，Task 7 會換掉）**

```kotlin
package com.steven.gesturetoolkit

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    Text("Gesture Toolkit", modifier = Modifier.padding(16.dp))
                }
            }
        }
    }
}
```

- [ ] **Step 10: 用本機已快取的 Gradle 產生 wrapper（指向 9.3.1，AGP 9.1.0 官方要求的最低版本）**

```bash
cd /Users/steven/CCProject/gesture-toolkit
/Users/steven/.gradle/wrapper/dists/gradle-9.1.0-all/7wzd0jkjit61aq2p43wpjgij9/gradle-9.1.0/bin/gradle wrapper --gradle-version 9.3.1
```

Expected: 產生 `gradlew`、`gradlew.bat`、`gradle/wrapper/gradle-wrapper.properties`（內容會指向 9.3.1）、`gradle/wrapper/gradle-wrapper.jar`。本機沒有快取 Gradle 9.3.1，第一次執行 `./gradlew` 時會自動下載，屬預期行為

- [ ] **Step 11: 建置驗證**

```bash
cd /Users/steven/CCProject/gesture-toolkit
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`，產出 `app/build/outputs/apk/debug/app-debug.apk`

- [ ] **Step 12: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/
git commit -m "feat(gesture-toolkit): 專案骨架，空白 Compose 畫面可建置"
```

---

## Task 2: Action 介面 + ActionRegistry（TDD）

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/Action.kt`
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/ActionRegistry.kt`
- Test: `gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/ActionRegistryTest.kt`

**Interfaces:**
- Consumes: 無
- Produces: `interface Action { val id: String; val label: String; fun isAvailable(): Boolean; fun execute(context: Context) }`；`class ActionRegistry(actions: List<Action>) { fun get(id: String): Action? }`（建構時若有重複 id 會丟 `IllegalArgumentException`）

- [ ] **Step 1: 寫失敗的測試**

`gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/ActionRegistryTest.kt`：

```kotlin
package com.steven.gesturetoolkit.actions

import android.content.Context
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ActionRegistryTest {

    private fun fakeAction(actionId: String, actionLabel: String = "Test") = object : Action {
        override val id = actionId
        override val label = actionLabel
        override fun isAvailable() = true
        override fun execute(context: Context) {}
    }

    @Test
    fun `get returns action by id`() {
        val registry = ActionRegistry(listOf(fakeAction("a"), fakeAction("b")))
        assertEquals("a", registry.get("a")?.id)
    }

    @Test
    fun `get returns null for unknown id`() {
        val registry = ActionRegistry(listOf(fakeAction("a")))
        assertNull(registry.get("z"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `duplicate ids throw on construction`() {
        ActionRegistry(listOf(fakeAction("a"), fakeAction("a")))
    }
}
```

- [ ] **Step 2: 執行測試確認失敗（因為 Action/ActionRegistry 還不存在）**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.ActionRegistryTest"
```

Expected: 編譯失敗，找不到 `Action`/`ActionRegistry`

- [ ] **Step 3: 寫 `Action.kt`**

```kotlin
package com.steven.gesturetoolkit.actions

import android.content.Context

interface Action {
    val id: String
    val label: String
    fun isAvailable(): Boolean
    fun execute(context: Context)
}
```

- [ ] **Step 4: 寫 `ActionRegistry.kt`**

```kotlin
package com.steven.gesturetoolkit.actions

class ActionRegistry(val actions: List<Action>) {

    init {
        val ids = actions.map { it.id }
        require(ids.size == ids.toSet().size) { "Duplicate action ids: $ids" }
    }

    fun get(id: String): Action? = actions.find { it.id == id }
}
```

- [ ] **Step 5: 執行測試確認通過**

```bash
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.ActionRegistryTest"
```

Expected: `BUILD SUCCESSFUL`，3 個測試全通過

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/ gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/
git commit -m "feat(gesture-toolkit): Action 介面 + ActionRegistry"
```

---

## Task 3: GestureAccessibilityService 骨架 + 系統設定內可見

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/service/GestureAccessibilityService.kt`
- Create: `gesture-toolkit/app/src/main/res/xml/accessibility_service_config.xml`
- Modify: `gesture-toolkit/app/src/main/AndroidManifest.xml`
- Modify: `gesture-toolkit/app/src/main/res/values/strings.xml`

**Interfaces:**
- Produces: `GestureAccessibilityService.instance: GestureAccessibilityService?`（companion object，服務連線時為自己，斷線時為 null）——後續所有 Action 靠這個判斷 `isAvailable()`

- [ ] **Step 1: 寫 `GestureAccessibilityService.kt`**

```kotlin
package com.steven.gesturetoolkit.service

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent

class GestureAccessibilityService : AccessibilityService() {

    companion object {
        var instance: GestureAccessibilityService? = null
            private set
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // 本 app 只用這個服務執行 performGlobalAction，不需要監聽任何無障礙事件
    }

    override fun onInterrupt() {
        // 系統中斷服務時呼叫，本 app 無需特別處理
    }

    override fun onDestroy() {
        super.onDestroy()
        if (instance === this) {
            instance = null
        }
    }
}
```

- [ ] **Step 2: 新增 `strings.xml` 的服務說明文字**

修改 `gesture-toolkit/app/src/main/res/values/strings.xml`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<resources>
    <string name="app_name">Gesture Toolkit</string>
    <string name="accessibility_service_description">Gesture Toolkit 需要無障礙權限才能執行螢幕截圖、鎖定螢幕、返回/首頁/最近使用等系統層級動作。所有資料僅在本機處理，不會上傳。</string>
</resources>
```

- [ ] **Step 3: 寫 `app/src/main/res/xml/accessibility_service_config.xml`**

```bash
mkdir -p /Users/steven/CCProject/gesture-toolkit/app/src/main/res/xml
```

```xml
<?xml version="1.0" encoding="utf-8"?>
<accessibility-service xmlns:android="http://schemas.android.com/apk/res/android"
    android:accessibilityEventTypes="typeAllMask"
    android:accessibilityFeedbackType="feedbackGeneric"
    android:accessibilityFlags="flagDefault"
    android:canPerformGestures="true"
    android:canRetrieveWindowContent="false"
    android:description="@string/accessibility_service_description"
    android:notificationTimeout="100" />
```

- [ ] **Step 4: 修改 `AndroidManifest.xml`，在 `<application>` 內加入服務宣告**

在 `</activity>` 之後、`</application>` 之前加入：

```xml
        <service
            android:name=".service.GestureAccessibilityService"
            android:permission="android.permission.BIND_ACCESSIBILITY_SERVICE"
            android:exported="false">
            <intent-filter>
                <action android:name="android.accessibilityservice.AccessibilityService" />
            </intent-filter>
            <meta-data
                android:name="android.accessibilityservice.settings"
                android:resource="@xml/accessibility_service_config" />
        </service>
```

- [ ] **Step 5: 建置並安裝到 Pixel 5（手動 QA）**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew installDebug
```

Expected: `BUILD SUCCESSFUL`，Pixel 5 需先用 USB 連接並開啟「USB 偵錯」，`adb devices` 要能看到裝置

手動驗證步驟：
1. 打開 Pixel 5「設定 → 協助工具（Accessibility）→ 已下載的應用程式」
2. 確認清單裡看得到「Gesture Toolkit」
3. 點進去，開啟權限，確認顯示上面寫的說明文字，且系統沒有跳出錯誤或崩潰
4. 開啟後回到 app（此時看不到任何 UI 反應是正常的，因為 Task 7 才會做設定畫面）

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/service/ gesture-toolkit/app/src/main/res/xml/ gesture-toolkit/app/src/main/res/values/strings.xml gesture-toolkit/app/src/main/AndroidManifest.xml
git commit -m "feat(gesture-toolkit): GestureAccessibilityService 骨架，可在系統設定啟用"
```

---

## Task 4: 系統導覽動作（截圖/鎖定/Home/Back/Recents）

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/impl/SystemNavActions.kt`
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/AppActions.kt`
- Test: `gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/SystemNavActionsTest.kt`

**Interfaces:**
- Consumes: `Action`（Task 2）、`GestureAccessibilityService.instance`（Task 3）
- Produces: `ScreenshotAction`、`LockScreenAction`、`HomeAction`、`BackAction`、`RecentsAction`（皆為 `Action` 實作）；`object AppActions { val registry: ActionRegistry }`——之後任務會持續往這個 registry 加東西

- [ ] **Step 1: 寫失敗的測試**

`gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/SystemNavActionsTest.kt`：

```kotlin
package com.steven.gesturetoolkit.actions.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class SystemNavActionsTest {

    private fun allActions() = listOf(
        ScreenshotAction(), LockScreenAction(), HomeAction(), BackAction(), RecentsAction()
    )

    @Test
    fun `actions have unique non-blank ids and labels`() {
        val actions = allActions()
        val ids = actions.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
        actions.forEach {
            assertFalse(it.id.isBlank())
            assertFalse(it.label.isBlank())
        }
    }

    @Test
    fun `isAvailable is false when accessibility service not connected`() {
        // 單元測試環境沒有真正連線的 GestureAccessibilityService，instance 必為 null
        allActions().forEach { assertFalse(it.isAvailable()) }
    }
}
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.impl.SystemNavActionsTest"
```

Expected: 編譯失敗，找不到對應類別

- [ ] **Step 3: 寫 `SystemNavActions.kt`**

```kotlin
package com.steven.gesturetoolkit.actions.impl

import android.accessibilityservice.AccessibilityService
import android.content.Context
import com.steven.gesturetoolkit.actions.Action
import com.steven.gesturetoolkit.service.GestureAccessibilityService

private fun performGlobalActionOrNoop(globalAction: Int) {
    GestureAccessibilityService.instance?.performGlobalAction(globalAction)
}

class ScreenshotAction : Action {
    override val id = "screenshot"
    override val label = "螢幕截圖"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_TAKE_SCREENSHOT)
    }
}

class LockScreenAction : Action {
    override val id = "lock_screen"
    override val label = "鎖定螢幕"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN)
    }
}

class HomeAction : Action {
    override val id = "home"
    override val label = "回到主畫面"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_HOME)
    }
}

class BackAction : Action {
    override val id = "back"
    override val label = "返回"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_BACK)
    }
}

class RecentsAction : Action {
    override val id = "recents"
    override val label = "最近使用App"
    override fun isAvailable() = GestureAccessibilityService.instance != null
    override fun execute(context: Context) {
        performGlobalActionOrNoop(AccessibilityService.GLOBAL_ACTION_RECENTS)
    }
}
```

- [ ] **Step 4: 執行測試確認通過**

```bash
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.impl.SystemNavActionsTest"
```

Expected: `BUILD SUCCESSFUL`，2 個測試通過

- [ ] **Step 5: 建立 `AppActions.kt`，把這 5 個動作註冊進 registry**

```kotlin
package com.steven.gesturetoolkit.actions

import com.steven.gesturetoolkit.actions.impl.BackAction
import com.steven.gesturetoolkit.actions.impl.HomeAction
import com.steven.gesturetoolkit.actions.impl.LockScreenAction
import com.steven.gesturetoolkit.actions.impl.RecentsAction
import com.steven.gesturetoolkit.actions.impl.ScreenshotAction

object AppActions {
    val registry = ActionRegistry(
        listOf(
            ScreenshotAction(),
            LockScreenAction(),
            HomeAction(),
            BackAction(),
            RecentsAction(),
        )
    )
}
```

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/ gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/
git commit -m "feat(gesture-toolkit): 系統導覽動作（截圖/鎖定/Home/Back/Recents）"
```

---

## Task 5: 音量調整動作

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/impl/VolumeActions.kt`
- Modify: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/AppActions.kt`
- Test: `gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/VolumeActionsTest.kt`

**Interfaces:**
- Consumes: `Action`（Task 2）
- Produces: `VolumeUpAction`、`VolumeDownAction`

- [ ] **Step 1: 寫失敗的測試**

```kotlin
package com.steven.gesturetoolkit.actions.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class VolumeActionsTest {

    @Test
    fun `volume actions are always available with valid metadata`() {
        val actions = listOf(VolumeUpAction(), VolumeDownAction())
        actions.forEach {
            assertTrue(it.isAvailable())
            assertFalse(it.id.isBlank())
            assertFalse(it.label.isBlank())
        }
        assertEquals(2, actions.map { it.id }.toSet().size)
    }
}
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.impl.VolumeActionsTest"
```

Expected: 編譯失敗，找不到 `VolumeUpAction`/`VolumeDownAction`

- [ ] **Step 3: 寫 `VolumeActions.kt`**

```kotlin
package com.steven.gesturetoolkit.actions.impl

import android.content.Context
import android.media.AudioManager
import com.steven.gesturetoolkit.actions.Action

class VolumeUpAction : Action {
    override val id = "volume_up"
    override val label = "音量 +"
    override fun isAvailable() = true
    override fun execute(context: Context) {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_RAISE, AudioManager.FLAG_SHOW_UI)
    }
}

class VolumeDownAction : Action {
    override val id = "volume_down"
    override val label = "音量 -"
    override fun isAvailable() = true
    override fun execute(context: Context) {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
        am.adjustStreamVolume(AudioManager.STREAM_MUSIC, AudioManager.ADJUST_LOWER, AudioManager.FLAG_SHOW_UI)
    }
}
```

- [ ] **Step 4: 執行測試確認通過**

```bash
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.impl.VolumeActionsTest"
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 5: 修改 `AppActions.kt`，加入這兩個動作**

```kotlin
package com.steven.gesturetoolkit.actions

import com.steven.gesturetoolkit.actions.impl.BackAction
import com.steven.gesturetoolkit.actions.impl.HomeAction
import com.steven.gesturetoolkit.actions.impl.LockScreenAction
import com.steven.gesturetoolkit.actions.impl.RecentsAction
import com.steven.gesturetoolkit.actions.impl.ScreenshotAction
import com.steven.gesturetoolkit.actions.impl.VolumeDownAction
import com.steven.gesturetoolkit.actions.impl.VolumeUpAction

object AppActions {
    val registry = ActionRegistry(
        listOf(
            ScreenshotAction(),
            LockScreenAction(),
            HomeAction(),
            BackAction(),
            RecentsAction(),
            VolumeUpAction(),
            VolumeDownAction(),
        )
    )
}
```

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/ gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/VolumeActionsTest.kt
git commit -m "feat(gesture-toolkit): 音量調整動作"
```

---

## Task 6: 啟動 App 動作

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/impl/LaunchAppAction.kt`
- Modify: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/AppActions.kt`
- Test: `gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/LaunchAppActionTest.kt`

**Interfaces:**
- Consumes: `Action`（Task 2）
- Produces: `class LaunchAppAction(packageName: String, label: String) : Action`；`data class InstalledAppInfo(packageName: String, label: String)`；`fun filterLaunchableApps(all: List<InstalledAppInfo>, excludeSelfPackage: String): List<InstalledAppInfo>`（供 Task 7 UI 的 App 選擇器使用）

- [ ] **Step 1: 寫失敗的測試**

```kotlin
package com.steven.gesturetoolkit.actions.impl

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LaunchAppActionTest {

    @Test
    fun `id is namespaced by package name to stay unique per app`() {
        val chrome = LaunchAppAction("com.android.chrome", "Chrome")
        val camera = LaunchAppAction("com.google.android.GoogleCamera", "相機")
        assertNotEquals(chrome.id, camera.id)
        assertTrue(chrome.id.contains("com.android.chrome"))
    }

    @Test
    fun `filterLaunchableApps excludes self and duplicates, sorts by label`() {
        val input = listOf(
            InstalledAppInfo("com.example.self", "Gesture Toolkit"),
            InstalledAppInfo("com.b", "Bravo"),
            InstalledAppInfo("com.a", "Alpha"),
            InstalledAppInfo("com.a", "Alpha"),
        )
        val result = filterLaunchableApps(input, excludeSelfPackage = "com.example.self")
        assertEquals(listOf("com.a", "com.b"), result.map { it.packageName })
    }
}
```

- [ ] **Step 2: 執行測試確認失敗**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.impl.LaunchAppActionTest"
```

Expected: 編譯失敗

- [ ] **Step 3: 寫 `LaunchAppAction.kt`**

```kotlin
package com.steven.gesturetoolkit.actions.impl

import android.content.Context
import android.content.Intent
import android.widget.Toast
import com.steven.gesturetoolkit.actions.Action

class LaunchAppAction(private val packageName: String, override val label: String) : Action {
    override val id = "launch_app:$packageName"
    override fun isAvailable() = true
    override fun execute(context: Context) {
        val intent = context.packageManager.getLaunchIntentForPackage(packageName)
        if (intent == null) {
            Toast.makeText(context, "找不到 App：$label", Toast.LENGTH_SHORT).show()
            return
        }
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }
}

data class InstalledAppInfo(val packageName: String, val label: String)

fun filterLaunchableApps(all: List<InstalledAppInfo>, excludeSelfPackage: String): List<InstalledAppInfo> =
    all.filter { it.packageName != excludeSelfPackage }
        .distinctBy { it.packageName }
        .sortedBy { it.label }
```

- [ ] **Step 4: 執行測試確認通過**

```bash
./gradlew testDebugUnitTest --tests "com.steven.gesturetoolkit.actions.impl.LaunchAppActionTest"
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 5: 修改 `AppActions.kt`，加入一個示範用的啟動相機動作**

```kotlin
package com.steven.gesturetoolkit.actions

import com.steven.gesturetoolkit.actions.impl.BackAction
import com.steven.gesturetoolkit.actions.impl.HomeAction
import com.steven.gesturetoolkit.actions.impl.LaunchAppAction
import com.steven.gesturetoolkit.actions.impl.LockScreenAction
import com.steven.gesturetoolkit.actions.impl.RecentsAction
import com.steven.gesturetoolkit.actions.impl.ScreenshotAction
import com.steven.gesturetoolkit.actions.impl.VolumeDownAction
import com.steven.gesturetoolkit.actions.impl.VolumeUpAction

object AppActions {
    val registry = ActionRegistry(
        listOf(
            ScreenshotAction(),
            LockScreenAction(),
            HomeAction(),
            BackAction(),
            RecentsAction(),
            VolumeUpAction(),
            VolumeDownAction(),
            LaunchAppAction("com.google.android.GoogleCamera", "相機"),
        )
    )
}
```

註：Pixel 5 內建相機 package name 是 `com.google.android.GoogleCamera`；Task 7 的手動 QA 若發現這個 package 在該裝置上不對，用 `adb shell pm list packages | grep -i camera` 查真正的名稱再改這裡。

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/actions/ gesture-toolkit/app/src/test/kotlin/com/steven/gesturetoolkit/actions/impl/LaunchAppActionTest.kt
git commit -m "feat(gesture-toolkit): 啟動 App 動作"
```

---

## Task 7: 設定畫面 UI + 全功能手動驗收

**Files:**
- Create: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/ui/ActionListScreen.kt`
- Modify: `gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/MainActivity.kt`

**Interfaces:**
- Consumes: `AppActions.registry`（Task 4-6）、`GestureAccessibilityService.instance`（Task 3）
- Produces: `@Composable fun ActionListScreen()`

- [ ] **Step 1: 寫 `ActionListScreen.kt`**

```kotlin
package com.steven.gesturetoolkit.ui

import android.content.Intent
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.steven.gesturetoolkit.actions.AppActions
import com.steven.gesturetoolkit.service.GestureAccessibilityService

@Composable
fun ActionListScreen() {
    val context = LocalContext.current
    var refreshTick by remember { mutableStateOf(0) }
    val serviceEnabled = remember(refreshTick) { GestureAccessibilityService.instance != null }

    Column(modifier = Modifier.fillMaxSize().padding(16.dp)) {
        Text("Gesture Toolkit", style = MaterialTheme.typography.headlineSmall)
        Spacer(Modifier.height(12.dp))

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(12.dp)) {
                Text(if (serviceEnabled) "無障礙服務：已啟用" else "無障礙服務：尚未啟用")
                Spacer(Modifier.height(8.dp))
                Row {
                    Button(onClick = {
                        context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    }) {
                        Text("前往設定")
                    }
                    Spacer(Modifier.width(8.dp))
                    Button(onClick = { refreshTick++ }) {
                        Text("重新整理狀態")
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        LazyColumn {
            items(AppActions.registry.actions) { action ->
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(action.label)
                    Button(
                        onClick = { action.execute(context) },
                        enabled = action.isAvailable(),
                    ) {
                        Text("測試")
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 2: 修改 `MainActivity.kt`，改用 `ActionListScreen`**

```kotlin
package com.steven.gesturetoolkit

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import com.steven.gesturetoolkit.ui.ActionListScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface {
                    ActionListScreen()
                }
            }
        }
    }
}
```

- [ ] **Step 3: 建置並跑一次全部單元測試**

```bash
cd /Users/steven/CCProject/gesture-toolkit
./gradlew testDebugUnitTest
```

Expected: 所有測試（Task 2/4/5/6 累積下來）全部通過

- [ ] **Step 4: 安裝到 Pixel 5**

```bash
./gradlew installDebug
adb shell am start -n com.steven.gesturetoolkit/.MainActivity
```

- [ ] **Step 5: 完整手動 QA 清單（照順序做，任何一項失敗都要記下來回報）**

1. App 開啟後看到「無障礙服務：尚未啟用」+ 動作清單，每個動作旁的「測試」按鈕應該是灰色（disabled）——只有音量、啟動相機這兩類例外，因為它們不靠無障礙服務
2. 點「前往設定」→ 應該直接跳到系統的協助工具設定頁
3. 開啟 Gesture Toolkit 的無障礙權限，返回 app，點「重新整理狀態」→ 應顯示「已啟用」，原本灰色的按鈕應變成可點擊
4. 逐一點擊「測試」按鈕，確認：
   - 螢幕截圖 → 真的截圖並存進相簿
   - 鎖定螢幕 → 螢幕真的鎖定（需要重新解鎖才能繼續測試）
   - 回到主畫面 → 真的跳回桌面
   - 返回 → 有反應（在 app 內測試時效果不明顯，屬預期）
   - 最近使用App → 真的開啟多工畫面
   - 音量 +／音量 - → 音量真的調整，且螢幕上出現系統音量 UI
   - 啟動 App（相機）→ 真的開啟相機 App；若沒反應，用 `adb shell pm list packages | grep -i camera` 查正確 package name，回 Task 6 Step 5 修正後重新 `installDebug`

- [ ] **Step 6: Commit**

```bash
cd /Users/steven/CCProject
git add gesture-toolkit/app/src/main/kotlin/com/steven/gesturetoolkit/
git commit -m "feat(gesture-toolkit): 設定畫面 UI，Core Engine 手動驗收通過"
```

---

## 完成後

Core Engine 完成，Steven 可以在 Pixel 5 上手動測試每個系統動作。下一步是感應器手勢偵測模組（獨立的下一份 spec + plan），會查詢這裡的 `AppActions.registry` 來綁定「敲背/甩動/轉腕」等手勢對應到哪個 Action。
