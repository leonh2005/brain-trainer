"""示範：Mac 提交 16 個工作，由 VM 上的 Ray 執行，並寫入使用 log。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool


def work(i):
    import os
    import platform
    total = sum(j * j for j in range(600_000))
    return i, total, platform.node(), os.cpu_count()


def main():
    resources = pool.connect()
    print("cluster:", {k: resources[k] for k in ("CPU", "memory") if k in resources})
    started = time.monotonic()
    refs = pool.submit(work, [(i,) for i in range(16)], name="demo_16_tasks")
    results = pool.collect(refs, name="demo_16_tasks", started=started)
    if results:
        print("hosts:", sorted({r[2] for r in results}))
        print("cpu_count:", sorted({r[3] for r in results}))
    pool.shutdown()


if __name__ == "__main__":
    main()
