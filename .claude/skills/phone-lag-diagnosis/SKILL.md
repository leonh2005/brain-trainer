---
name: Phone Lag Diagnosis
description: 診斷 Steven 的 Pixel 5 手機變慢/卡住，透過無線 adb 找出根因，不要一開口就叫他重開機
---

## Phone Lag Diagnosis

Steven 的手機是 **Pixel 5**，已開啟 Wireless debugging，與 Mac 在同一區網。收到「手機變慢/卡/lag」時，**先查再說**，不要直接建議重開機。

### Steps

1. **確認連線**
   ```bash
   /Users/steven/Library/Android/sdk/platform-tools/adb devices -l
   ```
   若沒有 `device` 狀態的裝置：請 Steven 確認手機有連 WiFi、Developer options 裡 Wireless debugging 是否還開著（螢幕關太久或重開過機會斷線，需要手動再配對一次）。

2. **抓 CPU / 記憶體概況**
   ```bash
   ADB=/Users/steven/Library/Android/sdk/platform-tools/adb
   $ADB shell dumpsys cpuinfo | head -20
   $ADB shell dumpsys meminfo | head -15
   ```
   特別注意 `android.hardware.keymaster@4.0-service-qti` 和 `keystore2` 這兩個系統程序 — 若吃到 50%+ CPU，代表硬體加密晶片卡住了（見下方已知根因)。

3. **找觸發源（ANR / 背景綁定逾時）**
   ```bash
   $ADB logcat -d -b events -t 2000 | grep -i am_anr | tail -20
   $ADB logcat -d -t 500 | grep -iE "keystore|keymaster" | tail -20
   ```
   `am_anr` 那行會直接列出是哪個 App 的 package name 卡住（例如 `Timed out while trying to bind`）。

4. **判斷根因後的處置**
   - 若是**單一 App** 背景綁定卡住（loop）：`$ADB shell am force-stop <package>` 先治標。
   - 若是 **keymaster/keystore2 卡在 BACKEND_BUSY**：這是系統底層硬體加密服務卡死，force-stop 個別 App 沒用（曾實測過，被拖下水的 App 會換一批）。非 root 手機**沒有替代路徑**，唯一解法是重開機：
     ```bash
     $ADB -s <device> shell reboot
     ```

5. **順手檢查夜間自動重開機是否有正常運作**（這是預防機制，壞掉就會一直累積到卡死）
   ```bash
   tail -20 /Users/steven/CCProject/logs/phone_reboot.log
   crontab -l | grep phone_weekly_reboot
   ```
   log 裡如果連續好幾天都是「找不到無線 adb 裝置，跳過」，代表手機半夜沒連線/螢幕太久沒亮導致 wireless adb 斷線，這個排程救不到——要跟 Steven 說清楚，不要默默略過。

### 已知根因（歷史案例，2026-08-29 查過）

**Yahoo 系列 App**（`com.yahoo.mobile.client.android.superapp` 奇摩股市 / `com.yahoo.mobile.client.android.TWStock` 台股）背景服務綁定逾時 → 被系統反覆 kill 又拉起 → 拖垮硬體加密晶片 keymaster/keystore2 → 全機所有需要憑證/加密驗證的操作卡死等待逾時。這是**系統層級**問題，不是任何一個 App 單獨的 bug，force-stop 治標不治本，只有重開機能重置。

### 已知配套 bug（曾修過，若又復發要重查）

`scripts/phone_weekly_reboot.sh` 每晚 00:00 用 cron 跑，透過無線 adb 自動重開機來預防上述累積。曾經因為腳本內用裸指令 `adb`（cron 環境 PATH 精簡、不含 Android SDK 路徑）導致整支腳本失敗退出，已改成呼叫 adb 絕對路徑修復。但即使修好指令本身，**手機半夜若斷線/離線，wireless adb 還是連不到，排程一樣會跳過**——這是目前尚未解決的已知限制，每次診斷都要順手看一下 log 有沒有連續失敗。
