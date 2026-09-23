"""每週掃描新聞，找出「已官方確認舉行日期」的元首會面，寫入 leader_meetings.json。

只服務 market-analysis 行事曆的「元首會面」類別。設計原則與 econ.py 的
_SUMMITS_2026 一致：僅收有明確日期、且來源為官方公告或主要通訊社的事件；
媒體推測、「有望」「研擬中」、日期未定者一律不列，避免日曆出現假事件。

由 crontab 每週一 07:30 觸發。
"""
import difflib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(BASE_DIR, "leader_meetings.json")
DEEPSEEK_KEY = os.path.expanduser("~/CCProject/.secrets/deepseek_key.txt")
TELEGRAM_TOKEN = os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")
CHAT_ID = "7556217543"

# (查詢字串, hl, gl, ceid)
QUERIES = [
    ("國是訪問 OR 元首會談 OR 領袖會面 OR 雙邊會談 OR 國事訪問 OR 元首峰會",
     "zh-TW", "TW", "TW:zh-Hant"),
    ("state visit OR bilateral summit OR leaders meeting OR summit meeting",
     "en-US", "US", "US:en"),
]

PROMPT_RULES = """以下是一批近期新聞標題，每行一則。

請只挑出符合以下全部條件的事件：
1. 是「國家元首／政府最高領袖之間的當面會面或訪問」（例如 A 國總統訪問 B 國、兩國領袖會談、國是訪問）。
2. 該會面已有**明確的舉行日期**（YYYY-MM-DD），且日期可由標題直接判斷。
3. 屬官方公告或主要通訊社報導（政府、外交部、白宮、Reuters、AP、AFP、新華社等）。

以下情況一律不要列入：
- 媒體推測、傳聞、匿名消息，或帶有「有望」「可能」「研擬中」「傳」「擬」等字眼。
- 只有年份或月份、沒有具體日期。
- 非元首層級（部長、議員、幕僚、企業家）。
- 同一場會面若有多則標題（例如同一國領袖同一趟訪問的不同報導、抵達與會談各一則），只輸出合併後最完整的一筆。
- 日期取「主要會談日」或訪問的核心日，不是抵達日或離境日。

只輸出純 JSON 陣列，不要任何說明文字或 markdown 標記：
[{"date": "YYYY-MM-DD", "title": "事件名稱（地點）", "source": "來源機構"}]

若沒有符合的事件，輸出 []
"""


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _fetch_titles():
    """抓近一週的 Google News RSS 標題。"""
    titles = []
    for q, hl, gl, ceid in QUERIES:
        url = (f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}+when:7d"
               f"&hl={hl}&gl={gl}&ceid={ceid}")
        try:
            xml = _get(url)
        except Exception as e:
            print(f"[warn] RSS 抓取失敗 ({hl}): {e}")
            continue
        for it in re.findall(r"<item>(.*?)</item>", xml, re.S)[:25]:
            t = re.search(r"<title>(.*?)</title>", it, re.S)
            if t:
                title = re.sub(r"<!\[CDATA\[|\]\]>", "", t.group(1)).strip()
                if title:
                    titles.append(title)
    return list(dict.fromkeys(titles))  # 去重、保序


def _build_prompt(titles, data):
    """組出完整 prompt：規則 + 已收錄事件(供 LLM 去重) + 標題。"""
    parts = [PROMPT_RULES]
    existing = []
    try:
        from econ import _SUMMITS_2026
        existing += [f"- {d} {t}" for d, t in _SUMMITS_2026]
    except Exception:
        pass
    existing += [f"- {e['date']} {e['title']}" for e in data.get("events", [])]
    if existing:
        parts.append("\n以下事件已在日曆中，請勿重複輸出，也不要輸出與它們描述同一場會面的條目：\n"
                     + "\n".join(existing))
    parts.append("\n新聞標題：\n" + "\n".join(titles))
    return "\n".join(parts)


def _ask_deepseek(prompt):
    with open(DEEPSEEK_KEY, encoding="utf-8") as f:
        key = f.read().strip()
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }).encode()
    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8", "replace"))
    return resp["choices"][0]["message"]["content"]


def _parse_json(text):
    """從 LLM 回應萃取 JSON 陣列(容忍 code fence 與前後雜訊)。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```(?:json)?", "", text).strip()
    match = re.search(r"\[.*\]", text, re.S)
    candidates = [text] + ([match.group(0)] if match else [])
    for c in candidates:
        try:
            parsed = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            return parsed
    return []


def _similar(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def _load():
    with open(JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _notify(text):
    try:
        with open(TELEGRAM_TOKEN, encoding="utf-8") as f:
            token = f.read().strip()
        body = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body),
            timeout=15)
    except Exception as e:
        print(f"[warn] Telegram 通知失敗: {e}")


def main():
    today = date.today().isoformat()
    data = _load()

    existing = {(e["date"], e["title"]) for e in data.get("events", [])}
    kept = [e for e in data.get("events", []) if e.get("date", "") >= today]

    titles = _fetch_titles()
    print(f"抓到 {len(titles)} 則標題")

    added = []
    if titles:
        try:
            for e in _parse_json(_ask_deepseek(_build_prompt(titles, data))):
                d, title = (e.get("date") or ""), (e.get("title") or "").strip()
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not title or d < today:
                    continue
                if (d, title) in existing:
                    continue
                existing.add((d, title))
                added.append({"date": d, "title": title,
                              "source": e.get("source", ""), "added": today})
        except Exception as e:
            print(f"[warn] LLM 判斷失敗: {e}")

    events = sorted(kept + added, key=lambda x: x["date"])
    # 模糊去重：同日期且標題高度相似者視為同一事件的重複報導
    unique = []
    for e in events:
        if any(e["date"] == u["date"] and _similar(e["title"], u["title"]) > 0.6
               for u in unique):
            continue
        unique.append(e)

    # 去重後才算真正新增，否則訊息會把被去重掉的條目也算進去
    kept_keys = {(e["date"], e["title"]) for e in kept}
    added = [e for e in unique if (e["date"], e["title"]) not in kept_keys]
    events = unique
    _save({
        "_note": "由 scan_leader_meetings.py 每週自動更新。僅收官方公告確認日期者，媒體推測或日期未定一律不列。",
        "updated": time.strftime("%Y-%m-%d %H:%M"),
        "events": events,
    })
    print(f"新增 {len(added)} 筆,現有 {len(events)} 筆")
    for e in added:
        print(f"  + {e['date']} {e['title']}")

    if added:
        lines = "\n".join(f"· {e['date']} {e['title']}" for e in added)
        _notify(f"重大行事曆新增元首會面 {len(added)} 筆：\n{lines}")


if __name__ == "__main__":
    main()
