#!/bin/bash
# 一次性排程：下午5點繼續排查 Mac 端 Google Drive 沒有把新檔案上傳到雲端的問題
# 2026-09-10 由 Steven 要求排定，跑完後自我卸載 LaunchAgent（不留常駐）

LOG="$HOME/CCProject/logs/gdrive_sync_diagnose.log"
PLIST="$HOME/Library/LaunchAgents/com.steven.gdrive-sync-diagnose-oneshot.plist"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 開始執行" >> "$LOG"

cd "$HOME/CCProject" && claude -p "$(cat <<'PROMPT'
延續稍早的診斷：Steven 的 Mac 上，Google Drive 桌面版沒有把新檔案上傳到雲端，導致手機 Obsidian（透過 DriveSync App）收不到更新。

## 已確認的背景（不用重查）
- 手機端 DriveSync 已經修好：Android省電白名單、同步路徑（從舊名「Obsidian Vault」改成新名「from Google keep」）都改對了，DriveSync自己顯示「準備完成、無錯誤」
- 真正卡住的是 Mac 端：測試檔案 `/Users/steven/我的雲端硬碟/📚 學習 & 筆記/from Google keep/Resources/_同步測試_請刪除.md` 已經存在超過4小時，但透過 `https://drive.google.com/drive/search?q=同步測試` 查雲端網頁版完全找不到這個檔案（用 browser_cookie3 抓 Firefox 的 google.com cookies + Playwright headless firefox 帶入登入態查詢，cookie檔在 `/Users/steven/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release/cookies.sqlite`）
- Google Drive App 的「錯誤清單」顯示「沒有問題」——不是報錯卡住，是本機檔案監聽器根本沒偵測到這個新檔案
- 已經試過：修改檔案內容重新觸發、暫停/恢復同步（selector：選單列圖示→齒輪→暫停/播放按鈕）——都沒讓這個檔案被排進同步佇列
- Google Drive 帳號空間 15.53GB/17GB (91-92%)已滿但還沒到100%，理論上不該是原因，但可以順便確認一下

## 這次要做的事
1. 先重新驗證問題是否還存在：查詢雲端網頁版看那個測試檔案現在出現了沒有（可能已經自己好了）
2. 如果還沒出現，嘗試更強力的修復手段：
   - 完全結束 Google Drive App（quit，不只是暫停）再重新啟動，強制它做完整的本機掃描
   - 或檢查 Google Drive 偏好設定裡「我的雲端硬碟同步」的資料夾範圍設定，確認「Resources」子資料夾沒被排除在同步範圍外
   - 檢查儲存空間滿了是不是真的無關（可以查官方文件或錯誤訊息判斷）
3. 驗證修復是否成功：重新查詢雲端網頁版，確認測試檔案出現
4. 如果成功了，額外確認一次全量：比對 Mac 本機 `from Google keep` 資料夾的檔案數量，跟手機 `/storage/emulated/0/DriveSyncFiles` 的檔案數量（用 `adb shell find ... -type f | wc -l`，需要手機透過USB連著且已解鎖螢幕、USB偵錯模式開著——如果沒連著就跳過這步，在報告裡註明）
5. 不管成不成功，都把完整過程和結論寫成清楚的中文摘要

## 完成後
把診斷結果和已經做了什麼、還剩什麼問題，寫進一個新檔案 `~/CCProject/logs/gdrive_sync_diagnose_result.md`（中文，簡潔），並透過 Telegram bot（token 在 `~/CCProject/.secrets/telegram_token.txt`，chat_id 7556217543）發送摘要通知 Steven。
PROMPT
)" --permission-mode acceptEdits --allowedTools "Read,Write,Edit,Bash,Grep,Glob,WebFetch" --disallowedTools "Agent,Workflow" --output-format text >> "$LOG" 2>&1

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 執行完成" >> "$LOG"

# 一次性任務，跑完自我卸載
launchctl bootout "gui/$(id -u)" "$PLIST" 2>>"$LOG"
rm -f "$PLIST"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已自我卸載" >> "$LOG"
