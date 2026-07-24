import quotes


def test_get_quote_us_uses_yf(monkeypatch):
    monkeypatch.setattr(quotes, "_yf_close", lambda s: 41.23 if s == "XLP" else 0.0)
    assert quotes.get_quote("XLP", "US") == 41.23


def test_get_quote_tw_uses_shioaji(monkeypatch):
    monkeypatch.setattr(quotes, "_shioaji_close", lambda t: 48.5 if t == "00864B" else 0.0)
    assert quotes.get_quote("00864B", "TW") == 48.5


def test_get_fx_reads_twd_pair(monkeypatch):
    monkeypatch.setattr(quotes, "_yf_close", lambda s: 32.35 if s == "TWD=X" else 0.0)
    assert quotes.get_fx() == 32.35


def test_unknown_market_raises():
    import pytest
    with pytest.raises(ValueError):
        quotes.get_quote("XLP", "XX")
