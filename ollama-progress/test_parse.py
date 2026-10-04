#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_line() 的單元測試。用真實 ollama.log 的行當 fixture。

跑法：/opt/homebrew/bin/python3 test_parse.py
"""

from overlay import parse_line

# 實際從 /opt/homebrew/var/log/ollama.log 抄來的行
L_START = (
    "slot   operator(): id  0 | task 0 | new prompt, n_ctx_slot = 16384, "
    "n_keep = 4, task.n_tokens = 5228"
)
L_PROGRESS = (
    "slot print_timing: id  0 | task 0 | prompt processing, n_tokens =   4096, "
    "progress = 0.78, t =  10.09 s / 405.92 tokens per second"
)
L_PREFILL_DONE = (
    "slot print_timing: id  0 | task 0 | prompt eval time =   28155.49 ms /  "
    "5228 tokens (    5.39 ms per token,   185.68 tokens per second)"
)
L_GEN_DONE = (
    "slot print_timing: id  0 | task 0 |        eval time =     282.42 ms /     "
    "5 tokens (   70.61 ms per token,    14.16 tokens per second)"
)
L_TOTAL = (
    "slot print_timing: id  0 | task 0 |       total time =   28437.91 ms /  "
    "5233 tokens"
)
L_IDLE = "srv  update_slots: all slots are idle"
L_LOADED = "srv  llama_server: model loaded"
L_GIN = (
    '[GIN] 2026/10/04 - 11:32:36 | 200 | 32.276551291s |       127.0.0.1 | '
    'POST     "/api/generate"'
)
# 不同 task id（多請求時用來過濾交錯）
L_START_T7 = (
    "slot   operator(): id  0 | task 7 | new prompt, n_ctx_slot = 16384, "
    "n_keep = 4, task.n_tokens = 100"
)
# 負向：字串像但格式不符
L_BAD_START = "slot   operator(): id  0 | task 0 | new prompt without the usual fields"
L_BAD_GEN = "slot print_timing: id  0 | task 0 | eval time = abc ms / 5 tokens"

CASES = [
    (L_START, {"type": "start", "total": 5228, "task": 0}),
    (L_START_T7, {"type": "start", "total": 100, "task": 7}),
    (L_PROGRESS, {"type": "progress", "processed": 4096, "progress": 0.78, "rate": 405.92, "task": 0}),
    (L_PREFILL_DONE, {"type": "prefill_done", "task": 0}),
    (L_GEN_DONE, {"type": "gen_done", "tokens": 5, "task": 0}),
    (L_TOTAL, None),      # 「total time」不可被當成生成完成
    (L_IDLE, {"type": "idle", "task": None}),
    (L_LOADED, None),
    (L_GIN, None),
    (L_BAD_START, None),  # 欄位格式不符 → 忽略而非誤判
    (L_BAD_GEN, None),
    ("", None),
]


def main():
    failed = 0
    for line, want in CASES:
        got = parse_line(line)
        if got != want:
            failed += 1
            print("FAIL\n  line: {!r}\n  want: {}\n  got : {}".format(line[:70], want, got))
    total = len(CASES)
    print("{} / {} 通過".format(total - failed, total))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
