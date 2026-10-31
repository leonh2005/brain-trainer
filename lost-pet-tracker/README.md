# 雙北走失動物協尋追蹤

抓 FB / Threads / PTT 上「雙北 ＋ 動物 ＋ 有獎金」的走失協尋貼文，
貼文**持續存在滿 3 天**才推 Telegram。

## 執行

```bash
python3 tracker.py
```

```bash
python3 tracker.py --check   # 只檢查既有案件是否結案，不重新掃描
```

## 排程（每 12 小時檢查結案）

| 時間 | LaunchAgent | 做什麼 |
|---|---|---|
| 09:00 | `com.steven.lost-pet-tracker` | 掃新貼文 + 推播滿 3 天的案件 + 檢查結案 |
| 21:00 | `com.steven.lost-pet-resolver` | 只檢查結案 |

```bash
launchctl print gui/$(id -u)/com.steven.lost-pet-tracker   # 看狀態
launchctl kickstart gui/$(id -u)/com.steven.lost-pet-tracker  # 手動觸發
```

結案關鍵字：已尋獲／已找到／已回家／已找回／順利回家／已平安／已結案／已認領／找到主人。
含「尚未尋獲」「還沒找到」等否定用語時不算結案。已結案的貼文不會再被推播。

## 篩選條件

| 條件 | 判定方式 |
|---|---|
| 動物 | 狗/犬/貓/毛孩/鳥/鸚鵡/兔/鼠/蜜袋鼯/烏龜 |
| 走失 | 走失/協尋/失蹤/尋找/不見/遺失 |
| 獎金 | 懸賞/獎金/賞金/紅包/酬謝/重賞/答謝/感謝金，或文中金額（支援「三萬」中文數字） |
| 雙北 | 台北/臺北/新北/雙北，或雙北 41 個區名／常見地名；出現其他縣市且無雙北字樣則排除 |

## 「持續三天」怎麼判定

1. 貼文第一次被掃到 → 記錄 `first_seen`
2. 之後每天掃到 → 更新 `last_seen`
3. 距 `first_seen` ≥ 3 天時：
   - 有貼文 URL → 重抓該頁確認還在（404 / 已刪除就不算）
   - 沒有 URL（FB 搜尋結果抓不到連結）→ 改用「實際被掃到幾天」判斷
4. 通過就推 Telegram，並標記 `notified` 避免重複

## 資料

- `data/posts.db` — `posts`（貼文）＋ `sightings`（每次掃到的日期）
- `data/run.log` — 每次執行的 log

想重來就刪掉 `data/posts.db`。

## 已知限制

- **FB 抓取率低**：headless Chromium 對 FB 搜尋頁只能拿到整頁文字裡的長句段，
  每組關鍵字約 0-4 篇。要更完整需要驅動已登入的真瀏覽器，成本較高。
- **Threads** 每次約 3-7 篇，品質較好（貼文連結完整，可驗證存活）。
- **PTT** 三個板各 20 篇，但雙北＋獎金的貼文很少（多為認養、交易）。
- 條件很窄，**可能好幾天才有一篇**符合，屬正常。

## 診斷腳本

`probe.py` / `fb_*.py` 是開發期的一次性探測腳本，可刪。
