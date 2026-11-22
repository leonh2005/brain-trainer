import cm_carousell


def test_parse_card_uses_third_segment_as_listing_title():
    text = "stan1997 | 13 天前 | Xbox Series X 主機 XSX | NT$20,000 | 狀況良好 | 14"
    card = cm_carousell.parse_card(text, "/p/xbox-series-x-123/")
    assert card["seller"] == "stan1997"
    assert card["title"] == "Xbox Series X 主機 XSX"
    assert card["price_text"] == "NT$20,000"
    assert card["time_text"] == "13 天前"
    assert card["pid"] == "123"
    assert card["url"] == "https://tw.carousell.com/p/xbox-series-x-123/"


def test_parse_card_tolerates_missing_condition_segment():
    text = "seller | 5 小時前 | PS5 Pro 1TB | NT$22,000"
    card = cm_carousell.parse_card(text, "/p/ps5-pro-9/")
    assert card["title"] == "PS5 Pro 1TB"
    assert card["price_text"] == "NT$22,000"
