import sys
from pathlib import Path
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sync_learnings import sync_learnings, MARKER


def _write_config(path, zhtw_value):
    data = {
        "model": {"default": "qwen3-tw"},
        "agent": {"personalities": {"zhtw": zhtw_value}},
        "display": {"personality": "zhtw"},
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_sync_learnings_appends_learnings_to_zhtw(tmp_path):
    config_path = tmp_path / "config.yaml"
    learnings_path = tmp_path / "learnings.md"
    _write_config(config_path, "永遠用繁體中文回覆。")
    learnings_path.write_text("## 2026-08-09\n\nHermes 回答太簡短。\n", encoding="utf-8")

    sync_learnings(config_path, learnings_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    zhtw = data["agent"]["personalities"]["zhtw"]
    assert zhtw.startswith("永遠用繁體中文回覆。")
    assert "Hermes 回答太簡短。" in zhtw
    assert data["model"]["default"] == "qwen3-tw"  # 其他 key 不受影響


def test_sync_learnings_is_idempotent(tmp_path):
    config_path = tmp_path / "config.yaml"
    learnings_path = tmp_path / "learnings.md"
    _write_config(config_path, "永遠用繁體中文回覆。")
    learnings_path.write_text("## 2026-08-09\n\n教材A\n", encoding="utf-8")

    sync_learnings(config_path, learnings_path)
    sync_learnings(config_path, learnings_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    zhtw = data["agent"]["personalities"]["zhtw"]
    assert zhtw.count("教材A") == 1
    assert zhtw.count("永遠用繁體中文回覆。") == 1


def test_sync_learnings_with_no_learnings_file_keeps_base_only(tmp_path):
    config_path = tmp_path / "config.yaml"
    learnings_path = tmp_path / "learnings.md"  # 不存在
    _write_config(config_path, "永遠用繁體中文回覆。")

    sync_learnings(config_path, learnings_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    zhtw = data["agent"]["personalities"]["zhtw"]
    assert zhtw == "永遠用繁體中文回覆。"
    assert MARKER not in zhtw
