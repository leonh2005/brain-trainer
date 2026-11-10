"""oracle_daily_report 解析與狀態邏輯測試。

跑法：python3 -m pytest scripts/tests/test_oracle_daily_report.py -v
"""
import fcntl
import importlib.util
import io
import json
from pathlib import Path

import pytest

_REPORT_PATH = Path(__file__).resolve().parents[1] / "oracle_daily_report.py"

SAMPLE = """\
[09:00:00] === Oracle ARM VM 自動建立腳本 ===
[09:00:00] 目標：3 台 VM.Standard.A1.Flex（1 OCPU / 6 GB × 50 GB）
[09:00:01] 第 1 輪：已有 0/3，未就緒 無，缺 line-bot-a1-1, line-bot-a1-2
[09:00:02]    line-bot-a1-1：容量不足
[09:00:03] 180 秒後再確認...
[09:03:03] 第 2 輪：已有 1/3，未就緒 無，缺 line-bot-a1-2
[09:03:04]    line-bot-a1-2：請求頻率過高
[09:03:04]    line-bot-a1-3：HTTP 500 InternalError
"""


@pytest.fixture(scope="module")
def rep():
    spec = importlib.util.spec_from_file_location("oracle_daily_report", _REPORT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_counts_rounds_and_reasons(rep):
    rounds, have, target, last_round, last_reason, counts = rep.parse(io.StringIO(SAMPLE))
    assert (rounds, have, target, last_round) == (2, 1, 3, 2)
    assert last_reason == "line-bot-a1-3 HTTP 500 InternalError"
    assert counts["容量不足"] == 1
    assert counts["請求頻率過高"] == 1


def test_parse_does_not_treat_target_line_as_reason(rep):
    _, _, _, _, _, counts = rep.parse(io.StringIO(SAMPLE))
    assert "3 台 VM.Standard.A1.Flex（1 OCPU / 6 GB × 50 GB）" not in counts


def test_last_target_line(rep):
    assert rep.last_target_line(io.StringIO(SAMPLE)) == (
        "3 台 VM.Standard.A1.Flex（1 OCPU / 6 GB × 50 GB）"
    )
    assert rep.last_target_line(io.StringIO("沒有這一行\n")) is None


def test_delta_start(rep):
    assert rep.delta_start(None, 500) is None      # 首份報告
    assert rep.delta_start(120, 500) == 120        # 正常
    assert rep.delta_start(999, 500) == 0          # log 被截斷/重建，從頭算


def test_load_offset_rejects_non_object_json(rep, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text('"hello"', encoding="utf-8")
    monkeypatch.setattr(rep, "STATE_PATH", str(state))
    assert rep.load_offset() is None

    state.write_text("{壞掉的 json", encoding="utf-8")
    assert rep.load_offset() is None

    state.write_text(json.dumps({"offset": 42}), encoding="utf-8")
    assert rep.load_offset() == 42


def test_save_offset_roundtrip(rep, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setattr(rep, "STATE_PATH", str(state))
    rep.save_offset(1234)
    assert rep.load_offset() == 1234
    assert json.loads(state.read_text(encoding="utf-8")) == {"offset": 1234}


def test_is_running_uses_flock(rep, tmp_path, monkeypatch):
    lock = tmp_path / "grab.lock"
    monkeypatch.setattr(rep, "LOCK_PATH", str(lock))
    assert rep.is_running() is False  # 檔案不存在

    lock.write_text("", encoding="utf-8")
    assert rep.is_running() is False  # 檔案在但沒人持有

    with open(lock, "r") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert rep.is_running() is True  # 被其他程序持有
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
    assert rep.is_running() is False
