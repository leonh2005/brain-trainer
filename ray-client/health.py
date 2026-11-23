"""檢查 Ray 常駐服務可用性：SSH tunnel + cluster resources。"""
import sys


def main() -> int:
    try:
        import pool
        resources = pool.connect()
    except Exception as exc:
        print(f"FAIL {type(exc).__name__}: {exc}")
        return 1
    print("OK ray_cluster")
    print(f"CPU={resources.get('CPU')} memory={resources.get('memory')}")
    pool.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
