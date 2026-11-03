"""test_parse: PDF 文字 → 題庫結構。

樣本用寫死的字串，不依賴實際 PDF（PDF 在外接硬碟，換機器或硬碟沒插時仍要能跑）。
"""

import pytest
from parse_questions import parse_text

SAMPLE = """11800 電腦軟體應用 丙級 工作項目01：電腦概論
1. (3)
關於「個人電腦所使用之記憶體」之敘述，下列何者錯誤？ ①軟碟與磁帶
均屬於輔助記憶體 ②ROM 常用來存放系統程式 ③ROM 中之資料僅能存
入，不能被讀出 ④電腦關機後，RAM 內部所儲存之資料會消失 。
Page 1 of 49
2. (2)
下列何者之傳輸速度最快？ ①電話線 ②光纖 ③同軸電纜 ④雙絞線 。
Page 2 of 49
"""


def test_parses_fields():
    qs = parse_text(SAMPLE)

    assert len(qs) == 2
    q = qs[0]
    assert q["cat"] == "工作項目01：電腦概論"
    assert q["q"] == "關於「個人電腦所使用之記憶體」之敘述，下列何者錯誤？"
    assert q["ans"] == 2          # (3) → 0-based 索引 2
    assert q["opts"][0] == "軟碟與磁帶均屬於輔助記憶體"
    assert q["opts"][3] == "電腦關機後，RAM 內部所儲存之資料會消失"


def test_page_noise_removed():
    for q in parse_text(SAMPLE):
        assert "Page" not in q["q"]
        assert all("Page" not in o for o in q["opts"])


def test_second_question():
    qs = parse_text(SAMPLE)

    assert qs[1]["q"] == "下列何者之傳輸速度最快？"
    assert qs[1]["ans"] == 1      # (2) → 索引 1


def test_missing_option_raises():
    """選項少於 4 個要報錯，不能靜默產出殘缺題庫。"""
    bad = """11800 電腦軟體應用 丙級 工作項目01：電腦概論
1. (3)  題幹？ ①甲 ②乙 ③丙 。
"""
    with pytest.raises(ValueError):
        parse_text(bad)
