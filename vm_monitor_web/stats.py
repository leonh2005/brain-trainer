"""透過一條 SSH 指令收集 VM 資源，並把輸出解析成乾淨的 dict。"""

from __future__ import annotations

import os
import re
import subprocess

VM_HOST = "ubuntu@161.33.42.181"
SSH_KEY = os.path.expanduser("~/.ssh/oracle_line_bot")

REMOTE_CMD = """printf '__VMSTAT__\\n'; vmstat 1 2 | tail -1
printf '__MEM__\\n'; free -b
printf '__DF__\\n'; df -B1 / /mnt/data 2>/dev/null || true
printf '__NET__\\n'; cat /proc/net/dev
printf '__PS__\\n'; ps -eo pid,pcpu,pmem,rss,stat,comm --sort=-pcpu | head -8
printf '__UP__\\n'; uptime
printf '__END__\\n'
"""


class ParseError(Exception):
    """遠端輸出缺少必要的 marker 或欄位。"""


def collect() -> dict:
    """執行遠端收集指令；失敗直接拋例外，由 route 轉成 ok:false。"""
    argv = [
        "ssh",
        "-i",
        SSH_KEY,
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=3",
        VM_HOST,
        REMOTE_CMD,
    ]
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=4, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"SSH 回傳碼 {result.returncode}")
    return parse_snapshot(result.stdout)


def parse_cpu(vmstat_line: str) -> int:
    fields = vmstat_line.split()
    if len(fields) < 15:
        raise ParseError("vmstat 欄位不足")
    idle = float(fields[14])
    return max(0, min(100, round(100 - idle)))


def parse_memory(text: str) -> dict:
    for line in text.splitlines():
        if line.startswith("Mem:"):
            fields = line.split()
            if len(fields) < 7:
                break
            return {
                "total": int(fields[1]),
                "used": int(fields[2]),
                "available": int(fields[6]),
            }
    return {}


def parse_disks(text: str) -> dict:
    disks = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6 or not fields[-1].startswith("/"):
            continue
        try:
            disks[fields[-1]] = {
                "size": int(fields[1]),
                "used": int(fields[2]),
                "percent": int(fields[4].rstrip("%")),
            }
        except ValueError:
            continue
    return disks


def parse_net(text: str) -> dict:
    rx = tx = 0
    found = False
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 10 or ":" not in fields[0]:
            continue
        if fields[0].rstrip(":") == "lo":
            continue
        try:
            rx += int(fields[1])
            tx += int(fields[9])
        except ValueError:
            continue
        found = True
    return {"rx_bytes": rx, "tx_bytes": tx} if found else {}


def parse_processes(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines()[1:]:
        fields = line.split(maxsplit=5)
        if len(fields) < 6:
            continue
        try:
            rows.append(
                {
                    "pid": int(fields[0]),
                    "cpu": float(fields[1]),
                    "mem": float(fields[2]),
                    "rss_kb": int(fields[3]),
                    "stat": fields[4],
                    "comm": fields[5],
                }
            )
        except ValueError:
            continue
    return rows


def parse_uptime(text: str) -> dict:
    load_match = re.search(r"load average:\s*([\d.,\s]+)", text)
    loads = []
    if load_match:
        for value in load_match.group(1).split(",")[:3]:
            try:
                loads.append(float(value.strip()))
            except ValueError:
                break
    up_match = re.search(r"up\s+(.+?),\s+\d+\s+users?", text)
    return {"load": loads, "uptime": up_match.group(1).strip() if up_match else ""}


def parse_snapshot(stdout: str) -> dict:
    if "__END__" not in stdout:
        raise ParseError("缺少 __END__ marker")
    sections: dict[str, list[str]] = {}
    current = None
    for line in stdout.splitlines():
        if line.startswith("__") and line.endswith("__"):
            current = line.strip("_")
            sections[current] = []
        elif current:
            sections[current].append(line)

    vmstat_lines = [line for line in sections.get("VMSTAT", []) if line.strip()]
    memory = parse_memory("\n".join(sections.get("MEM", [])))
    disks = parse_disks("\n".join(sections.get("DF", [])))
    network = parse_net("\n".join(sections.get("NET", [])))
    up = parse_uptime("\n".join(sections.get("UP", [])))
    if not vmstat_lines or not memory or not disks or not network or not up["load"]:
        raise ParseError("必要欄位缺失")

    return {
        "cpu": parse_cpu(vmstat_lines[0]),
        "load": up["load"],
        "memory": memory,
        "disks": disks,
        "network": network,
        "processes": parse_processes("\n".join(sections.get("PS", []))),
        "uptime": up["uptime"],
    }
