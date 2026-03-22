#!/usr/bin/env python3
"""
Thread 珍藏摘要產生器
每天早上 7:00 執行，讀取前一天新增的筆記並產生摘要
"""

import os
import json
from datetime import date, timedelta, datetime
from pathlib import Path
from google import genai

GEMINI_API_KEY = "AIzaSyAvng5q3FjDYgTEUl1gOfzUYUmpbI2Sg4U"
VAULT_PATH = Path("/Users/steven/Library/CloudStorage/GoogleDrive-leonh2005@gmail.com/我的雲端硬碟/Obsidian Vault")
THREAD_DIR = VAULT_PATH / "thread"
SUMMARY_DIR = THREAD_DIR / "摘要"
MARKER_FILE = THREAD_DIR / ".last_summarized"


def get_notes_to_summarize() -> list[Path]:
    """取得需要摘要的筆記：第一次執行讀全部，之後只讀前一天的"""
    all_notes = [
        p for p in THREAD_DIR.glob("*.md")
        if p.parent == THREAD_DIR  # 只讀 thread/ 根目錄，不含子資料夾
    ]

    if not MARKER_FILE.exists():
        # 第一次執行，讀取全部
        return all_notes

    # 讀取上次執行日期
    last_date = date.fromisoformat(MARKER_FILE.read_text().strip())
    yesterday = date.today() - timedelta(days=1)

    # 只取前一天新增/修改的筆記
    return [
        p for p in all_notes
        if date.fromtimestamp(p.stat().st_mtime) == yesterday
    ]


def summarize_notes(notes: list[Path], target_date: date) -> str:
    """用 Gemini 產生摘要"""
    if not notes:
        return f"# {target_date} Thread 珍藏摘要\n\n今日無新增珍藏。\n"

    combined = ""
    for note in notes:
        content = note.read_text(encoding="utf-8").strip()
        if content:
            combined += f"=== {note.stem} ===\n{content}\n\n"

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = (
        f"以下是 {target_date} 的 Threads 珍藏筆記內容，請幫我整理成繁體中文摘要。\n"
        "每一則筆記用小標題區分，重點條列式呈現，簡潔易讀。\n\n"
        f"{combined}"
    )

    result = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[prompt]
    )

    header = f"# {target_date} Thread 珍藏摘要\n\n"
    return header + result.text.strip() + "\n"


def main():
    notes = get_notes_to_summarize()
    target_date = date.today() - timedelta(days=1) if MARKER_FILE.exists() else date.today()

    summary_content = summarize_notes(notes, target_date)

    # 建立摘要資料夾
    today_str = date.today().isoformat()
    output_dir = SUMMARY_DIR / today_str
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "摘要.md"
    output_file.write_text(summary_content, encoding="utf-8")
    print(f"摘要已儲存：{output_file}")

    # 更新 marker
    MARKER_FILE.write_text(date.today().isoformat())


if __name__ == "__main__":
    main()
