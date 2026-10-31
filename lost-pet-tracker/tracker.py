"""雙北走失動物協尋（有獎金）追蹤

每天掃 FB / Threads / PTT，記錄貼文首次出現時間。
貼文持續存在滿 3 天且仍在線上，才推 Telegram。

執行：python3 tracker.py
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "posts.db")
FIREFOX_COOKIE = os.path.expanduser(
    "~/Library/Application Support/Firefox/Profiles/ro7nczf2.default-release/cookies.sqlite"
)
CHROME = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-1208/chrome-mac-arm64/"
    "Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
)
TELEGRAM_TOKEN = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
TELEGRAM_CHAT = "7556217543"
NOTIFY_AFTER_DAYS = 3

# 實測：FB 中文複合詞搜尋會把結果吃光，單詞＋地區效果最好
FB_QUERIES = ["協尋 台北", "協尋 新北", "走失 台北", "走失 新北", "懸賞 走失"]
THREADS_QUERIES = ["協尋 走失", "走失 懸賞"]
PTT_BOARDS = ["Pet_Get", "cat", "dog"]

# 台北市 12 區（與其他縣市有同名區，需搭配「台北」字樣才算）
TAIPEI_DISTRICTS = [
    "中正", "大同", "中山", "松山", "大安", "萬華", "信義", "士林", "北投", "內湖",
    "南港", "文山",
]
# 新北市 29 區：這些區名在其他縣市罕見，可單獨當雙北證據
NEW_TAIPEI_UNIQUE = [
    "板橋", "三重", "中和", "永和", "新莊", "新店", "樹林", "鶯歌", "三峽", "淡水",
    "汐止", "瑞芳", "土城", "蘆洲", "五股", "泰山", "林口", "深坑", "石碇", "坪林",
    "三芝", "石門", "八里", "平溪", "雙溪", "貢寮", "金山", "萬里", "烏來",
]
COMMON_LANDMARKS = [
    "東湖", "天母", "石牌", "公館", "景美", "木柵", "頂溪", "江子翠", "府中",
    "亞東", "迴龍", "丹鳳", "輔大", "新板", "北大特區",
]
DISTRICTS = TAIPEI_DISTRICTS + NEW_TAIPEI_UNIQUE + COMMON_LANDMARKS
TWIN_CITIES = ["台北", "臺北", "新北", "雙北"]
# 其他縣市：出現這些且沒有台北/新北字樣 → 不是雙北（避免「中正」被高雄中正路吃進來的誤判）
OTHER_CITIES = ["基隆", "桃園", "新竹", "苗栗", "台中", "臺中", "彰化", "南投", "雲林",
                "嘉義", "台南", "臺南", "高雄", "屏東", "宜蘭", "花蓮", "台東", "臺東",
                "澎湖", "金門", "連江", "馬祖"]
ANIMALS = ["狗", "犬", "貓", "毛孩", "鳥", "鸚鵡", "兔", "鼠", "蜜袋鼯", "烏龜", "倉鼠"]
LOST_WORDS = ["走失", "協尋", "失蹤", "尋找", "不見", "遺失"]
REWARD_WORDS = ["懸賞", "獎金", "賞金", "紅包", "酬謝", "重賞", "答謝", "感謝金", "謝金"]
# 結案用語：要求「已/已經/順利/平安」前綴，避免「希望能幫牠找到主人」「請幫忙找到」誤判
RESOLVED_PATTERN = re.compile(r"(?:已經|已|順利|平安)(?:尋獲|找到|回家|找回|結案|認領)")
RESOLVE_ALERT_WORDS = ["尚未尋獲", "還沒找到", "尚未找到", "未尋獲", "仍找不到"]


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


# ─── 資料庫 ───────────────────────────────────────────────────────────


def init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            url TEXT NOT NULL,
            text TEXT,
            district TEXT,
            reward TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            notified INTEGER DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sightings (
            post_id TEXT NOT NULL,
            seen_date TEXT NOT NULL,
            PRIMARY KEY (post_id, seen_date)
        )"""
    )
    # 舊 DB 沒有結案欄位，就地補上
    cols = {row[1] for row in conn.execute("PRAGMA table_info(posts)")}
    if "resolved" not in cols:
        conn.execute("ALTER TABLE posts ADD COLUMN resolved INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE posts ADD COLUMN resolved_at TEXT")
    if "dead_misses" not in cols:
        # 抓不到頁面的次數；連續 2 次才判定貼文已刪，避免 PTT 臨時限流把案件全部判死
        conn.execute("ALTER TABLE posts ADD COLUMN dead_misses INTEGER DEFAULT 0")
    conn.commit()
    return conn


