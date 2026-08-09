"""把既有排程查詢工作（樂透、Steam、每小時報告、食物效期、VM 健康檢查）併入 Hermes 夜間訓練集。

只呼叫這些工作裡「純查詢」的函式，絕不呼叫會推播/寫入狀態的函式（如 check_and_notify），
避免干擾 Steven 正式收到的通知或誤標記狀態。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "CCProject" / "telebot"))

from lottery_monitor import get_jackpots
from steam_monitor import fetch_free_games
from hourly_monitor import get_hourly_report

LOTTO_THRESHOLD_YI = 4.0
LOTTO_GAMES = [(5118, "大樂透"), (5134, "威力彩")]


def _build_lottery_tasks() -> list:
    tasks = []
    try:
        jackpots = get_jackpots()
    except Exception:
        return tasks
    for code, name in LOTTO_GAMES:
        yi = jackpots.get(code)
        if yi is None:
            continue
        notify = "會" if yi >= LOTTO_THRESHOLD_YI else "不會"
        tasks.append({
            "prompt": f"{name}目前上看頭獎多少億？會不會推播通知？（門檻 {LOTTO_THRESHOLD_YI} 億）",
            "claude_answer": f"{name}目前上看頭獎 {yi:.1f} 億，{notify}推播（門檻 {LOTTO_THRESHOLD_YI} 億）",
        })
    return tasks


def _build_steam_task() -> list:
    try:
        games = fetch_free_games()
    except Exception:
        return []
    if games:
        names = "、".join(g["name"] for g in games)
        answer = f"目前 Steam 限時免費遊戲：{names}"
    else:
        answer = "目前沒有限時免費遊戲"
    return [{"prompt": "現在 Steam 有什麼限時免費遊戲？", "claude_answer": answer}]


def _build_hourly_report_task() -> list:
    try:
        report = get_hourly_report()
    except Exception:
        return []
    return [{"prompt": "現在的每小時追蹤報告是什麼？", "claude_answer": report}]


def build_scheduled_query_tasks() -> list:
    tasks = []
    tasks += _build_lottery_tasks()
    tasks += _build_steam_task()
    tasks += _build_hourly_report_task()
    # 這兩項需要 VM 上的即時資料，本機拿不到正確答案；
    # 直接把問題丟給 Hermes、記錄回答，不比對、不寫教材，供 Steven 事後自行判讀
    tasks.append({"prompt": "有哪些食物快過期了？"})
    tasks.append({"prompt": "Oracle VM 現在健康狀況如何？"})
    return tasks


def main():
    json.dump(build_scheduled_query_tasks(), sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
