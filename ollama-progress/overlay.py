#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ollama 前處理進度浮窗。

被動 tail Ollama 的 log，解析前處理（prompt processing）進度，
在 DSH 送出請求時浮出一個置頂小視窗顯示進度，完成後自動隱藏。

架構：
    Ollama ──寫──▶ ollama.log ──tail（唯讀）──▶ overlay.py（tkinter 浮窗）

純標準庫。用 /opt/homebrew/bin/python3（3.14 + Tk 9.1；需先 `brew install python-tk`，
系統 /usr/bin/python3 的 Tk 8.5 無邊框視窗不會顯示）。
程式掛掉不影響 DSH：本程式不擋在 DSH 與 Ollama 之間。
"""

import faulthandler
import logging
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk

LOG_PATH = "/opt/homebrew/var/log/ollama.log"

IDLE_HIDE_DELAY = 4.0      # 收到 idle 後，幾秒才隱藏（避免一閃一閃）
POLL_MS = 150              # tkinter 迴圈輪詢佇列的頻率
TAIL_SLEEP = 0.2           # tail 執行緒沒讀到新行時的休眠
PREFILL_RATE_EST = 180.0   # 前處理速率估計（tok/s，實測約 186）；用於介於 log 樣本之間的時間內插
PREFILL_STALE_SEC = 120.0  # 前處理階段多久沒新事件就判定卡住並收掉視窗
DRIFT_WARN_LINES = 500     # 連讀這麼多行都解析不出事件 → 疑似 log 格式漂移，警告一次

WIN_W, WIN_H = 300, 92
BAR_W, BAR_H = 264, 10
MARGIN_RIGHT, MARGIN_BOTTOM = 24, 72

ALPHA_SHOWN = 0.93
BG = "#1c1c1e"
FG = "#f2f2f7"
MUTED = "#9a9aa1"
ACCENT = "#0a84ff"
BAR_BG = "#2c2c2e"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("ollama-progress")

# ── log 解析 ─────────────────────────────────────────────────────────────
# 範例行：
#   slot   operator(): id  0 | task 0 | new prompt, n_ctx_slot = 16384, n_keep = 4, task.n_tokens = 5228
#   slot print_timing: id  0 | task 0 | prompt processing, n_tokens =   4096, progress = 0.78, t =  10.09 s / 405.92 tokens per second
#   slot print_timing: id  0 | task 0 | prompt eval time =   28155.49 ms /  5228 tokens ( ... )
#   slot print_timing: id  0 | task 0 |        eval time =     282.42 ms /     5 tokens ( ... )
#   srv  update_slots: all slots are idle
RE_TASK = re.compile(r"id\s+\d+\s*\|\s*task\s+(\d+)")
RE_START = re.compile(r"new prompt, n_ctx_slot = \d+, n_keep = \d+, task\.n_tokens = (\d+)")
RE_PROGRESS = re.compile(
    r"prompt processing, n_tokens =\s*(\d+), progress = ([\d.]+), "
    r"t =\s*([\d.]+) s /\s*([\d.]+) tokens per second"
)
# (?<!prompt ) 讓這條不會誤吃前處理的 "prompt eval time" 行（不依賴檢查順序）
RE_GEN = re.compile(r"(?<!prompt )eval time =\s*[\d.]+ ms /\s*(\d+) tokens")


def _task_of(line):
    m = RE_TASK.search(line)
    return int(m.group(1)) if m else None


def parse_line(line):
    """把一行 ollama log 轉成事件 dict；認不得就回 None。"""
    if "new prompt, n_ctx_slot" in line:
        m = RE_START.search(line)
        if m:
            return {"type": "start", "total": int(m.group(1)), "task": _task_of(line)}
    if "prompt processing," in line:
        m = RE_PROGRESS.search(line)
        if m:
            return {
                "type": "progress",
                "processed": int(m.group(1)),
                "progress": float(m.group(2)),
                "rate": float(m.group(4)),
                "task": _task_of(line),
            }
    if "prompt eval time" in line:
        return {"type": "prefill_done", "task": _task_of(line)}
    if "eval time" in line:
        m = RE_GEN.search(line)
        if m:
            return {"type": "gen_done", "tokens": int(m.group(1)), "task": _task_of(line)}
    if "all slots are idle" in line:
        return {"type": "idle", "task": None}
    return None


def tail_lines(path, q, stop):
    """追蹤 log 檔，把新行的解析結果丟進 queue。應付檔案被截斷／重建。"""
    fh = None
    inode = None
    missing_warned = False
    unparsed = 0
    drift_warned = False
    while not stop.is_set():
        try:
            st = os.stat(path)
            missing_warned = False
            if fh is None or inode != st.st_ino:
                if fh is not None:
                    fh.close()
                fh = open(path, "r", errors="replace")
                fh.seek(0, os.SEEK_END)  # 只讀新內容
                inode = st.st_ino
                log.info("開始追蹤 %s", path)
            pos = fh.tell()
            line = fh.readline()
            if not line:
                unparsed = 0  # 沒有新行 = 真的閒置，不算漂移
                # 偵測被截斷（例如 brew services restart）
                try:
                    if os.stat(path).st_size < fh.tell():
                        fh.seek(0)
                except OSError:
                    pass
                time.sleep(TAIL_SLEEP)
                continue
            if not line.endswith("\n"):
                # 這一行還沒寫完（Ollama log 有 buffering，可能分次寫入），
                # 直接吃掉會漏掉後面真正的內容 → 退回原位等下一輪。
                fh.seek(pos)
                time.sleep(TAIL_SLEEP)
                continue
            ev = parse_line(line.rstrip("\n"))
            if ev:
                unparsed = 0
                drift_warned = False
                q.put(ev)
            else:
                unparsed += 1
                if unparsed >= DRIFT_WARN_LINES and not drift_warned:
                    # 連續讀了數百行都認不得 → log 格式可能改了（本程式會靜默失效）
                    log.warning("已連續 %d 行解析不出事件，log 格式可能已變動", unparsed)
                    drift_warned = True
        except FileNotFoundError:
            if not missing_warned:
                log.warning("找不到 log 檔 %s（持續等待；請確認 Ollama 的 log 路徑）", path)
                missing_warned = True
            time.sleep(0.5)
        except Exception:  # noqa: BLE001 - tail 不可因單行錯誤中斷
            log.exception("tail 讀取異常，續跑")
            time.sleep(0.5)
    if fh is not None:
        fh.close()


def fmt_tok(n):
    return "{:.1f}K".format(n / 1000.0) if n >= 1000 else str(n)


# ── 浮窗 ─────────────────────────────────────────────────────────────────
class Overlay:
    def __init__(self, q):
        self.q = q
        # 狀態
        self.phase = "idle"          # idle | prefill | gen
        self.total = 0
        self.processed = 0
        self.progress = 0.0
        self.gen_tokens = 0
        self.t0 = 0.0                # 本次請求開始時間
        self.task = None             # 目前追蹤的 task id（過濾交錯請求）
        self.idle_since = None
        self.last_event_at = 0.0

        self.root = tk.Tk()
        self.root.title("Ollama")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.0)
        self.root.configure(bg=BG)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = sw - WIN_W - MARGIN_RIGHT
        y = sh - WIN_H - MARGIN_BOTTOM
        self.shown_geom = "{}x{}+{}+{}".format(WIN_W, WIN_H, x, y)
        self.root.geometry(self.shown_geom)

        pad = tk.Frame(self.root, bg=BG)
        pad.pack(fill="both", expand=True, padx=14, pady=12)

        self.head = tk.Label(
            pad, text="● 前處理中", bg=BG, fg=FG,
            font=("Helvetica Neue", 13, "bold"), anchor="w",
        )
        self.head.pack(fill="x")
        self.pct = tk.Label(
            pad, text="0%", bg=BG, fg=ACCENT,
            font=("Helvetica Neue", 13, "bold"), anchor="e",
        )
        self.pct.place(relx=1.0, rely=0.0, anchor="ne")

        self.canvas = tk.Canvas(
            pad, width=BAR_W, height=BAR_H, bg=BAR_BG,
            highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill="x", pady=(8, 6))
        self.bar = self.canvas.create_rectangle(0, 0, 0, BAR_H, fill=ACCENT, width=0)
        self.canvas.bind("<Configure>", lambda e: self._redraw_bar())

        self.detail = tk.Label(
            pad, text="", bg=BG, fg=MUTED,
            font=("Helvetica Neue", 11), anchor="w",
        )
        self.detail.pack(fill="x")

        self._bind_drag(self.root)
        self._visible = False
        # macOS/Tk 9 三個實測坑：
        #   1. 建立時指定的位置會被忽略（一律跑到左上角）→ 必須 map 之後再設一次。
        #   2. map 之後設位置會讓「無邊框」失效（跑出標題列）→ 先重斷言 overrideredirect。
        #   3. 但重斷言又會把位置重置 → 正確順序：update → overrideredirect → geometry。
        # 另：隱藏不能用 withdraw()/deiconify()（deiconify 每次都會搶焦點，實測）。
        self.root.update()
        self.root.overrideredirect(True)
        self.root.geometry(self.shown_geom)
        self.root.update()
        self.root.after(POLL_MS, self._tick)

    def _bind_drag(self, w):
        w.bind("<ButtonPress-1>", self._drag_start)
        w.bind("<B1-Motion>", self._drag_move)
        for c in w.winfo_children():
            self._bind_drag(c)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry("+{}+{}".format(e.x_root - self._dx, e.y_root - self._dy))

    def _redraw_bar(self):
        w = self.canvas.winfo_width() or BAR_W
        self.canvas.coords(self.bar, 0, 0, int(w * self.progress), BAR_H)

    def _show(self):
        if self._visible:
            return
        self._visible = True
        log.info("show 視窗 (phase=%s total=%s)", self.phase, self.total)
        # 用 lift() 抬升，**不要**在這裡重設 -topmost：實測重設 -topmost 會把 Python app
        # 喚到前景（使用者看到「python app 一直被打開」、搶走選單列）；lift() 不會。
        # -topmost 只在建立時設一次（見 __init__）即可維持浮動層級。
        self.root.attributes("-alpha", ALPHA_SHOWN)
        self.root.lift()

    def _hide(self):
        if not self._visible:
            return
        self._visible = False
        log.info("hide 視窗")
        self.root.attributes("-alpha", 0.0)

    def _set_bar(self, frac):
        self.progress = max(0.0, min(1.0, frac))
        w = self.canvas.winfo_width() or BAR_W
        self.canvas.coords(self.bar, 0, 0, int(w * self.progress), BAR_H)

    def _render(self):
        if self.phase == "prefill":
            # Ollama 的 progress 行很稀疏（長 prompt 每 512 tok 一次，短 prompt 可能只印一次），
            # 故以「log 給的進度」與「已耗時/估計總時」取較大者，讓進度條會走。
            elapsed = max(0.0, time.time() - self.t0)
            est_dur = self.total / PREFILL_RATE_EST if self.total else 0.0
            time_frac = min(0.97, elapsed / est_dur) if est_dur > 0 else 0.0
            frac = min(0.99, max(self.progress, time_frac))
            self._set_bar(frac)
            eta = max(0.0, est_dur - elapsed)
            shown = self.processed or int(self.total * frac)
            self.head.config(text="● 前處理中", fg=FG)
            self.pct.config(text="{}%".format(int(frac * 100)))
            self.detail.config(text="{} / {} tok · 約剩 {:.0f} 秒".format(
                fmt_tok(shown), fmt_tok(self.total), eta))
        elif self.phase == "gen":
            self.head.config(text="● 生成中", fg=FG)
            self.pct.config(text="100%")
            self.detail.config(text="前處理完成 · 文字會直接出現在對話框")

    def _tick(self):
        try:
            dirty = False
            try:
                while True:
                    ev = self.q.get_nowait()
                    if self._apply(ev):
                        dirty = True
            except queue.Empty:
                pass

            # 前處理階段若太久沒有新事件 → 判定卡住（例如請求失敗），強制收掉，避免視窗永久卡住
            if (self.phase == "prefill" and self._visible
                    and time.time() - self.last_event_at >= PREFILL_STALE_SEC):
                log.warning("前處理已 %.0f 秒無進展，強制隱藏", PREFILL_STALE_SEC)
                self.phase = "idle"
                self._hide()

            if self.phase == "idle" and self.idle_since is not None:
                if time.time() - self.idle_since >= IDLE_HIDE_DELAY:
                    self._hide()
                    self.idle_since = None
            if dirty or self.phase == "prefill":   # prefill 期間持續重繪，讓時間內插的進度條會走
                self._render()
        except Exception:  # noqa: BLE001 - 單次例外不可讓 after 迴圈斷掉（否則畫面永久凍結）
            log.exception("tick 異常")
        finally:
            self.root.after(POLL_MS, self._tick)

    def _apply(self, ev):
        t = ev["type"]
        self.last_event_at = time.time()

        if t == "start":
            self.phase = "prefill"
            self.total = ev["total"]
            self.processed = 0
            self.gen_tokens = 0
            self.t0 = time.time()
            self.task = ev.get("task")
            self.idle_since = None
            log.info("start 請求 total=%s task=%s", self.total, self.task)
            self._set_bar(0.0)
            self._show()
            return True

        # 只認當前追蹤的那個 task，避免多請求交錯時數字互相蓋掉
        if ev.get("task") is not None and self.task is not None and ev["task"] != self.task:
            return False

        if t == "progress":
            if self.phase != "prefill":   # 沒看過 start（如程式中途啟動）→ 忽略，免顯示 0 總數
                return False
            self.processed = ev["processed"]
            self.idle_since = None
            self._set_bar(ev["progress"])
            self._show()
        elif t == "prefill_done":
            if self.phase != "prefill":
                return False
            self.phase = "gen"
            self.processed = self.total
            self.idle_since = None
            self._set_bar(1.0)
            self._show()
        elif t == "gen_done":
            if self.phase != "gen":
                return False
            self.gen_tokens = ev["tokens"]
        elif t == "idle":
            self.phase = "idle"
            self.task = None
            self.idle_since = time.time()
        return True

    def run(self):
        self.root.mainloop()


def demo(q):
    """--demo：不讀 log，模擬一次請求，用來肉眼驗證浮窗。"""
    time.sleep(1.0)
    q.put({"type": "start", "total": 5228, "task": 0})
    for i in range(1, 11):
        time.sleep(0.6)
        q.put({"type": "progress", "processed": int(5228 * i / 10.0),
               "progress": i / 10.0, "rate": 190.0, "task": 0})
    q.put({"type": "prefill_done", "task": 0})
    time.sleep(2.0)
    q.put({"type": "gen_done", "tokens": 128, "task": 0})
    q.put({"type": "idle", "task": None})


def main():
    faulthandler.enable()
    q = queue.Queue()
    stop = threading.Event()
    if "--demo" in sys.argv:
        threading.Thread(target=demo, args=(q,), daemon=True).start()
    else:
        threading.Thread(target=tail_lines, args=(LOG_PATH, q, stop), daemon=True).start()
    try:
        Overlay(q).run()
    finally:
        stop.set()


if __name__ == "__main__":
    main()
