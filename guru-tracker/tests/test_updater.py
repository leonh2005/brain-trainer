# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import updater

SAMPLE_INFOTABLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ALLY FINL INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>02005N100</cusip>
    <value>498992850</value>
    <shrsOrPrnAmt><sshPrnamt>12719675</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>037833100</cusip>
    <value>1000000</value>
    <shrsOrPrnAmt><sshPrnamt>5000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
</informationTable>
"""


def test_parse_infotable_xml_extracts_holdings():
    rows = updater.parse_infotable_xml(SAMPLE_INFOTABLE_XML)
    assert len(rows) == 2
    ally = next(r for r in rows if r["cusip"] == "02005N100")
    assert ally["issuer"] == "ALLY FINL INC"
    assert ally["value_usd"] == 498992850.0
    assert ally["shares"] == 12719675.0


def test_parse_infotable_xml_skips_rows_without_cusip():
    xml = SAMPLE_INFOTABLE_XML.replace("<cusip>037833100</cusip>", "<cusip></cusip>")
    rows = updater.parse_infotable_xml(xml)
    assert len(rows) == 1


def test_report_period_maps_quarter_correctly():
    assert updater._report_period("2026-03-31") == "2026-Q1"
    assert updater._report_period("2026-06-30") == "2026-Q2"
    assert updater._report_period("2026-09-30") == "2026-Q3"
    assert updater._report_period("2026-12-31") == "2026-Q4"


def test_detect_value_scale_flags_legacy_thousands_convention():
    # 舊制(千美元)：value=13926 對應 1,545,610股，隱含股價僅$0.009 -> 應偵測為需要x1000校正
    rows = [{"value_usd": 13926, "shares": 1545610}, {"value_usd": 99057, "shares": 1493390}]
    assert updater.detect_value_scale(rows) == 1000.0


def test_detect_value_scale_leaves_new_convention_unchanged():
    # 新制(整數美元)：隱含股價正常(數十美元) -> 不校正
    rows = [{"value_usd": 57843260493.0, "shares": 227917808.0}]
    assert updater.detect_value_scale(rows) == 1.0


def test_detect_value_scale_empty_rows_defaults_to_no_scale():
    assert updater.detect_value_scale([]) == 1.0


def test_compute_weight_pct_basic():
    assert updater.compute_weight_pct(250.0, 1000.0) == 25.0


def test_compute_weight_pct_zero_total_returns_zero_not_error():
    assert updater.compute_weight_pct(100.0, 0) == 0.0
    assert updater.compute_weight_pct(100.0, None) == 0.0


def _seed_snapshot(conn, holder_id, tickers_weights, period="2026-Q1"):
    for ticker, weight in tickers_weights.items():
        db.upsert_snapshot(conn, holder_id, ticker, period, 1, 1000.0, weight, "2026-05-15")
    conn.commit()


def test_compute_steven_zhou_only_includes_tickers_meeting_threshold(monkeypatch):
    conn = db.get_conn(":memory:")
    monkeypatch.setattr(updater.config, "HOLDERS", [
        {"id": "a", "name": "A"}, {"id": "b", "name": "B"}, {"id": "c", "name": "C"},
    ])
    for hid in ("a", "b", "c"):
        db.upsert_holder(conn, hid, hid, hid, "13f", None, "test")

    # AAPL held by all 3 (>=threshold 2), MSFT held by only 1 (below threshold)
    _seed_snapshot(conn, "a", {"AAPL": 10.0, "MSFT": 5.0})
    _seed_snapshot(conn, "b", {"AAPL": 20.0})
    _seed_snapshot(conn, "c", {"AAPL": 30.0})
    db.set_config(conn, "steven_zhou_threshold", "2")

    updater.compute_steven_zhou(conn)

    periods = db.latest_periods(conn, updater.config.STEVEN_ZHOU_ID, limit=1)
    result = db.get_snapshot(conn, updater.config.STEVEN_ZHOU_ID, periods[0])
    tickers = {r["ticker"] for r in result}
    assert tickers == {"AAPL"}


def test_compute_steven_zhou_splits_weight_equally(monkeypatch):
    conn = db.get_conn(":memory:")
    monkeypatch.setattr(updater.config, "HOLDERS", [
        {"id": "a", "name": "A"}, {"id": "b", "name": "B"},
    ])
    for hid in ("a", "b"):
        db.upsert_holder(conn, hid, hid, hid, "13f", None, "test")
    _seed_snapshot(conn, "a", {"AAPL": 10.0, "MSFT": 5.0})
    _seed_snapshot(conn, "b", {"AAPL": 20.0, "MSFT": 15.0})
    db.set_config(conn, "steven_zhou_threshold", "2")

    updater.compute_steven_zhou(conn)

    periods = db.latest_periods(conn, updater.config.STEVEN_ZHOU_ID, limit=1)
    result = db.get_snapshot(conn, updater.config.STEVEN_ZHOU_ID, periods[0])
    assert len(result) == 2
    for r in result:
        assert r["weight_pct"] == 50.0


def test_cusip_already_resolved_distinguishes_unresolved_from_not_queried():
    conn = db.get_conn(":memory:")
    assert db.cusip_already_resolved(conn, "000000000") is False
    # OpenFIGI 查無結果時 ticker 存 None，但代表「已查過」，不該被視為「還沒查過」
    db.upsert_cusip_ticker(conn, "000000000", None, "2026-01-01")
    conn.commit()
    assert db.cusip_already_resolved(conn, "000000000") is True
    assert db.get_cusip_ticker(conn, "000000000") is None


def test_compute_steven_zhou_empty_intersection_leaves_no_holdings(monkeypatch):
    conn = db.get_conn(":memory:")
    monkeypatch.setattr(updater.config, "HOLDERS", [
        {"id": "a", "name": "A"}, {"id": "b", "name": "B"},
    ])
    for hid in ("a", "b"):
        db.upsert_holder(conn, hid, hid, hid, "13f", None, "test")
    _seed_snapshot(conn, "a", {"AAPL": 10.0})
    _seed_snapshot(conn, "b", {"MSFT": 20.0})
    db.set_config(conn, "steven_zhou_threshold", "2")

    updater.compute_steven_zhou(conn)

    periods = db.latest_periods(conn, updater.config.STEVEN_ZHOU_ID, limit=1)
    result = db.get_snapshot(conn, updater.config.STEVEN_ZHOU_ID, periods[0]) if periods else []
    assert result == []
