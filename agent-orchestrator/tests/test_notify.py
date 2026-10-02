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


def test_send_survives_undecodable_token(tmp_path, monkeypatch):
    """token 檔格式錯（非 UTF-8）要停用，不是崩潰。"""
    bad = tmp_path / "tok.bin"
    bad.write_bytes(b"\xff\xfe\x00invalid")
    monkeypatch.setattr(notify, "TOKEN_FILE", bad)
    assert notify.send("hi") is False


def test_token_survives_undecodable_file(tmp_path, monkeypatch):
    bad = tmp_path / "tok.bin"
    bad.write_bytes(b"\xff\xfe\x00invalid")
    monkeypatch.setattr(notify, "TOKEN_FILE", bad)
    assert notify._token() is None
