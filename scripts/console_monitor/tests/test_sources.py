import cm_carousell
import cm_sources

NOW = 1795315200  # 2026-11-22 12:00 +08, hand-computed
CUTOFF = 1795276800


def test_extract_price_reads_common_taiwan_formats():
    assert cm_sources.extract_price("NT$18,000") == 18000
    assert cm_sources.extract_price("18000元") == 18000
    assert cm_sources.extract_price("18k") == 18000
    assert cm_sources.extract_price("價格見內文") is None


def test_is_sell_post_rejects_wanted_and_sold_posts():
    assert cm_sources.is_sell_post("[售] PS5 Pro 1TB") is True
    assert cm_sources.is_sell_post("出售 XSX 1TB") is True
    assert cm_sources.is_sell_post("[徵] PS5 Pro") is False
    assert cm_sources.is_sell_post("收購 Xbox Series X") is False
    assert cm_sources.is_sell_post("已售 PS5 Pro") is False


def test_matches_console_separates_ps5_pro_from_other_consoles():
    assert cm_sources.matches_console("PS5 Pro 1TB 公司貨", "ps5_pro") is True
    assert cm_sources.matches_console("PS5 Slim 1TB", "ps5_pro") is False
    assert cm_sources.matches_console("Xbox Series X 1TB", "xsx") is True
    assert cm_sources.matches_console("Xbox Series S 512GB", "xsx") is False
    assert cm_sources.matches_console("XSS 512GB", "xsx") is False


def test_apply_excludes_keeps_no_controller_listing_but_drops_controller_only():
    assert cm_sources.apply_excludes("二手 XSX 1TB 不含手把", "xsx") is True
    assert cm_sources.apply_excludes("Xbox Series X 含原廠手把", "xsx") is True
    assert cm_sources.apply_excludes("PS5 Pro 專用手把", "ps5_pro") is False
    assert cm_sources.apply_excludes("PS5 Pro 保護貼", "ps5_pro") is False


def test_parse_ruten_search_returns_ids_in_order():
    payload = {"Rows": [{"Id": "111"}, {"Id": "222"}]}
    assert cm_sources.parse_ruten_search(payload) == ["111", "222"]


def test_parse_ruten_detail_filters_price_date_and_availability():
    payload = {"data": [
        {"id": "1", "name": "Xbox Series X 1TB 二手 不含手把", "goods_price": "15000",
         "num": 1, "available": True, "is_goods_sale": True, "store_name": "電玩老頭",
         "post_time": "1795277000", "update_time": "1795310000"},
        {"id": "2", "name": "Xbox Series X 1TB 手把", "goods_price": "9000",
         "num": 1, "available": True, "is_goods_sale": True, "store_name": "A",
         "post_time": "1795277000", "update_time": "1795310000"},
        {"id": "3", "name": "Xbox Series X 1TB", "goods_price": "15000",
         "num": 1, "available": True, "is_goods_sale": True, "store_name": "B",
         "post_time": "1795276799", "update_time": "1795310000"},
        {"id": "4", "name": "Xbox Series X 1TB", "goods_price": "15000",
         "num": 0, "available": True, "is_goods_sale": True, "store_name": "C",
         "post_time": "1795277000", "update_time": "1795310000"},
    ]}
    items = cm_sources.parse_ruten_detail(payload, "xsx")
    assert len(items) == 1
    item = items[0]
    assert item["uid"] == "ruten|xsx|1"
    assert item["price"] == 15000
    assert item["posted_ts"] == 1795277000
    assert item["source"] == "ruten"
    assert item["console"] == "xsx"


def test_parse_ruten_detail_keeps_listing_when_is_goods_sale_is_false():
    payload = {"data": [
        {"id": "55", "name": "Xbox Series X 1TB 二手 不含手把", "goods_price": "15000",
         "num": 1, "available": True, "is_goods_sale": False, "store_name": "賣家",
         "post_time": "1795277000", "update_time": "1795310000"},
    ]}
    assert len(cm_sources.parse_ruten_detail(payload, "xsx")) == 1


