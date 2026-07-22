import json

import hay_monitor


def _item(shop, variant, in_stock, url="http://x"):
    return {"shop": shop, "title": "軟纖", "variant": variant,
            "price": "60", "in_stock": in_stock, "url": url}


def _wire(monkeypatch, tmp_path, items):
    """把檔案路徑導到 tmp、collect 回傳 items、send 記錄呼叫。"""
    monkeypatch.setattr(hay_monitor, "SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(hay_monitor, "CURRENT_FILE", str(tmp_path / "current.json"))
    monkeypatch.setattr(hay_monitor, "LOG_FILE", str(tmp_path / "log.log"))
    monkeypatch.setattr(hay_monitor, "collect", lambda: items)
    sent = []
    monkeypatch.setattr(hay_monitor, "send", lambda it: sent.append(it))
    return sent


def test_baseline_does_not_push(monkeypatch, tmp_path):
    sent = _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", True)])
    hay_monitor.main()
    assert sent == []
    # baseline 應寫入 seen
    assert (tmp_path / "seen.json").exists()


def test_out_to_in_pushes_once(monkeypatch, tmp_path):
    # 先建 baseline：缺貨
    sent = _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", False)])
    hay_monitor.main()
    assert sent == []
    # 補貨：缺→有 應推一次
    sent2 = _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", True)])
    hay_monitor.main()
    assert len(sent2) == 1


def test_persistent_instock_no_repeat(monkeypatch, tmp_path):
    # baseline 已有貨
    _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", True)])
    hay_monitor.main()
    # 再跑一次仍有貨 → 不重複推
    sent2 = _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", True)])
    hay_monitor.main()
    assert sent2 == []


def test_in_to_out_does_not_push(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", True)])
    hay_monitor.main()
    sent2 = _wire(monkeypatch, tmp_path, [_item("豬寶窩窩", "90g", False)])
    hay_monitor.main()
    assert sent2 == []


def test_current_file_has_all_soft_items(monkeypatch, tmp_path):
    items = [_item("豬寶窩窩", "90g", True), _item("魏啥麻", "285g", False)]
    _wire(monkeypatch, tmp_path, items)
    hay_monitor.main()
    data = json.load(open(tmp_path / "current.json"))
    assert "updated" in data
    assert len(data["items"]) == 2  # 有貨與缺貨都列
