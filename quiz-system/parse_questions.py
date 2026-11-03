"""技術士技能檢定「電腦軟體應用」丙級學科 PDF → questions.json。

用法：
    ./venv/bin/python parse_questions.py [--pdf /path/to.pdf]

PDF 排版陷阱（2026-11-03 實測）：
- 每頁尾有 "Page N of 49"，會插進選項文字
- 第 1 頁是封面，第 50 頁只有頁碼
- 每個工作項目開頭有一行 "11800 電腦軟體應用 丙級 工作項目NN：..."

驗證失敗時直接中止，不寫出殘缺的題庫檔。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DEFAULT_PDF = Path("/Volumes/1TOWC/Donwload/118003A14.pdf")
OUTPUT = BASE_DIR / "questions.json"

EXPECTED_TOTAL = 748  # V114112414 版次；換版次時要重新確認

PAGE_NOISE = re.compile(r"Page \d+ of \d+")
CAT_HEADER = re.compile(r"11800 電腦軟體應用 丙級 (工作項目\d+：[^\n]+)")
Q_SPLIT = re.compile(r"\n(?=\s*\d+\.\s*\(\d\))")
Q_HEAD = re.compile(r"\s*(\d+)\.\s*\((\d)\)\s*")
OPT_SPLIT = re.compile(r"[①②③④]")


def _clean(text: str) -> str:
    """移除排版換行；被換行切開的英數詞要補空格（Game\\nPort、IEEE \\n802.15.4）。"""
    text = re.sub(r"(?<=[A-Za-z0-9])\s*\n\s*(?=[A-Za-z0-9])", " ", text)
    text = re.sub(r"\s*\n\s*", "", text)
    return text.strip().rstrip("。 ").strip()


def _parse_segment(segment: str, cat: str) -> list[dict]:
    out = []
    for block in Q_SPLIT.split(segment):
        m = Q_HEAD.match(block)
        if not m:
            continue
        parts = OPT_SPLIT.split(block[m.end():])
        no = m.group(1)
        ans_no = int(m.group(2))
        if not 1 <= ans_no <= 4:
            raise ValueError(f"{cat} 第 {no} 題：答案 ({ans_no}) 超出 1-4")
        if len(parts) != 5:
            raise ValueError(f"{cat} 第 {no} 題：選項數為 {len(parts) - 1}，預期 4")
        stem = _clean(parts[0])
        opts = [_clean(p) for p in parts[1:]]
        if not stem or any(not o for o in opts):
            raise ValueError(f"{cat} 第 {no} 題：題幹或選項為空")
        out.append({
            "cat": cat,
            "q": stem,
            "opts": opts,
            "ans": ans_no - 1,
            "no": int(no),
        })
    return out


def parse_text(text: str) -> list[dict]:
    """整份 PDF 文字 → 題目 list（每題含 cat/q/opts/ans/no）。"""
    text = PAGE_NOISE.sub("", text)
    parts = CAT_HEADER.split(text)
    if len(parts) < 3:
        raise ValueError("找不到任何工作項目標題，PDF 格式可能已變更")

    questions: list[dict] = []
    for i in range(1, len(parts), 2):
        questions.extend(_parse_segment(parts[i + 1], parts[i].strip()))
    return questions


def extract_pdf_text(pdf_path: Path) -> str:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        return "".join(page.get_text() for page in doc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--out", type=Path, default=OUTPUT)
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"找不到 PDF：{args.pdf}", file=sys.stderr)
        return 1

    questions = parse_text(extract_pdf_text(args.pdf))
    if len(questions) != EXPECTED_TOTAL:
        print(f"題數不符：解析到 {len(questions)} 題，預期 {EXPECTED_TOTAL}", file=sys.stderr)
        return 1

    # id 用陣列索引，之後抽題與判分都靠它
    for i, q in enumerate(questions):
        q["id"] = i

    # 先寫暫存檔再 replace：中斷時不會留下被截斷的題庫，害 app 啟動失敗
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")
    tmp.write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, args.out)
    counts: dict[str, int] = {}
    for q in questions:
        counts[q["cat"]] = counts.get(q["cat"], 0) + 1
    print(f"已寫出 {len(questions)} 題 → {args.out}")
    for cat, n in counts.items():
        print(f"  {cat}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
