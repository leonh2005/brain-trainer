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


@pytest.fixture
def frozen_today(frm, monkeypatch):
    """把模組的 date.today() 凍結在指定日期，讓測試與真實日期無關。"""
    def _set(year: int, month: int, day: int) -> None:
        class _FakeDate(datetime.date):
            @classmethod
            def today(cls):
                return datetime.date(year, month, day)
        monkeypatch.setattr(frm, "date", _FakeDate)
    return _set


def test_fetch_volume_reaches_ma_window_at_month_start(frm, monkeypatch, frozen_today):
    """月初（當月僅 1 個交易日）仍必須湊到 ≥21 筆。

    2026-11-02 實測：months=2 只回 19 筆（10 月 19 天、11 月 0 天），
    check_volume 因此永遠回「資料不足」，② 成交量訊號無法生成結果。
    """
    frozen_today(2026, 11, 2)

    def fake_get_json(url: str) -> dict:
        ym = url.rsplit("=", 1)[-1][:6]  # YYYYMM
        year, month = int(ym[:4]), int(ym[4:6])
        n = 1 if ym == "202611" else 19  # 月初當月僅 1 天；前月 19 個交易日
        days = _trading_days(year, month, n)
        return {"data": [[d, "0", "1,000,000,000", "0", "0", "0"] for d in days]}

    monkeypatch.setattr(frm, "_get_json", fake_get_json)

    rows, errors = frm.fetch_volume()
    assert len(rows) >= frm.VOL_MA_DAYS + 1, (
        f"只拿到 {len(rows)} 筆，20 日均量需要 {frm.VOL_MA_DAYS + 1} 筆，"
        "月初會永遠顯示「資料不足」"
    )
    assert not errors
    # 防方向錯誤：月份必須往前抓，不能抓到未來（today.month + i）
    assert all("2026-09-01" <= r["date"] <= "2026-11-02" for r in rows), \
        f"日期超出預期範圍：{rows[0]['date']}~{rows[-1]['date']}"


def test_fetch_volume_tolerates_unpublished_current_month(frm, monkeypatch, frozen_today):
    """TWSE 當月未發布時只回 {"stat": ...}、沒有 data 鍵，仍須湊到 ≥21 筆。"""
    frozen_today(2026, 11, 2)

    def fake_get_json(url: str) -> dict:
        ym = url.rsplit("=", 1)[-1][:6]
        if ym == "202611":
            return {"stat": "很抱歉，沒有符合條件的資料!"}
        year, month = int(ym[:4]), int(ym[4:6])
        days = _trading_days(year, month, 19)
        return {"data": [[d, "0", "1,000,000,000", "0", "0", "0"] for d in days]}

    monkeypatch.setattr(frm, "_get_json", fake_get_json)

    rows, errors = frm.fetch_volume()
    assert len(rows) >= frm.VOL_MA_DAYS + 1
    assert all(r["date"] < "2026-11-01" for r in rows), "當月未發布時不應出現 11 月資料"


def test_fetch_volume_reports_failed_month(frm, monkeypatch, frozen_today):
    """單月抓取失敗要出現在回傳的 errors，且 check_volume 把它顯示出來。

    command-center 卡片只讀 status JSON 的 detail，錯誤若只 print 到 log
    就與「歷史資料真的不足」無法區分。
    """
    frozen_today(2026, 11, 2)

    def fake_get_json(url: str) -> dict:
        ym = url.rsplit("=", 1)[-1][:6]
        if ym == "202609":
            raise TimeoutError("Read timed out.")
        year, month = int(ym[:4]), int(ym[4:6])
        n = 1 if ym == "202611" else 19   # 月初當月僅 1 天
        days = _trading_days(year, month, n)
        return {"data": [[d, "0", "1,000,000,000", "0", "0", "0"] for d in days]}

    monkeypatch.setattr(frm, "_get_json", fake_get_json)

    rows, errors = frm.fetch_volume()
    assert len(errors) == 1 and "2026-09" in errors[0]

    # 只抓到 2 個月的 19 筆 → 不足，detail 必須點名抓取失敗
    result = frm.check_volume(rows, errors)
    assert result["ok"] is False
    assert "抓取失敗" in result["detail"]


def test_check_volume_warns_when_current_month_failed(frm, frozen_today):
    """當月抓取失敗時，卡片必須說明判定用的是哪一天（不是今天）。"""
    frozen_today(2026, 11, 2)
    rows = [{"date": f"2026-10-{i:02d}", "amount": 1e12} for i in range(1, 22)]
    result = frm.check_volume(rows, ["2026-11 取得失敗：timeout"])
    assert result["ok"] is True
    assert "當月資料未取得" in result["warn"]
    assert "2026-10-21" in result["warn"]


def test_check_volume_no_warn_when_failure_outside_window(frm, frozen_today):
    """失敗月份落在 20 日均量窗口外時不該警告，否則 ⚠️ 會永遠掛著。"""
    frozen_today(2026, 11, 2)
    rows = [{"date": f"2026-10-{i:02d}", "amount": 1e12} for i in range(1, 22)]
    result = frm.check_volume(rows, ["2026-08 取得失敗：timeout"])
    assert result["ok"] is True
    assert "warn" not in result


