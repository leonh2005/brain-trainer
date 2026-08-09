"""把累積的 Hermes 教材（learnings.md）同步進 config.yaml 的 zhtw personality，
讓 Hermes 每次對話都會帶著這些教材。"""
from pathlib import Path
import yaml

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"
DEFAULT_LEARNINGS_PATH = Path.home() / ".hermes" / "learnings.md"

MARKER = "\n\n---\n# Steven 訓練教材（自動同步，勿手動編輯此區塊）\n\n"


def sync_learnings(config_path: Path = DEFAULT_CONFIG_PATH,
                    learnings_path: Path = DEFAULT_LEARNINGS_PATH) -> None:
    config_path = Path(config_path)
    learnings_path = Path(learnings_path)

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    current = config.get("agent", {}).get("personalities", {}).get("zhtw", "")
    base = current.split(MARKER)[0].rstrip()

    learnings = ""
    if learnings_path.exists():
        learnings = learnings_path.read_text(encoding="utf-8").strip()

    new_value = base if not learnings else base + MARKER + learnings

    config.setdefault("agent", {}).setdefault("personalities", {})["zhtw"] = new_value

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def main():
    sync_learnings()
    print("已同步 learnings.md 進 config.yaml")


if __name__ == "__main__":
    main()
