import notify


def test_send_returns_false_without_token(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "TOKEN_FILE", tmp_path / "nope.txt")
    assert notify.send("hi") is False


def test_send_returns_false_on_http_error(tmp_path, monkeypatch):
    token = tmp_path / "tok.txt"
    token.write_text("dummy")
    monkeypatch.setattr(notify, "TOKEN_FILE", token)

    def boom(*args, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send("hi") is False   # 不可讓例外穿出
