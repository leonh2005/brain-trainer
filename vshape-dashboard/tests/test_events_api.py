"""/api/events：只回今天、新到舊。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as vd  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_log(monkeypatch, tmp_path):
    monkeypatch.setattr(vd, "EVENT_LOG", str(tmp_path / "vshape_events.log"))
    yield


def test_events_empty_when_file_missing():
    body = vd.app.test_client().get("/api/events").get_json()
    assert body["events"] == []
    assert body["date"] == str(date.today())


def test_events_only_today_newest_first():
    today = date.today().isoformat()
    lines = [
        f"{today} 09:10 | long | 0050 元大台灣50 | A | 收 102.70 | VWAP 101.42",
        f"{today} 09:20 | short | 00631L 元大台灣50正2 | A | 收 32.91 | VWAP 32.72",
        "2026-11-11 13:00 | long | 2330 台積電 | B | 收 100.00 | VWAP 99.00",
    ]
    with open(vd.EVENT_LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    body = vd.app.test_client().get("/api/events").get_json()
    assert len(body["events"]) == 2
    assert body["events"][0].split(" | ")[1] == "short"     # 新到舊
    assert all(e.startswith(today) for e in body["events"])
