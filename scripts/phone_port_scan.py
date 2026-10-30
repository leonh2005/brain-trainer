#!/usr/bin/env python3
"""掃描指定 IP 的 TCP 埠，找出可能是 adb 無線偵錯的埠。

用法：python3 phone_port_scan.py <IP> [起始埠] [結束埠]
輸出：一行一個開放埠（由小到大），其餘資訊都進 stderr。
無參數時預設掃 1024-65535。連入逾時 0.5 秒、並行 400，整輪約幾十秒。
"""
import asyncio
import sys

IP = sys.argv[1]
START = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
END = int(sys.argv[3]) if len(sys.argv) > 3 else 65535


async def probe(port, sem, out):
    async with sem:
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(IP, port), timeout=0.5)
            out.append(port)
            w.close()
        except Exception:
            pass


async def main():
    sem = asyncio.Semaphore(400)
    out = []
    await asyncio.gather(*(probe(p, sem, out) for p in range(START, END + 1)))
    for p in sorted(out):
        print(p)


asyncio.run(main())
