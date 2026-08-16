import sources
from conftest import fixture


def test_piggybabies_parses_variations_instock():
    items = sources.parse_piggybabies(fixture("piggybabies_soft.html"))
    # fixture 兩個 variation 都有貨
    assert len(items) == 2
    assert all(it["in_stock"] is True for it in items)
    assert all(it["shop"] == "豬寶窩窩" for it in items)
    variants = {it["variant"] for it in items}
    assert "分裝 90 g" in variants
    prices = {it["price"] for it in items}
    assert "60" in prices and "399" in prices


def test_piggybabies_all_soft_kept():
    # 產品標題含「軟纖」，全部 variation 保留
    items = sources.parse_piggybabies(fixture("piggybabies_soft.html"))
    assert len(items) == 2


def test_piggybabies_mixed_fiber_page_keeps_only_soft_variant():
    # 現況：豬寶窩窩把粗纖/中纖/軟纖/果園草放同一頁，title 列出全部纖度，
    # 不能靠 title 判斷，要看 variant 本身標的纖度
    import html as htmllib
    import json

    variations = [
        {"attributes": {"attribute_規格": "粗纖梗多提摩西一割 / 10 oz"}, "display_price": 145, "is_in_stock": True},
        {"attributes": {"attribute_規格": "中纖梗葉各半提摩西二割 / 10 oz"}, "display_price": 145, "is_in_stock": True},
        {"attributes": {"attribute_規格": "軟纖葉多提摩西二割 / 10 oz"}, "display_price": 145, "is_in_stock": True},
        {"attributes": {"attribute_規格": "頂級果園草 / 10 oz"}, "display_price": 145, "is_in_stock": True},
    ]
    page = (
        '<html><head><title>【豬寶窩窩】兔子洞 Rabbit hole hay－提摩西草（粗纖、中纖、軟纖）、果園草</title></head>'
        f'<body data-product_variations="{htmllib.escape(json.dumps(variations))}"></body></html>'
    )
    items = sources.parse_piggybabies(page, "https://example.com")
    assert len(items) == 1
    assert "軟纖" in items[0]["variant"]


def test_weyyngbuy_non_soft_excluded():
    # Rabbit02 是「中纖」→ 應被軟纖過濾器排除
    items = sources.parse_weyyngbuy_product(
        fixture("weyyngbuy_rabbit02.html"),
        "https://www.weyyngbuy.com/products/Rabbit02",
    )
    assert items == []


def test_weyyngbuy_soft_included_with_stock_flags():
    # Rabbit06 是「SOFT軟纖」→ 保留，並正確判斷 sold-out
    items = sources.parse_weyyngbuy_product(
        fixture("weyyngbuy_rabbit06_soft.html"),
        "https://www.weyyngbuy.com/products/Rabbit06",
    )
    assert len(items) >= 1
    assert all(it["shop"] == "魏啥麻" for it in items)
    assert all("軟纖" in it["title"] or "SOFT" in it["title"] for it in items)
    # sold-out class 的 variant 應為缺貨
    for it in items:
        assert isinstance(it["in_stock"], bool)


def test_weyyngbuy_sold_out_detection():
    # 用 rabbit02 的結構驗證 sold-out class 判斷（改標題成軟纖讓它通過過濾）
    page = fixture("weyyngbuy_rabbit02.html").replace("中纖", "軟纖")
    items = sources.parse_weyyngbuy_product(page, "http://x")
    by_variant = {it["variant"]: it["in_stock"] for it in items}
    # 285g 無 sold-out → 有貨；908g 有 sold-out → 缺貨
    assert by_variant["285g(台灣分裝)超取最多5宅配10"] is True
    assert by_variant["908g(台灣分裝)"] is False


def test_category_id_extraction():
    ids = sources.parse_weyyngbuy_category_ids(fixture("weyyngbuy_category.html"))
    assert "Rabbit06" in ids
    assert "Rabbit02" in ids