def detect_resolved(text: str) -> bool:
    """貼文是否已標記結案（有否定用語時不算）"""
    if not text:
        return False
    if any(w in text for w in RESOLVE_ALERT_WORDS):
        return False
    return bool(RESOLVED_PATTERN.search(text))


# ─── 篩選 ─────────────────────────────────────────────────────────────


def classify(text: str) -> dict | None:
    """判斷是否為雙北＋動物＋有獎金的協尋文，回傳 {district, reward}"""
    if not text or len(text) < 10:
        return None

    has_animal = any(a in text for a in ANIMALS)
    has_lost = any(w in text for w in LOST_WORDS)
    has_reward_word = any(w in text for w in REWARD_WORDS)
    # 數字金額：3萬、5000元、$8000
    has_amount = bool(re.search(r"\d+\s*[萬元]|\$\s*\d{3,}|\d{4,}\s*元", text))
    if not (has_animal and has_lost and (has_reward_word or has_amount)):
        return None

    # 新北專屬區名可單獨成立；台北市區名要有「台北」才成立
    location = next((d for d in NEW_TAIPEI_UNIQUE if d in text), None)
    if not location and any(c in text for c in TWIN_CITIES):
        location = next((d for d in COMMON_LANDMARKS if d in text), None) or "台北"
    if not location:
        return None

    # 其他縣市若比雙北證據更早出現，代表主場不在雙北
    # （「台中北屯走失，台北的朋友幫分享」要擋掉；「新竹認養的狗在板橋走失」也會被擋，寧可漏推）
    other_pos = min((text.find(c) for c in OTHER_CITIES if c in text), default=-1)
    if other_pos >= 0:
        twin_pos = min((text.find(c) for c in TWIN_CITIES if c in text), default=-1)
        loc_pos = min((text.find(d) for d in DISTRICTS if d in text), default=-1)
        main_pos = min((p for p in (twin_pos, loc_pos) if p >= 0), default=-1)
        if main_pos < 0 or other_pos < main_pos:
            return None

    # 金額可能是阿拉伯數字也可能是中文（「獎金三萬」很常見）
    cn = "一二三四五六七八九十百千萬兩"
    reward = None
    match = re.search(
        rf"(?:{'|'.join(REWARD_WORDS)})[^\d{cn}]{{0,6}}([\d{cn}]+(?:\.\d+)?\s*[萬元]?)", text
    )
    if match:
        reward = match.group(1)
    elif has_amount:
        found = re.search(r"\d+(?:\.\d+)?\s*[萬元]", text)
        reward = found.group(0) if found else None

    return {"district": location, "reward": reward}


# ─── 抓取：Playwright（FB / Threads）─────────────────────────────────


def load_cookies(host_like: str) -> list[dict]:
    con = sqlite3.connect(f"file:{FIREFOX_COOKIE}?immutable=1", uri=True)
    rows = con.execute(
        "SELECT host,name,value,path,isSecure,isHttpOnly FROM moz_cookies WHERE host LIKE ?",
        (f"%{host_like}%",),
    ).fetchall()
    con.close()
    return [
        {
            "name": n,
            "value": v,
            "domain": h if h.startswith(".") else "." + h,
            "path": p,
            "secure": bool(s),
            "httpOnly": bool(hh),
        }
        for h, n, v, p, s, hh in rows
    ]


