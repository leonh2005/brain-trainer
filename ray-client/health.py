"""檢查 Ray 常駐服務可用性：SSH tunnel + cluster resources。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool

try:
    resources = pool.connect()
    print("OK ray_cluster")
    print(f"CPU={resources.get('CPU')} memory={resources.get('memory')}")
    pool.shutdown()
except Exception as exc:
    print(f"FAIL {type(exc).__name__}: {exc}")
    sys.exit(1)
