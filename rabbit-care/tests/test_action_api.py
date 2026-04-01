import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import json
from app import app, init_db, DB_PATH
import tempfile

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = str(tmp_path / 'test.db')
    monkeypatch.setattr('app.DB_PATH', db_file)
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    init_db()
    with app.test_client() as c:
        yield c

def test_log_action_creates_record(client):
    payload = {
        'action': 'eating',
        'confidence': 0.92,
        'timestamp': '2026-04-01T09:23:00'
    }
    resp = client.post('/api/log-action',
                       data=json.dumps(payload),
                       content_type='application/json')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'ok'

def test_log_action_appends_multiple(client):
    for action, ts in [('eating', '2026-04-01T09:00:00'), ('drinking', '2026-04-01T10:00:00')]:
        client.post('/api/log-action',
                    data=json.dumps({'action': action, 'confidence': 0.9, 'timestamp': ts}),
                    content_type='application/json')
    resp = client.get('/api/today-actions?date=2026-04-01')
    assert resp.status_code == 200
    actions = resp.get_json()['actions']
    assert len(actions) == 2
    assert actions[0]['action'] == 'eating'
    assert actions[1]['action'] == 'drinking'

def test_log_action_ignores_other(client):
    payload = {'action': 'other', 'confidence': 0.9, 'timestamp': '2026-04-01T09:00:00'}
    resp = client.post('/api/log-action',
                       data=json.dumps(payload),
                       content_type='application/json')
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ignored'