def scrape_facebook(page) -> list[dict]:
    """FB 貼文搜尋；抓所有貼文連結與其文字"""
    results = []
    for query in FB_QUERIES:
        url = f"https://www.facebook.com/search/posts?q={urllib.parse.quote(query)}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4500)
            for _ in range(6):  # 滾動載入更多
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2200)
            # 實測：headless 下 role=article / feed 子元素都抓不到貼文正文，
            # 只有整頁 innerText 裡的長句段是真的貼文（其餘是專頁卡片與導覽雜訊）。
            body_text = page.inner_text("body")
            added = 0
            for line in body_text.split("\n"):
                text = line.strip()
                if len(text) < 40 or text.count("Facebook") > 3:
                    continue
                results.append({
                    "id": f"fb:{hashlib_id(text[:200])}",
                    "platform": "facebook",
                    "url": "",
                    "text": text[:800],
                })
                added += 1
            log(f"FB「{query}」→ {added} 段")
        except Exception as e:
            log(f"FB「{query}」失敗：{type(e).__name__}: {str(e)[:80]}")
    return results


def scrape_threads(page) -> list[dict]:
    results = []
    for query in THREADS_QUERIES:
        url = f"https://www.threads.com/search?q={urllib.parse.quote(query)}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(4000)
            for _ in range(6):
                page.keyboard.press("End")
                page.wait_for_timeout(2000)
            items = page.eval_on_selector_all(
                'article, div[data-pressable-container]',
                """els => els.map(e => {
                    const a = e.querySelector('a[href*="/post/"]');
                    return {href: a ? a.href : '', text: (e.innerText || '').slice(0, 600)};
                })""",
            )
            for item in items:
                href = item.get("href", "")
                text = (item.get("text") or "").strip()
                if "/post/" not in href or len(text) < 15:
                    continue
                results.append({
                    "id": f"threads:{hashlib_id(href)}",
                    "platform": "threads",
                    "url": href.split("?")[0],
                    "text": text,
                })
            log(f"Threads「{query}」→ {len(items)} 個連結")
        except Exception as e:
            log(f"Threads「{query}」失敗：{type(e).__name__}: {str(e)[:80]}")
    return results


def hashlib_id(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode()).hexdigest()[:16]


# ─── 抓取：PTT（純 HTTP）─────────────────────────────────────────────


def scrape_ptt() -> list[dict]:
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    for board in PTT_BOARDS:
        for keyword in ("協尋", "走失"):
            url = f"https://www.ptt.cc/bbs/{board}/search?q={urllib.parse.quote(keyword)}"
            try:
                req = urllib.request.Request(url, headers=headers)
                html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
            except Exception as e:
                log(f"PTT {board}/{keyword} 失敗：{type(e).__name__}")
                continue
            entries = re.findall(
                r'<div class="r-ent">.*?<a href="(/bbs/[^"]+)">([^<]+)</a>', html, re.S
            )
            for path, title in entries:
                results.append({
                    "id": f"ptt:{hashlib_id(path)}",
                    "platform": "ptt",
                    "url": f"https://www.ptt.cc{path}",
                    "text": title.replace("&quot;", '"'),
                })
            log(f"PTT {board}「{keyword}」→ {len(entries)} 篇")

    # 標題常只寫「[協尋] 板橋 走失柴犬」，金額多在內文 → 對可能相關的補抓文章頁
    need_body = [
        r for r in results
        if any(a in r["text"] for a in ANIMALS) and any(w in r["text"] for w in LOST_WORDS)
    ][:10]
    for item in need_body:
        html = fetch_html(item["url"])
        if not html:
            continue
        item["text"] = f"{item['text']} || {re.sub(r'<[^>]+>', ' ', html)[:1500]}"
    log(f"PTT 補抓內文 {len(need_body)} 篇")
    return results


# ─── 存活驗證 ────────────────────────────────────────────────────────


