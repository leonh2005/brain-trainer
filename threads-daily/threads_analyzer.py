#!/usr/bin/env python3
"""
Threads 珍藏每日分析
- 每天 08:00 執行
- 抓取上次執行後到今日 03:00 前的新增珍藏
- 用 Claude Haiku 分析對 Steven 的幫助
- 存檔 + 推播 Telegram 摘要
"""

import os, json, sqlite3, shutil, hashlib, re
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI
import requests

# ── 設定 ────────────────────────────────────────────────────────────────────
BASE_DIR       = Path(__file__).parent
STATE_FILE     = BASE_DIR / "state.json"
TELEGRAM_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
TELEGRAM_CHAT  = "7556217543"
FF_PROFILE     = Path.home() / "Library/Application Support/Firefox/Profiles/ro7nczf2.default-release"
OPENAI_KEY     = open(os.path.expanduser("~/CCProject/.secrets/openai_news_key.txt")).read().strip()
OBSIDIAN_VAULT_DIR = Path.home() / "我的雲端硬碟" / "📚 學習 & 筆記" / "from Google keep" / "Projects" / "Threads 珍藏日報"


def copy_to_obsidian(out_file: Path):
    """複製當天報告進 Obsidian vault；vault 資料夾不存在（Google Drive 未同步）就跳過，不影響主流程"""
    try:
        if OBSIDIAN_VAULT_DIR.is_dir():
            shutil.copy2(str(out_file), str(OBSIDIAN_VAULT_DIR / out_file.name))
        else:
            print(f"警告：Obsidian vault 資料夾不存在，跳過複製（{OBSIDIAN_VAULT_DIR}）")
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
def fetch_saved_posts(cookies: dict) -> list[dict]:
    """回傳 [{"text": 貼文內容, "url": 貼文永久連結}, ...]"""
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

        raw = page.evaluate(extract_js)

        # 滾動載入更多；Threads 偶爾滾動後整頁改顯示錯誤訊息（蓋掉原本已載入的內容），
        # 一旦偵測到就放棄這次滾動、保留滾動前已抓到的內容，不讓失敗的滾動把資料弄丟
        for _ in range(2):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            if page.get_by_text("Something went wrong").count() > 0:
                break
            raw = page.evaluate(extract_js)

        posts = [
            {"text": r["text"][:800], "url": f"https://www.threads.com{r['href']}"}
            for r in raw if r.get("text")
        ]
        browser.close()

    return posts

# ── Claude Haiku 分析 ────────────────────────────────────────────────────────
def analyze_with_llm(posts: list[dict]) -> str:
    client = OpenAI(api_key=OPENAI_KEY)

    posts_text = "\n\n---\n\n".join(f"[{i+1}] {p['text']}" for i, p in enumerate(posts))

    prompt = f"""以下是 Steven 在 Threads 上的新增珍藏貼文（共 {len(posts)} 篇）。

Steven 的背景：台灣，非開發者但熟悉終端機，正在用 Claude Code 做個人專案（台股當沖回放、兔子照護系統、財經新聞分析等），對 AI 工具、自動化、投資理財有高度興趣。

請針對每篇珍藏：
1. 用一句話說明這篇在講什麼
2. 具體說明對 Steven 有什麼幫助或可以如何應用

最後給「今日重點推薦」：挑出最值得 Steven 立刻跟進的 1-2 篇，說明原因。

請用繁體中文，簡潔有重點。

---

{posts_text}"""

    msg = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.choices[0].message.content

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
    import re
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
