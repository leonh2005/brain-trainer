import argparse

import cm_monitor
import cm_state


def test_init_baseline_creates_empty_state_when_source_has_no_items(tmp_path, monkeypatch):
    monkeypatch.setattr(cm_monitor, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cm_monitor, "CURRENT_FILE", tmp_path / "current.json")
    monkeypatch.setattr(cm_monitor, "LOG_FILE", tmp_path / "monitor.log")
    monkeypatch.setattr(cm_monitor, "collect_source", lambda source: [])

    args = argparse.Namespace(dry_run=False, only="ptt", init_baseline=True, backfill_cutoff=False)
    cm_monitor.run(args)

    state = cm_state.load_state(cm_monitor.state_path("ptt"))
    assert state["baseline_done"] is True
    assert state["items"] == {}


def test_normal_zero_result_does_not_write_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cm_monitor, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cm_monitor, "CURRENT_FILE", tmp_path / "current.json")
    monkeypatch.setattr(cm_monitor, "LOG_FILE", tmp_path / "monitor.log")
    monkeypatch.setattr(cm_monitor, "collect_source", lambda source: [])

    args = argparse.Namespace(dry_run=False, only="ptt", init_baseline=False, backfill_cutoff=False)
    cm_monitor.run(args)

    assert not cm_monitor.state_path("ptt").exists()


def test_backfill_after_stale_state_still_notifies(tmp_path, monkeypatch):
    now = 1795315200
    monkeypatch.setattr(cm_monitor, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(cm_monitor, "CURRENT_FILE", tmp_path / "current.json")
    monkeypatch.setattr(cm_monitor, "LOG_FILE", tmp_path / "monitor.log")
    cm_monitor.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = cm_state.new_state("ruten")
    cm_state.record_baseline(state, [], now - 8000)
    cm_state.save_state(cm_monitor.state_path("ruten"), state)

    item = {"uid": "ruten|xsx|1", "name": "Xbox Series X 1TB", "price": 15000,
            "posted_ts": 1795277000, "source": "ruten", "console": "xsx", "hints": [], "url": "u"}
    monkeypatch.setattr(cm_monitor, "collect_source", lambda source: [item])
    sent = []
    monkeypatch.setattr(cm_monitor, "send", lambda item, dry_run: sent.append(item) or {"status": "delivered", "message_id": 1})

    args = argparse.Namespace(dry_run=False, only="ruten", init_baseline=False, backfill_cutoff=True)
    cm_monitor.run(args)
    assert len(sent) == 1


def test_collect_source_keeps_successful_console_when_other_fails(tmp_path, monkeypatch):
    def fake_fetch(key):
        if key == "ps5_pro":
            raise RuntimeError("boom")
        return [{"uid": "ruten|xsx|1", "name": "Xbox Series X", "posted_ts": 1}]

    monkeypatch.setattr(cm_monitor, "LOG_FILE", tmp_path / "monitor.log")
    monkeypatch.setattr(cm_monitor, "SOURCES", {"ruten": fake_fetch})
    items = cm_monitor.collect_source("ruten")
    assert [i["uid"] for i in items] == ["ruten|xsx|1"]
