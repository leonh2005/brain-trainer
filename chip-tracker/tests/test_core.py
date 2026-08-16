# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import updater


def _mem_conn():
    return db.get_conn(":memory:")


def test_streak():
    assert db.streak([500, 200, 100, -50, 300]) == 3
    assert db.streak([-10, 5]) == 0
    assert db.streak([]) == 0
    assert db.streak([None, 5]) == 0
    assert db.streak([1, 1, 1]) == 3


def test_upsert_daily_idempotent_and_coalesce():
    conn = _mem_conn()
    db.upsert_daily(conn, "2330", "2026-07-14", foreign_net=100, total_net=120)
    db.upsert_daily(conn, "2330", "2026-07-14", margin_balance=5000)
    db.upsert_daily(conn, "2330", "2026-07-14", foreign_net=None)  # 不得覆蓋既有值
    rows = db.get_daily_history(conn, "2330")
    assert len(rows) == 1
    assert rows[0]["foreign_net"] == 100
    assert rows[0]["margin_balance"] == 5000
    assert rows[0]["total_net"] == 120


def test_add_remove_stock():
    conn = _mem_conn()
    lists = {l["name"]: l["id"] for l in db.list_lists(conn)}
    db.add_stock(conn, "2330", "台積電", lists["庫存"])
    db.add_stock(conn, "2330", "台積電", lists["觀察"])  # 重複加入 = 改清單
    stocks = db.list_stocks(conn)
    assert len(stocks) == 1 and stocks[0]["list_id"] == lists["觀察"]
    db.remove_stock(conn, "2330")
    assert db.list_stocks(conn) == []


def test_lists():
    import pytest
    conn = _mem_conn()
    names = {l["name"] for l in db.list_lists(conn)}
    assert names == {"庫存", "觀察"}

    new_id = db.add_list(conn, "電子股")
    assert {l["name"] for l in db.list_lists(conn)} == {"庫存", "觀察", "電子股"}

    with pytest.raises(ValueError):
        db.add_list(conn, "電子股")  # 重複命名

    holding_id = next(l["id"] for l in db.list_lists(conn) if l["name"] == "庫存")
    with pytest.raises(ValueError):
        db.delete_list(conn, holding_id)  # 預設清單不可刪

    db.add_stock(conn, "2330", "台積電", new_id)
    with pytest.raises(ValueError):
        db.delete_list(conn, new_id)  # 清單內尚有股票

    db.remove_stock(conn, "2330")
    db.delete_list(conn, new_id)  # 淨空後可刪
    assert {l["name"] for l in db.list_lists(conn)} == {"庫存", "觀察"}


def test_aggregate_institutional():
    rows = [
        {"date": "2026-07-14", "name": "Foreign_Investor", "buy": 5_000_000, "sell": 2_000_000},
        {"date": "2026-07-14", "name": "Foreign_Dealer_Self", "buy": 100_000, "sell": 50_000},
        {"date": "2026-07-14", "name": "Investment_Trust", "buy": 800_000, "sell": 100_000},
        {"date": "2026-07-14", "name": "Dealer_self", "buy": 30_000, "sell": 90_000},
        {"date": "2026-07-14", "name": "Dealer_Hedging", "buy": 10_000, "sell": 20_000},
        {"date": "2026-07-13", "name": "Foreign_Investor", "buy": 0, "sell": 1_000_000},
    ]
    agg = updater.aggregate_institutional(rows)
    d = agg["2026-07-14"]
    assert d["foreign_net"] == 3050   # (3,000,000 + 50,000) / 1000
    assert d["trust_net"] == 700
    assert d["dealer_net"] == -70     # (-60,000 - 10,000) / 1000
    assert d["total_net"] == 3680
    assert agg["2026-07-13"]["foreign_net"] == -1000


def test_parse_tdcc_csv():
    csv_text = (
        "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例百分比\n"
        "20260711,2330,1,500000,64000000,2.5\n"
        "20260711,2330,11,3000,129000000,5.0\n"
        "20260711,2330,12,800,77000000,3.0\n"
        "20260711,2330,15,1542,15500000000,60.0\n"
        "20260711,2330,16,10,-2600000,-0.1\n"   # 差異數，須排除
        "20260711,2330,17,600000,25900000000,100.0\n"  # 合計，須排除
        "20260711,9999,15,99,1000000,50.0\n"   # 非追蹤股，須排除
    )
    parsed = updater.parse_tdcc_csv(csv_text, ["2330"])
    assert list(parsed) == ["2330"]
    date, big, whale, retail = parsed["2330"]
    assert date == "2026-07-11"
    assert big == 63.0     # 3.0 + 60.0（級距12-15）
    assert retail == 7.5   # 2.5 + 5.0（級距1-11）
    assert whale == 1542


def test_parse_tdcc_html():
    html = """
    <table>
      <tr><td>1</td><td>1-999</td><td>2,306,149</td><td>267,183,521</td><td>1.03</td></tr>
      <tr><td>11</td><td>200001-400000</td><td>1,334</td><td>374,361,057</td><td>1.44</td></tr>
      <tr><td>12</td><td>400001-600000</td><td>548</td><td>268,864,579</td><td>1.03</td></tr>
      <tr><td>15</td><td>1000001以上</td><td>1,484</td><td>22,100,113,305</td><td>85.22</td></tr>
      <tr><td>16</td><td>差異數調整</td><td></td><td>-2,000</td><td>-0.00</td></tr>
      <tr><td>17</td><td>合計</td><td>2,835,392</td><td>25,932,370,067</td><td>100.00</td></tr>
    </table>"""
    big, whale, retail = updater.parse_tdcc_html(html)
    assert big == 86.25    # 1.03 + 85.22
    assert whale == 1484
    assert retail == 2.47  # 1.03 + 1.44
    assert updater.parse_tdcc_html("<table><tr><td>查無此資料</td></tr></table>") is None


def test_resolve_query():
    import pytest
    updater._stock_info_cache = {
        "2330": "台積電", "00881": "國泰台灣科技龍頭",
        "2317": "鴻海", "2354": "鴻準",
    }
    try:
        assert updater.resolve_query("2330") == ("2330", "台積電")
        assert updater.resolve_query("台積電") == ("2330", "台積電")
        assert updater.resolve_query("台積") == ("2330", "台積電")   # 唯一子字串
        assert updater.resolve_query("鴻海") == ("2317", "鴻海")     # 精確優先於子字串
        assert updater.resolve_query("不存在") == (None, None)
        assert updater.resolve_query("") == (None, None)
        with pytest.raises(ValueError):
            updater.resolve_query("鴻")  # 鴻海/鴻準 多筆
    finally:
        updater._stock_info_cache = None


def test_tdcc_needed():
    conn = _mem_conn()
    assert updater.tdcc_needed(conn, ["2330"]) is True  # 空庫
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    db.upsert_weekly(conn, "2330", today, 60.0, 1000, 10.0)
    conn.commit()
    assert updater.tdcc_needed(conn, ["2330"]) is False
    assert updater.tdcc_needed(conn, ["2330", "00881"]) is True  # 00881 缺週資料