def test_parse_ptt_search_uses_article_code_as_posted_time():
    html = '''<div class="title"><a href="/bbs/Gamesale/M.1795276800.A.ABC.html">[售] PS5 Pro 19000</a></div>
<div class="title"><a href="/bbs/Gamesale/M.1795276800.A.DEF.html">[售] PS5 Pro 12000</a></div>
<div class="title"><a href="/bbs/Gamesale/M.1795276800.A.GHI.html">[徵] PS5 Pro 20000</a></div>'''
    items = cm_sources.parse_ptt_search(html, "Gamesale", "ps5_pro")
    assert len(items) == 1
    assert items[0]["uid"] == "ptt|Gamesale|M.1795276800.A.ABC"
    assert items[0]["posted_ts"] == CUTOFF
    assert items[0]["price"] == 19000


def test_rel_time_to_ts_rejects_vague_and_old_relative_times():
    assert cm_sources.rel_time_to_ts("17 小時前", NOW) == NOW - 17 * 3600
    assert cm_sources.rel_time_to_ts("5 分鐘前", NOW) == NOW - 300
    assert cm_sources.rel_time_to_ts("17 hours ago", NOW) == NOW - 17 * 3600
    assert cm_sources.rel_time_to_ts("昨天", NOW) is None
    assert cm_sources.rel_time_to_ts("3 天前", NOW) is None
    assert cm_sources.rel_time_to_ts("1 週前", NOW) is None
    assert cm_sources.rel_time_to_ts("剛剛", NOW) is None


def test_fetch_carousell_returns_parsed_items_not_raw_cards(monkeypatch):
    monkeypatch.setattr(cm_carousell, "fetch", lambda query, key: [
        {"pid": "1", "title": "Xbox Series X 1TB 二手 不含手把",
         "price_text": "NT$15,000", "time_text": "5 小時前",
         "url": "https://tw.carousell.com/p/1/"},
    ])
    monkeypatch.setattr(cm_sources.time, "time", lambda: NOW)
    items = cm_sources.fetch_carousell("xsx")
    assert len(items) == 1
    assert items[0]["uid"] == "carousell|xsx|1"
    assert items[0]["posted_ts"] == NOW - 5 * 3600


def test_parse_carousell_drops_listings_without_trustworthy_time():
    cards = [
        {"pid": "1472946955", "title": "Xbox Series X 1TB 二手 不含手把",
         "price_text": "NT$15,000", "time_text": "5 小時前", "url": "https://tw.carousell.com/p/1472946955/"},
        {"pid": "999", "title": "Xbox Series X 1TB", "price_text": "NT$15,000",
         "time_text": "昨天", "url": "https://tw.carousell.com/p/999/"},
    ]
    items = cm_sources.parse_carousell_cards(cards, NOW, "xsx")
    assert len(items) == 1
    assert items[0]["uid"] == "carousell|xsx|1472946955"
    assert items[0]["posted_ts"] == NOW - 5 * 3600


def test_extract_price_handles_k_followed_by_chinese_currency():
    assert cm_sources.extract_price("Xbox Series X 18k元") == 18000


def test_courteous_thanks_at_end_does_not_mark_post_as_sold():
    assert cm_sources.is_sell_post("[售] PS5 Pro 1TB 18000 謝謝") is True


def test_parse_ruten_detail_accepts_numeric_stock_as_string():
    payload = {"data": [
        {"id": "77", "name": "Xbox Series X 1TB 二手", "goods_price": "15000",
         "num": "1", "available": True, "is_goods_sale": False, "store_name": "S",
         "post_time": "1795277000", "update_time": "1795310000"},
    ]}
    assert len(cm_sources.parse_ruten_detail(payload, "xsx")) == 1


def test_apply_excludes_tolerates_extra_space_before_controller_word():
    assert cm_sources.apply_excludes("Xbox Series X 含 手把", "xsx") is True
