"""last30days-web 測試。

重點是 iCloud dataless 檔案的處理：~/Documents/Last30Days 是 iCloud 管管的目錄，
檔案內容未落地本機時開啟會觸發下載、macOS 回 EDEADLK，早先的 retry 會讓首頁
每個請求多花數秒。
"""
import errno
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as webapp  # noqa: E402


class FakeStat:
    """代理真實 stat 結果，只覆寫 dataless 相關的三個欄位。

    其餘欄位照常委派，這樣 read_text() 等內部呼叫 self.stat() 的地方不會壞掉，
    測試失敗的原因才會精確指向 dataless 判定。
    """

    def __init__(self, real, size=None, blocks=None, flags=None):
        self._real = real
        self.st_size = real.st_size if size is None else size
        self.st_blocks = real.st_blocks if blocks is None else blocks
        self.st_flags = real.st_flags if flags is None else flags

    def __getattr__(self, name):
        return getattr(self._real, name)


def _patch_stat(monkeypatch, size=None, blocks=None, flags=None):
    real_stat = Path.stat

    def fake_stat(self):
        return FakeStat(real_stat(self), size=size, blocks=blocks, flags=flags)

    monkeypatch.setattr(Path, "stat", fake_stat)


def test_is_dataless_false_for_local_file(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hello")
    assert webapp.is_dataless(f) is False


def test_is_dataless_true_by_sf_dataless_flag(tmp_path, monkeypatch):
    """iCloud 標記 SF_DATALESS 時要判定為 dataless。"""
    f = tmp_path / "b.md"
    f.write_text("hello")
    _patch_stat(monkeypatch, flags=webapp.SF_DATALESS)
    assert webapp.is_dataless(f) is True


def test_is_dataless_true_by_zero_blocks(tmp_path, monkeypatch):
    """沒有 SF_DATALESS flag、但 blocks=0 而 size>0 時也要判定為 dataless。"""
    f = tmp_path / "c.md"
    f.write_text("hello")
    _patch_stat(monkeypatch, blocks=0)
    assert webapp.is_dataless(f) is True


def test_is_dataless_false_for_empty_file(tmp_path, monkeypatch):
    """空檔案 blocks=0 是正常的，不算 dataless。"""
    f = tmp_path / "empty.md"
    f.write_text("")
    _patch_stat(monkeypatch, size=0, blocks=0)
    assert webapp.is_dataless(f) is False


def test_read_text_retry_reads_local_file(tmp_path):
    f = tmp_path / "d.md"
    f.write_text("hello world")
    assert webapp.read_text_retry(f) == "hello world"


def test_read_text_retry_fails_fast_on_dataless(tmp_path, monkeypatch):
    """dataless 檔案要立刻失敗，不能 sleep 重試——那是首頁變慢的主因。"""
    f = tmp_path / "e.md"
    f.write_text("hello")
    _patch_stat(monkeypatch, flags=webapp.SF_DATALESS)

    t0 = time.time()
    with pytest.raises(OSError) as exc:
        webapp.read_text_retry(f)
    elapsed = time.time() - t0

    assert exc.value.errno == errno.EDEADLK
    assert elapsed < 0.1, f"不該重試等待下載，卻花了 {elapsed:.2f}s"


def test_report_title_falls_back_to_filename_for_dataless(tmp_path, monkeypatch):
    """dataless 時標題退回檔名，不讓整個首頁掛掉。"""
    f = tmp_path / "台積電-20260716-2121.md"
    f.write_text("# Production Brief: 台積電法說優於預期\n")
    _patch_stat(monkeypatch, flags=webapp.SF_DATALESS)

    title = webapp.report_title(f)
    assert "台積電" in title
    assert "法說" not in title  # 讀不到內容，標題只能來自檔名


def test_report_route_returns_503_with_message_for_dataless(tmp_path, monkeypatch):
    """點進 dataless 報告要有說明，不要丟 500 給使用者。"""
    monkeypatch.setattr(webapp, "SAVE_DIR", tmp_path)
    f = tmp_path / "台積電-20260716-2121.md"
    f.write_text("# Production Brief: 台積電法說優於預期\n")
    _patch_stat(monkeypatch, flags=webapp.SF_DATALESS)

    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as c:
        resp = c.get("/report/" + "台積電-20260716-2121.md")

    assert resp.status_code == 503
    assert "iCloud" in resp.get_data(as_text=True)
