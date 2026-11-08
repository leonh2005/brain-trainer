#!/usr/bin/env python3
"""
Threads 珍藏每日分析
- 每天 08:00 執行
- 抓取上次執行後到今日 03:00 前的新增珍藏
- 用 Claude Haiku 分析對 Steven 的幫助
- 存檔 + 推播 Telegram 摘要
"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI

# ── 設定 ────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
STATE_FILE     = BASE_DIR / "state.json"
TELEGRAM_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
TELEGRAM_CHAT  = "7556217543"
FF_PROFILE     = Path.home() / "Library/Application Support/Firefox/Profiles/ro7nczf2.default-release"
OPENAI_KEY     = open(os.path.expanduser("~/CCProject/.secrets/openai_news_key.txt")).read().strip()
# 2026-11-09 修正：原本寫 ~/我的雲端硬碟/，那是 Google Drive 的過時本機快照，
# 實際同步中的 vault 在 CloudStorage 下（見 memory reference_obsidian）。
OBSIDIAN_VAULT_DIR = (
    Path.home() / "Library" / "CloudStorage"
    / "GoogleDrive-leonh2005@gmail.com" / "我的雲端硬碟"
    / "📚 學習 & 筆記" / "from Google keep" / "Projects" / "Threads 珍藏日報"
)


def copy_to_obsidian(out_file: Path):
    """複製當天報告進 Obsidian vault；資料夾不存在就建立，失敗不影響主流程"""
    try:
        OBSIDIAN_VAULT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(out_file), str(OBSIDIAN_VAULT_DIR / out_file.name))
    except Exception as e:
        print(f"複製進 Obsidian 失敗（不影響主流程）: {e}")

# ── 工具函數 ─────────────────────────────────────────────────────────────────
def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": msg, "parse_mode": "Markdown"}, timeout=10)

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"analyzed_ids": []}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def post_id(text: str) -> str:
    return hashlib.md5(text.strip()[:200].encode()).hexdigest()

def get_ff_cookies() -> dict:
    tmp = "/tmp/ff_threads_cookies.sqlite"
    shutil.copy2(str(FF_PROFILE / "cookies.sqlite"), tmp)
    conn = sqlite3.connect(tmp)
    cur  = conn.cursor()
    cur.execute("SELECT name, value FROM moz_cookies WHERE host LIKE '%threads%'")
    cookies = {n: v for n, v in cur.fetchall()}
    conn.close()
    os.remove(tmp)
    return cookies

# ── 抓取 Threads 珍藏 ────────────────────────────────────────────────────────
MAX_SCROLLS = 60   # 單次抓取的滾動上限（保護用，正常靠 IDLE_SCROLLS 提早結束）
IDLE_SCROLLS = 3   # 連續幾次滾動都沒有新貼文，視為到底


def fetch_saved_posts(cookies: dict) -> list[dict]:
    """回傳 [{"text": 貼文內容, "url": 貼文永久連結}, ...]

    Threads 的珍藏頁是**虛擬滾動**：DOM 只保留視窗內的幾則，滾出畫面就被回收。
    因此不能只取最後一次的結果（2026-11-09 實測：總共 101 篇，舊版每次都覆蓋
    只抓到 8 篇，漏掉 92%）。必須每次滾動後**累積**、用 href 去重，
    並以「連續 IDLE_SCROLLS 次沒有新增」判斷到底，而不是固定滾幾次。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:137.0) Gecko/20100101 Firefox/137.0"
        )
        ctx.add_cookies([
            {"name": k, "value": v, "domain": ".threads.com", "path": "/"}
            for k, v in cookies.items()
        ])
        page = ctx.new_page()
        page.goto("https://www.threads.com/saved", timeout=20000)
        page.wait_for_timeout(3000)

        # 用貼文永久連結（/@user/post/shortcode）當錨點，往上找最近的內文區塊，
        # 一則貼文只取一筆（避免同貼文裡的引用/連結預覽文字被當成獨立項目重複計算）
        extract_js = """() => {
            const seen = new Set();
            const results = [];
            document.querySelectorAll('a[href*="/post/"]').forEach(link => {
                const href = link.getAttribute('href');
                if (!href || seen.has(href)) return;
                let el = link, text = '';
                for (let i = 0; i < 10; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    if (el.querySelectorAll('a[href*="/post/"]').length !== 1) break;
                    const texts = Array.from(el.querySelectorAll('div[dir="auto"], span[dir="auto"]'))
                        .map(x => (x.innerText || x.textContent || '').trim())
                        .filter(x => x.length > 40);
                    if (texts.length > 0) { text = texts[0]; break; }
                }
                if (text) { seen.add(href); results.push({href, text}); }
            });
            return results;
        }"""

        seen: dict[str, str] = {}

        def collect() -> int:
            before = len(seen)
            for r in page.evaluate(extract_js):
                if r["href"] not in seen and r.get("text"):
                    seen[r["href"]] = r["text"]
            return len(seen) - before

        collect()
        idle = 0
        for _ in range(MAX_SCROLLS):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1600)
            # 滾動偶爾會讓整頁變成錯誤訊息（蓋掉已載入內容）；一旦發生就停手，
            # 已累積的內容保留，不讓失敗的滾動把資料弄丟
            if page.get_by_text("Something went wrong").count() > 0:
                break
            if collect() == 0:
                idle += 1
                if idle >= IDLE_SCROLLS:
                    break
            else:
                idle = 0

        posts = [
            {"text": text[:800], "url": f"https://www.threads.com{href}"}
            for href, text in seen.items()
        ]
        browser.close()

    return posts

