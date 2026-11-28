"""Flask routes 的單元測試（TDD：先寫，預期 RED）。"""

import pytest

SAMPLE_RESULT = {
    "cpu": 9,
    "load": [0.0, 0.0, 0.0],
    "memory": {"total": 100, "used": 40, "available": 60},
    "disks": {"/": {"size": 200, "used": 80, "percent": 40}},
    "network": {"rx_bytes": 1000, "tx_bytes": 2000},
    "processes": [{"pid": 1, "cpu": 5.0, "mem": 1.0, "rss_kb": 100, "stat": "S", "comm": "python"}],
    "uptime": "up 8 days",
}


@pytest.fixture
def client(monkeypatch):
    """把 SSH 收集換成固定資料，避免測試真的連 VM。"""
    import vm_monitor_web.app as app_module

    monkeypatch.setattr(app_module, "collect", lambda: SAMPLE_RESULT)
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


def test_index_shows_refresh_interval(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"5000" in response.data
    assert b"/mnt/data" in response.data
    assert b"setInterval" in response.data


def test_api_stats_returns_snapshot(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    data = response.get_json()
    assert data["ok"] is True
    assert data["cpu"] == 9
    assert data["network"]["rx_bytes"] == 1000


def test_api_stats_reports_failure_without_secrets(client, monkeypatch):
    import vm_monitor_web.app as app_module

    monkeypatch.setattr(app_module, "collect", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    response = client.get("/api/stats")
    data = response.get_json()
    assert response.status_code == 200
    assert data["ok"] is False
    assert "boom" in data["error"]
    assert "oracle_line_bot" not in data["error"]