def test_check_futures_refuses_to_judge_across_gap(frm):
    """缺一天會讓兩日變化被當成單日減碼 → 必須拒絕判定，不可誤報成立。"""
    rows = [
        {"date": "2026-10-27", "net": -100000},
        {"date": "2026-10-28", "net": -100000},   # 10-29、10-30 被漏抓
        {"date": "2026-11-02", "net": -60000},    # 兩日合計減 40,000 口
        {"date": "2026-11-03", "net": -40000},
    ]
    result = frm.check_futures(rows)
    assert result["ok"] is False
    assert "缺口" in result["detail"]


def test_fetch_volume_skips_bad_row_without_dropping_rest(frm, monkeypatch, frozen_today):
    """單一壞列只跳過該列，不可截掉同月後半段（含最新交易日）。"""
    frozen_today(2026, 11, 2)

    def fake_get_json(url: str) -> dict:
        ym = url.rsplit("=", 1)[-1][:6]
        year, month = int(ym[:4]), int(ym[4:6])
        n = 1 if ym == "202611" else 19    # 月初當月僅 1 天
        days = _trading_days(year, month, n)
        rows = [[d, "0", "1,000,000,000", "0", "0", "0"] for d in days]
        if ym == "202610":
            rows[3][2] = "-"          # 壞列
        return {"data": rows}

    monkeypatch.setattr(frm, "_get_json", fake_get_json)

    rows, errors = frm.fetch_volume()
    dates = [r["date"] for r in rows]
    assert "2026-10-27" in dates, "壞列之後的資料必須保留"
    assert dates == sorted(dates)
    assert any("無法解析" in e for e in errors)


def test_check_futures_allows_normal_weekend_gap(frm):
    """週五→週一之間沒有工作日，屬正常，不可誤擋。"""
    rows = [
        {"date": "2026-10-29", "net": -100000},   # 週四
        {"date": "2026-10-30", "net": -95000},    # 週五
        {"date": "2026-11-02", "net": -85000},    # 週一
        {"date": "2026-11-03", "net": -70000},    # 週二
    ]
    result = frm.check_futures(rows)
    assert "缺口" not in result["detail"]


def test_check_futures_detects_single_weekday_gap(frm):
    """週三→週五中間夾了週四 → 必須抓到（固定天數門檻會漏掉這種）。"""
    rows = [
        {"date": "2026-10-26", "net": -100000},   # 週一
        {"date": "2026-10-27", "net": -95000},    # 週二
        {"date": "2026-10-29", "net": -83000},    # 週四（10-28 週三漏抓）
        {"date": "2026-10-30", "net": -71000},    # 週五
    ]
    result = frm.check_futures(rows)
    assert result["ok"] is False
    assert "10-28" in result["detail"] or "工作日" in result["detail"]


def test_check_futures_warns_when_errors_but_rows_suffice(frm):
    """湊足筆數但有天抓不到時，必須提示最新資料日可能不是最近交易日。"""
    rows = [
        {"date": f"2026-10-{d}", "net": -100000 + i * 12000}
        for i, d in enumerate(["26", "27", "28", "29", "30"])
    ]
    result = frm.check_futures(rows, ["2026-11-02：timeout"])
    assert "抓取失敗" in result["warn"]


def test_check_fx_uses_rounded_total(frm):
    """累計升值 0.300 元必須判定成立，不能因浮點誤差顯示與判定矛盾。"""
    rows = [
        {"date": "2026-10-27", "rate": 31.002},
        {"date": "2026-10-28", "rate": 30.902},
        {"date": "2026-10-29", "rate": 30.802},
        {"date": "2026-10-30", "rate": 30.702},
    ]
    result = frm.check_fx(rows)
    assert result["total_appreciation"] == 0.3
    assert result["ok"] is True


def test_fx_accepts_iso_dates(frm, monkeypatch):
    """來源若已是 ISO 格式（YYYY-MM-DD）也要吃得下，不能被丟光。"""
    monkeypatch.setattr(frm, "_get_json", lambda url: [
        {"Date": "2026-10-29", "USD/NTD": "32.452"},
        {"Date": "2026-10-30", "USD/NTD": "32.478"},
    ])
    rows, errors = frm.fetch_fx()
    assert [r["date"] for r in rows] == ["2026-10-29", "2026-10-30"]
    assert not errors


def test_fx_reports_unparseable_dates(frm, monkeypatch):
    """日期格式無法解析時要記錯，不能靜默變成 0 筆。"""
    monkeypatch.setattr(frm, "_get_json", lambda url: [
        {"Date": "30/10/2026", "USD/NTD": "32.478"},
    ])
    rows, errors = frm.fetch_fx()
    assert rows == []
    assert errors and "無法解析" in errors[0]


def test_check_fx_refuses_to_judge_across_gap(frm):
    """匯率中間缺一天時不可把不相鄰的兩日當成連續升值。"""
    rows = [
        {"date": "2026-10-26", "rate": 32.60},
        {"date": "2026-10-27", "rate": 32.55},
        {"date": "2026-10-28", "rate": 32.50},   # 10-29 缺
        {"date": "2026-11-02", "rate": 32.30},
    ]
    result = frm.check_fx(rows)
    assert result["ok"] is False
    assert "缺口" in result["detail"]


def test_fx_dates_are_iso(frm, monkeypatch):
    """匯率日期從期交所的 YYYYMMDD 統一成 YYYY-MM-DD。"""
    monkeypatch.setattr(frm, "_get_json", lambda url: [
        {"Date": "20261029", "USD/NTD": "32.452"},
        {"Date": "20261030", "USD/NTD": "32.478"},
    ])
    rows, _ = frm.fetch_fx()
    assert [r["date"] for r in rows] == ["2026-10-29", "2026-10-30"]