def fetch_html(url: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except Exception:
        return None


def is_still_alive(url: str) -> bool:
    """重抓貼文頁，確認還在。

    注意不要再比對 "404" 字串：Threads 的 HTML 內嵌 CSS 有 #3E4042 這種色碼，
    會讓所有 Threads 貼文被誤判成已刪除。
    """
    html = fetch_html(url)
    if html is None:
        return False
    markers = ["這則內容目前無法顯示", "content isn't available", "已被刪除"]
    return len(html) > 2000 and not any(m in html for m in markers)


def check_resolved(conn: sqlite3.Connection) -> None:
    """每半天跑一次：重抓有 URL 的案件，偵測是否已結案並通知"""
    cutoff = (date.today() - timedelta(days=90)).isoformat()
    rows = conn.execute(
        "SELECT id, url, district, reward, text, dead_misses FROM posts "
        "WHERE resolved = 0 AND url != '' AND dead_misses < 2 AND first_seen >= ?",
        (cutoff,),
    ).fetchall()
    if not rows:
        log("沒有需要檢查結案的案件")
        return

    log(f"檢查 {len(rows)} 件是否結案")
    alerts, resolved_ids = [], []
    for post_id, url, district, reward, text, dead_misses in rows:
        html = fetch_html(url)
        if html is None:
            misses = (dead_misses or 0) + 1
            conn.execute("UPDATE posts SET dead_misses = ? WHERE id = ?", (misses, post_id))
            log(f"  {post_id[:20]} 讀不到（第 {misses} 次）")
            continue
        if not detect_resolved(html):
            continue
        alerts.append(f"✅ {district or '雙北'}／{reward or '未標金額'}\n{text[:100]}\n{url}")
        resolved_ids.append(post_id)
        log(f"  {post_id[:20]} 已結案")
    conn.commit()

    # 推播成功才標記結案，避免 Telegram 失敗後永久漏掉
    if alerts and notify("🐾 協尋案件已結案\n\n" + "\n\n".join(alerts)):
        for post_id in resolved_ids:
            conn.execute(
                "UPDATE posts SET resolved = 1, resolved_at = ? WHERE id = ?",
                (date.today().isoformat(), post_id),
            )
        conn.commit()
        log(f"已標記 {len(resolved_ids)} 件結案")
    elif not alerts:
        log("檢查完成，0 件新結案")


# ─── 通知 ────────────────────────────────────────────────────────────


def notify(text: str) -> bool:
    """推 Telegram，成功回傳 True。超過 3500 字自動分批（單則上限 4096）。"""
    if not os.path.exists(TELEGRAM_TOKEN):
        log("找不到 Telegram token，略過通知")
        return False
    try:
        with open(TELEGRAM_TOKEN, encoding="utf-8") as f:
            token = f.read().strip()
        for start in range(0, len(text), 3500):
            response = urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=urllib.parse.urlencode({
                    "chat_id": TELEGRAM_CHAT,
                    "text": text[start:start + 3500],
                }).encode(),
                timeout=15,
            )
            if json.loads(response.read()).get("ok") is not True:
                log("Telegram 回應不是 ok")
                return False
        log("Telegram 已推播")
        return True
    except Exception as e:
        log(f"Telegram 失敗：{type(e).__name__}: {e}")
        return False


# ─── 主流程 ──────────────────────────────────────────────────────────


