"""foreign_return_monitor 關鍵邏輯測試（成交量跨月邊界）。

跑法：python3 -m pytest scripts/tests/test_foreign_return_monitor.py -v
"""
import datetime
import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "foreign_return_monitor.py"


@pytest.fixture(scope="module")
def frm():
    spec = importlib.util.spec_from_file_location("foreign_return_monitor", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _trading_days(year: int, month: int, n: int) -> list[str]:
    """取該月前 n 個工作日的民國格式日期字串（FMTQIK row[0] 格式）。"""
    out: list[str] = []
    d = datetime.date(year, month, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(f"{d.year - 1911}/{d.month:02d}/{d.day:02d}")
        d += datetime.timedelta(days=1)
    return out


def test_fetch_volume_reaches_ma_window_at_month_start(frm, monkeypatch):
    """月初（當月僅 1 個交易日）仍必須湊到 ≥21 筆。

    2026-11-02 實測：months=2 只回 19 筆（10 月 19 天、11 月 0 天），
    check_volume 因此永遠回「資料不足」，② 成交量訊號無法生成結果。
    """
    class _FakeDate(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 11, 2)

    monkeypatch.setattr(frm, "date", _FakeDate)

    def fake_get_json(url: str) -> dict:
        ym = url.rsplit("=", 1)[-1][:6]  # YYYYMM
        year, month = int(ym[:4]), int(ym[4:6])
        n = 1 if ym == "202611" else 19  # 月初當月僅 1 天；前月 19 個交易日
        days = _trading_days(year, month, n)
        return {"data": [[d, "0", "1,000,000,000", "0", "0", "0"] for d in days]}

    monkeypatch.setattr(frm, "_get_json", fake_get_json)

    rows = frm.fetch_volume()
    assert len(rows) >= frm.VOL_MA_DAYS + 1, (
        f"只拿到 {len(rows)} 筆，20 日均量需要 {frm.VOL_MA_DAYS + 1} 筆，"
        "月初會永遠顯示「資料不足」"
    )
    # 防方向錯誤：月份必須往前抓，不能抓到未來（today.month + i）
    assert all("2026-09-01" <= r["date"] <= "2026-11-02" for r in rows), \
        f"日期超出預期範圍：{rows[0]['date']}~{rows[-1]['date']}"


def test_fetch_volume_tolerates_unpublished_current_month(frm, monkeypatch):
    """TWSE 當月未發布時只回 {"stat": ...}、沒有 data 鍵，仍須湊到 ≥21 筆。"""
    class _FakeDate(datetime.date):
        @classmethod
        def today(cls):
            return datetime.date(2026, 11, 2)

    monkeypatch.setattr(frm, "date", _FakeDate)

    def fake_get_json(url: str) -> dict:
        ym = url.rsplit("=", 1)[-1][:6]
        if ym == "202611":
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        year, month = int(ym[:4]), int(ym[4:6])
        days = _trading_days(year, month, 19)
        return {"data": [[d, "0", "1,000,000,000", "0", "0", "0"] for d in days]}

    monkeypatch.setattr(frm, "_get_json", fake_get_json)

    rows = frm.fetch_volume()
    assert len(rows) >= frm.VOL_MA_DAYS + 1
    assert all(r["date"] < "2026-11-01" for r in rows), "當月未發布時不應出現 11 月資料"
