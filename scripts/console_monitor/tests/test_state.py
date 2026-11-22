import cm_state
import pytest

CUTOFF = 1795276800
NOW = 1795315200


def _item(uid, price=15000, posted=CUTOFF + 100):
    return {"uid": uid, "price": price, "posted_ts": posted, "name": uid}


def test_load_state_missing_file_returns_empty_baseline(tmp_path):
    state = cm_state.load_state(tmp_path / "seen.json")
    assert state["baseline_done"] is False
    assert state["items"] == {}


def test_save_state_round_trip_keeps_source_and_items(tmp_path):
    path = tmp_path / "seen.json"
    state = cm_state.new_state("ruten")
    state["items"]["ruten|xsx|1"] = {"status": "delivered", "attempts": 1}
    cm_state.save_state(path, state)
    loaded = cm_state.load_state(path)
    assert loaded["source"] == "ruten"
    assert loaded["items"]["ruten|xsx|1"]["status"] == "delivered"


def test_record_baseline_marks_all_existing_items_without_notifying():
    state = cm_state.new_state("ptt")
    items = [_item("ptt|Gamesale|A"), _item("ptt|Gamesale|B")]
    cm_state.record_baseline(state, items, NOW)
    assert state["baseline_done"] is True
    assert set(state["items"]) == {"ptt|Gamesale|A", "ptt|Gamesale|B"}
    assert all(v["status"] == "baseline" for v in state["items"].values())
    assert cm_state.select_new(state, items) == []


def test_select_new_only_returns_unseen_items_after_cutoff():
    state = cm_state.new_state("ruten")
    cm_state.record_baseline(state, [_item("ruten|xsx|1")], NOW - 100)
    items = [
        _item("ruten|xsx|1"),
        _item("ruten|xsx|2", posted=CUTOFF),
        _item("ruten|xsx|3", posted=CUTOFF - 1),
    ]
    new = cm_state.select_new(state, items)
    assert [i["uid"] for i in new] == ["ruten|xsx|2"]


def test_select_new_does_not_mutate_state():
    state = cm_state.new_state("ruten")
    cm_state.record_baseline(state, [_item("ruten|xsx|1")], NOW - 100)
    before = str(state)
    cm_state.select_new(state, [_item("ruten|xsx|2")], NOW)
    assert str(state) == before


def test_mark_delivered_records_message_id_and_attempt():
    state = cm_state.new_state("ruten")
    cm_state.record_baseline(state, [_item("ruten|xsx|1")], NOW - 100)
    cm_state.mark(state, "ruten|xsx|1", "delivered", NOW, message_id=123)
    entry = state["items"]["ruten|xsx|1"]
    assert entry["status"] == "delivered"
    assert entry["message_id"] == 123
    assert entry["attempts"] == 1


def test_needs_rebaseline_flags_stale_source_but_not_fresh_one():
    state = cm_state.new_state("carousell")
    state["baseline_done"] = True
    cm_state.record_last_success(state, NOW - 8000)
    assert cm_state.needs_rebaseline(state, NOW, 7800) is True
    cm_state.record_last_success(state, NOW - 100)
    assert cm_state.needs_rebaseline(state, NOW, 7800) is False


def test_retryable_status_is_retried_on_next_run():
    state = cm_state.new_state("ruten")
    state["baseline_done"] = True
    state["items"]["ruten|xsx|1"] = {"status": "retryable", "attempts": 1}
    item = _item("ruten|xsx|1")
    assert cm_state.select_new(state, [item], NOW) == [item]


def test_corrupt_state_file_raises_instead_of_silent_baseline(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        cm_state.load_state(path)
