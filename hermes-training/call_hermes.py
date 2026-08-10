"""呼叫本機 Hermes CLI（headless -z 模式）取得單次回答。"""
import subprocess
import sys


class HermesCallError(RuntimeError):
    pass


def call_hermes(prompt: str, timeout: int = 240) -> str:
    result = subprocess.run(
        ["hermes", "-z", prompt],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise HermesCallError(f"hermes 執行失敗 (exit={result.returncode}): {result.stderr.strip()}")
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
