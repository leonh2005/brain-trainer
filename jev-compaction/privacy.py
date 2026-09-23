#!/usr/bin/env python3
"""隱私處理：憑證遮蔽（redact）+ 識別資訊偽名化（anonymize）。

移植自同目錄的 anonymize.mjs，行為需保持一致（該版本通過三輪 code review）。
Jev 只回傳機率、不生成文字，所以代號不需要還原——這是整個壓縮方案能成立的基礎。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 憑證類遮蔽。順序重要：較具體的樣式要排在較通用的前面。
REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"), "[REDACTED_ANTHROPIC_KEY]"),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"apikey_[A-Za-z0-9_]{16,}"), "[REDACTED_TYPESAFE_KEY]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"glpat-[A-Za-z0-9_-]{20,}"), "[REDACTED_GITLAB_TOKEN]"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), "[REDACTED_TELEGRAM_TOKEN]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED_JWT]"),
    (re.compile(r"\b[A-Z][12]\d{8}\b"), "[REDACTED_TW_ID]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I), "[REDACTED_EMAIL]"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._-]{10,}"), r"\1[REDACTED]"),
    # 通用賦值：涵蓋雙引號、單引號、無引號三種寫法
    (re.compile(
        r"((?:\"|')?(?:password|passwd|pwd|api[_-]?key|token|secret)(?:\"|')?\s*[:=]\s*)"
        r"(?:\"[^\"]{4,}\"|'[^']{4,}'|[A-Za-z0-9_\-./+]{12,})", re.I), r"\1[REDACTED]"),
]

# 通用目錄名不可代號化：換掉會破壞語意（Jev 反而看不懂「scripts」是什麼）。
GENERIC_DIRS = {
    "scripts", "logs", "log", "data", "reports", "docs", "templates", "static", "assets",
    "node_modules", "__pycache__", "temp", "tmp", "backup", "backups", "config", "conf",
    "test", "tests", "dist", "build", "public", "images", "img", "media", "cache",
    "output", "outputs", "input", "src", "lib", "bin", "files", "downloads", "archive",
}

# shell 指令與常見工具名。專案目錄若恰好叫這些名字（本機真的有一個叫 mkdir 的），
# 全局替換會把每一行 Bash 輸出都污染掉——過度替換比漏掉更糟。
SHELL_COMMANDS = {
    "mkdir", "rmdir", "grep", "find", "curl", "wget", "cat", "ls", "rm", "cp", "mv",
    "echo", "sed", "awk", "python", "python3", "node", "npm", "npx", "git", "docker",
    "ssh", "scp", "rsync", "tar", "zip", "unzip", "kill", "killall", "ps", "top", "df",
    "du", "chmod", "chown", "jq", "sqlite", "sqlite3", "pip", "pip3", "brew", "launchctl",
    "systemctl", "crontab", "tail", "head", "sort", "uniq", "wc", "xargs", "which",
    "env", "export", "source", "bash", "sh", "zsh", "make", "gcc", "java", "go", "cargo",
    "code", "open", "pbcopy", "pbpaste", "defaults", "osascript", "say", "afplay",
    "screencapture", "ffmpeg", "convert", "nginx", "redis", "mysql", "psql", "mongo",
    "main", "test", "app", "run", "start", "stop", "status", "build", "init", "update",
}

_FILE_TAIL = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_HOME_PREFIX = re.compile(r"/Users/steven")
_HOME_ANY = re.compile(r"/Users/steven(?![\w])(?:\/[^\s\"'`)\]},;:|]*)?")
_TILDE = re.compile(r"~(?![\d~])(?:\/[^\s\"'`)\]},;:|]*)?")
_SYS_PATH = re.compile(
    r"/(?:Volumes/[^/\s\"'`)\]},;:|]+|opt|tmp|var|etc|srv|mnt)(?:\/[^\s\"'`)\]},;:|]*)?")
_HOST = re.compile(r"\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d{2,5})?")
# 私有網段：要求至少 2 段後綴，避免把版本號（10.5）或價位（10.99）當成 IP。
_IP = re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?::\d{2,5})?\b")
_USER = re.compile(r"\bsteven\b", re.I)


class Pseudonyms:
    """代號產生器。同一項目在同一次執行內永遠對到同一代號。"""

    def __init__(self) -> None:
        self.maps: dict[str, dict[str, str]] = {}
        self.counts: dict[str, int] = {}

    def code(self, kind: str, value: str) -> str:
        m = self.maps.setdefault(kind, {})
        if value not in m:
            m[value] = f"<{kind.upper()}_{len(m)}>"
        self.counts[kind] = self.counts.get(kind, 0) + 1
        return m[value]


def list_entities(root: str | None = None) -> list[str]:
    """專案名單：路徑以外的純文字提及（如「telebot 的卡片」）也需要代號化。"""
    root = root or os.path.expanduser("~/CCProject")
    try:
        names = []
        for entry in os.scandir(root):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            low = entry.name.lower()
            if low in GENERIC_DIRS or low in SHELL_COMMANDS:
                continue
            names.append(entry.name)
        return names
    except OSError:
        return []


def redact(text: str) -> str:
    out = text
    for pat, rep in REDACTIONS:
        out = pat.sub(rep, out)
    return out


def _safe_tail(relative: str) -> str:
    """只保留「看起來是檔案」的最後一層（有副檔名者）。

    目錄名往往就是專案名（command-center、chip-tracker），本身就是識別資訊。
    """
    segs = [s for s in relative.split("/") if s]
    last = segs[-1] if segs else ""
    return last if _FILE_TAIL.search(last) else ""


def _render_home(raw: str, p: Pseudonyms) -> str:
    """~/x 與 /Users/steven/x 指向同一物，必須對到同一個代號。"""
    relative = _HOME_PREFIX.sub("", raw, count=1) if raw.startswith("/Users/steven") else raw[1:]
    if not relative or relative == "/":
        return p.code("path", "/home")
    trailing = "/" if raw.endswith("/") else ""
    code = p.code("path", relative)
    tail = _safe_tail(relative)
    return f"{code}/{tail}{trailing}" if tail else f"{code}{trailing}"


def anonymize(text: str, p: Pseudonyms, entities: list[str] | None = None) -> str:
    out = text
    out = _HOME_ANY.sub(lambda m: _render_home(m.group(0), p), out)
    out = _TILDE.sub(lambda m: _render_home(m.group(0), p), out)
    out = _SYS_PATH.sub(lambda m: p.code("syspath", m.group(0)), out)
    out = _HOST.sub(lambda m: p.code("host", m.group(0)), out)
    out = _IP.sub(lambda m: p.code("ip", m.group(0)), out)
    # 專案名放在路徑處理之後：此時路徑內的專案名已被吃掉，
    # 這裡處理純文字提及。長的先換，避免部分匹配。
    for e in sorted(entities or [], key=len, reverse=True):
        if len(e) >= 3 and e in out:
            out = out.replace(e, p.code("project", e))
    out = _USER.sub(lambda _m: p.code("user", "steven"), out)
    return out


def apply_privacy(text: str, p: Pseudonyms, anon: bool = True,
                  entities: list[str] | None = None) -> str:
    """實際的隱私處理入口。anon=False 時只做憑證遮蔽。"""
    r = redact(text)
    return anonymize(r, p, entities) if anon else r