# ── Claude Haiku 分析 ────────────────────────────────────────────────────────
BATCH_SIZE = 12   # 每批丟給模型的篇數；一次太多會被 max_tokens 截斷

STEVEN_CONTEXT = (
    "Steven 的背景：台灣，非開發者但熟悉終端機，正在用 Claude Code 做個人專案"
    "（台股當沖回放、兔子照護系統、財經新聞分析等），對 AI 工具、自動化、投資理財有高度興趣。"
)


def analyze_with_llm(posts: list[dict]) -> str:
    client = OpenAI(api_key=OPENAI_KEY)

    # 逐篇說明分成小批處理：修好抓取後一次可能進來近百篇，
    # 單次呼叫會超出 max_tokens 被截斷（2026-11-09 實測 101 篇）。
    parts = []
    batches = [posts[i:i + BATCH_SIZE] for i in range(0, len(posts), BATCH_SIZE)]
    for n, chunk in enumerate(batches, 1):
        posts_text = "\n\n---\n\n".join(f"[{i+1}] {p['text']}" for i, p in enumerate(chunk))
        prompt = f"""以下是 Steven 在 Threads 上的新增珍藏貼文（第 {n}/{len(batches)} 批，共 {len(chunk)} 篇）。

{STEVEN_CONTEXT}

請針對每篇珍藏：
1. 用一句話說明這篇在講什麼
2. 具體說明對 Steven 有什麼幫助或可以如何應用

請用繁體中文，簡潔有重點。

---
{posts_text}"""
        msg = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        parts.append(msg.choices[0].message.content)

    # 重點推薦只做一次，用標題掃過全部，避免每批各推一次
    titles = "\n".join(f"[{i+1}] {p['text'][:80]}" for i, p in enumerate(posts))
    rec_prompt = f"""以下是 Steven 今天新增的 {len(posts)} 篇 Threads 珍藏（只列開頭）：

{STEVEN_CONTEXT}

{titles}

請挑出最值得他立刻跟進的 1-2 篇，說明原因。繁體中文，簡潔。"""
    rec = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=800,
        messages=[{"role": "user", "content": rec_prompt}],
    )
    parts.append("## 今日重點推薦\n\n" + rec.choices[0].message.content)
    return "\n\n---\n\n".join(parts)

# ── 主程式 ───────────────────────────────────────────────────────────────────
def main():
    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # 只在 03:00 前有新資料需要分析（正常情況 08:00 執行時必然 < 03:00 還沒過）
    cutoff_time = now.replace(hour=3, minute=0, second=0, microsecond=0)

    state = load_state()
    analyzed_ids: list = state.get("analyzed_ids", [])

    print(f"[{now:%H:%M}] 開始抓取 Threads 珍藏...")

    try:
        cookies = get_ff_cookies()
    except Exception as e:
        send_telegram(f"⚠️ Threads 分析失敗：取 cookies 時出錯\n{e}")
        return

    try:
        all_posts = fetch_saved_posts(cookies)
    except Exception as e:
        send_telegram(f"⚠️ Threads 分析失敗：抓取珍藏時出錯\n{e}")
        return

    # 過濾已分析過的
    new_posts = [p for p in all_posts if post_id(p["text"]) not in analyzed_ids]

    if not new_posts:
        print("沒有新珍藏，跳過分析。")
        send_telegram(f"📌 *Threads 珍藏日報*（{today}）\n\n昨晚沒有新增珍藏 😴")
        return

    print(f"找到 {len(new_posts)} 篇新珍藏，開始分析...")

    try:
        analysis = analyze_with_llm(new_posts)
    except Exception as e:
        send_telegram(f"⚠️ Threads 分析失敗：Haiku 分析時出錯\n{e}")
        return

    # 存成 markdown 檔
    out_file = BASE_DIR / f"{today}.md"
    out_file.write_text(
        f"# Threads 珍藏分析 — {today}\n\n"
        f"> 分析時間：{now:%H:%M}｜新增 {len(new_posts)} 篇\n\n"
        f"## 分析結果\n\n{analysis}\n\n"
        f"## 原始珍藏內容\n\n" +
        "\n\n---\n\n".join(f"**[{i+1}]** [查看原文]({p['url']})\n{p['text']}" for i, p in enumerate(new_posts)),
        encoding="utf-8"
    )

    # 更新已分析 ID
    for p in new_posts:
        pid = post_id(p["text"])
        if pid not in analyzed_ids:
            analyzed_ids.append(pid)
    state["analyzed_ids"] = analyzed_ids[-500:]  # 保留最近 500 筆
    state["last_run"] = now.isoformat()
    save_state(state)

    # Telegram 只推「今日重點推薦」段落
    highlight_match = re.search(r'(今日重點推薦.*)', analysis, re.DOTALL)
    highlight = highlight_match.group(1).strip() if highlight_match else analysis[:600]
    tg_msg = (
        f"📌 *Threads 珍藏日報*（{today}）\n"
        f"新增 {len(new_posts)} 篇\n\n"
        f"{highlight}\n\n"
        f"_完整分析已存至 threads-daily/{today}.md_"
    )
    send_telegram(tg_msg)
    copy_to_obsidian(out_file)
    print(f"完成！分析結果已存至 {out_file}")

if __name__ == "__main__":
    main()