def main() -> None:
    conn = init_db()
    today = date.today().isoformat()
    log(f"=== 開始掃描 {today} ===")

    posts: list[dict] = []
    posts.extend(scrape_ptt())

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path=CHROME,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            locale="zh-TW",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        ctx.add_cookies(load_cookies("threads.com") + load_cookies("facebook.com"))
        page = ctx.new_page()
        posts.extend(scrape_facebook(page))
        posts.extend(scrape_threads(page))
        browser.close()

    log(f"原始抓取 {len(posts)} 筆")

    kept = 0
    rejected: dict[str, list[str]] = {"facebook": [], "threads": [], "ptt": []}
    for post in posts:
        info = classify(post["text"])
        if not info:
            samples = rejected.setdefault(post["platform"], [])
            if len(samples) < 3:
                samples.append(post["text"][:70].replace(chr(10), " "))
            continue
        kept += 1
        resolved = int(detect_resolved(post["text"]))
        row = conn.execute(
            "SELECT first_seen, resolved FROM posts WHERE id = ?", (post["id"],)
        ).fetchone()
        if row:
            # 已結案的不要被一般掃描又改回未結案
            conn.execute(
                "UPDATE posts SET last_seen = ?, text = ?, resolved = ? WHERE id = ?",
                (today, post["text"], max(resolved, row[1] or 0), post["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO posts
                   (id, platform, url, text, district, reward, first_seen, last_seen, resolved)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (post["id"], post["platform"], post["url"], post["text"],
                 info["district"], info["reward"], today, today, resolved),
            )
            if resolved:
                log(f"新貼文（已結案，不推播）：{post['text'][:40]}")
            else:
                log(f"新貼文：{post['platform']} {info['district']} {info['reward']} "
                    f"{post['text'][:40]}")
        conn.execute(
            "INSERT OR IGNORE INTO sightings (post_id, seen_date) VALUES (?, ?)",
            (post["id"], today),
        )
    conn.commit()
    log(f"符合條件 {kept} 筆")
    for platform, samples in rejected.items():
        for sample in samples:
            log(f"  未通過 {platform}：{sample}")

    # 檢查滿 3 天且仍存在的，推播
    cutoff = (date.today() - timedelta(days=NOTIFY_AFTER_DAYS)).isoformat()
    candidates = conn.execute(
        "SELECT id, platform, url, district, reward, text, first_seen "
        "FROM posts WHERE first_seen <= ? AND notified = 0 AND resolved = 0",
        (cutoff,),
    ).fetchall()

    alerts, ready_ids = [], []
    for post_id, platform, url, district, reward, text, first_seen in candidates:
        # Threads 未登入抓到的只是 JS 空殼（沒有正文），不能用 HTML 判斷存活
        if url and platform != "threads":
            alive = is_still_alive(url)
        else:
            # 抓不到可驗證的頁面時，改用「實際被掃到的天數」
            seen_days = conn.execute(
                "SELECT COUNT(*) FROM sightings WHERE post_id = ?", (post_id,)
            ).fetchone()[0]
            alive = seen_days >= NOTIFY_AFTER_DAYS
        days = (date.today() - date.fromisoformat(first_seen)).days
        log(f"檢查 {post_id}（{days} 天）：{'仍在' if alive else '已消失'}")
        if not alive:
            continue
        alerts.append(
            f"【{days} 天仍在協尋】{district or '雙北'}／{reward or '未標金額'}\n"
            f"{text[:120]}\n{url}"
        )
        ready_ids.append(post_id)

    # 推播成功才標記，避免 Telegram 失敗後這批永遠不再推
    if alerts and notify("🐾 雙北走失動物協尋（有獎金）\n\n" + "\n\n".join(alerts)):
        for post_id in ready_ids:
            conn.execute("UPDATE posts SET notified = 1 WHERE id = ?", (post_id,))
        conn.commit()
        log(f"已標記 {len(ready_ids)} 件推播")
    elif not alerts:
        log("本次無需推播")

    # 早上這輪順便做結案檢查，晚上 21:00 再由 --check 補一次，合起來是每 12 小時
    check_resolved(conn)

    conn.close()
    log("=== 完成 ===")


if __name__ == "__main__":
    try:
        if "--check" in sys.argv:
            # 只檢查既有案件是否結案（每半天跑一次），不重新掃描
            conn = init_db()
            check_resolved(conn)
            conn.close()
        else:
            main()
    except Exception as exc:
        log(f"執行失敗：{type(exc).__name__}: {exc}")
        notify(f"協尋追蹤執行失敗：{type(exc).__name__}: {exc}")
        sys.exit(1)
