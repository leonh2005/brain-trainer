import cm_config
import cm_sources
import pytest


def test_cutoff_boundary_accepts_midnight_and_rejects_one_second_before():
    assert cm_config.is_after_cutoff(1795276800) is True
    assert cm_config.is_after_cutoff(1795276799) is False


def test_missing_posted_time_never_passes_cutoff():
    assert cm_config.is_after_cutoff(None) is False


def test_cutoff_constant_matches_taipei_midnight():
    assert cm_config.CUTOFF_TS == 1795276800
    assert cm_config.validate_cutoff() is None


def test_both_consoles_have_independent_price_floors():
    assert cm_config.CONSOLES["ps5_pro"]["price_min"] == 18000
    assert cm_config.CONSOLES["xsx"]["price_min"] == 10000


def test_read_token_does_not_require_file_at_import_time(tmp_path, monkeypatch):
    monkeypatch.setattr(cm_config, "TOKEN_FILE", str(tmp_path / "missing.txt"))
    with pytest.raises(FileNotFoundError):
        cm_config.read_token()


def test_xsx_short_name_with_chinese_suffix_is_recognized():
    assert cm_sources.matches_console("XSX主机 1TB 售15000", "xsx") is True
    assert cm_sources.matches_console("XSS版 512GB", "xsx") is False


def test_console_bundle_with_attached_controller_is_kept():
    assert cm_sources.apply_excludes("Xbox Series X 1TB 附原廠搖桿", "xsx") is True
