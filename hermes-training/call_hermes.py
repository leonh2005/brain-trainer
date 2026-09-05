"""呼叫本機 Hermes CLI（headless -z 模式）取得單次回答。"""
import subprocess
import sys


class HermesCallError(RuntimeError):
    pass


def _memory_snapshot() -> str:
    """507（記憶體不足）失敗時附上當下佔用最高的程序，供事後排查真凶。"""
    try:
        ps = subprocess.run(
            ["ps", "-Ao", "rss,comm"],
            capture_output=True, text=True, timeout=5,
        )
        rows = sorted(
            (line.split(None, 1) for line in ps.stdout.splitlines()[1:] if line.strip()),
            key=lambda r: int(r[0]), reverse=True,
        )[:10]
        lines = [f"{int(rss) / 1024:.0f}MB {comm}" for rss, comm in rows]
        return "記憶體佔用前10名：\n" + "\n".join(lines)
    except Exception as e:
        return f"（記憶體快照擷取失敗: {e}）"


def call_hermes(prompt: str, timeout: int = 240) -> str:
    result = subprocess.run(
        ["hermes", "-z", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        msg = f"hermes 執行失敗 (exit={result.returncode}): {result.stderr.strip()}"
        if "507" in result.stderr:
            msg += "\n" + _memory_snapshot()
        raise HermesCallError(msg)
    return result.stdout.strip()


def main():
    prompt = sys.stdin.read().strip()
    if not prompt:
        print("Usage: echo '<prompt>' | python3 call_hermes.py", file=sys.stderr)
        sys.exit(1)
    try:
        print(call_hermes(prompt))
    except HermesCallError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
