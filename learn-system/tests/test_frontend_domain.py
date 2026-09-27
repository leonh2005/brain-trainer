"""領域頁的原始碼層級檢查。

本專案刻意不引入前端框架與 JS 測試執行器，故以原始碼斷言守住幾件會直接
造成安全或功能問題的行為：伺服器資料只能走 textContent、說明按需載入、
題型只列領域真的有的目標、錯誤要看得見、輪詢不能無上限堆積。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"


def _static(name):
    return (STATIC / name).read_text(encoding="utf-8")


def _html(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _block(src, start):
    """從 start 之後的第一個 { 起做括號配對，取出整個區塊。

    本檔的模板字串裡 ${...} 的括號成對，故直接數括號是安全的。
    """
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j]
    raise AssertionError("括號沒有配對")


def _function_body(src, name):
    """以括號配對取出具名函式本體，用來斷言「這件事不在這條路徑上」。"""
    return _block(src, src.index(f"function {name}("))


def _handler_body(src, element_id):
    """取出某個元素的 onclick 處理函式本體。"""
    return _block(src, src.index(f"getElementById('{element_id}').onclick"))


def test_domain_js_never_uses_innerhtml():
    """領域名、概念名、批改回饋全是伺服器資料，只能用 textContent 插入。"""
    assert "innerHTML" not in _static("domain.js")


def test_domain_page_loads_escape_helper_before_its_script():
    html = _html("domain.html")
    assert html.index("/static/escape.js") < html.index("/static/domain.js")


def test_domain_page_has_a_mistake_log_section():
    assert 'id="mistakes"' in _html("domain.html")


def test_concept_description_is_fetched_only_when_selected():
    """說明第一次生成要跑 Agent，必須按需載入，不能進 refresh 的輪詢路徑。"""
    src = _static("domain.js")
    assert "/explain" not in _function_body(src, "refresh")
    assert "/explain" in _function_body(src, "selectConcept")


def test_mistakes_are_loaded_from_the_endpoint():
    assert "/mistakes" in _static("domain.js")


def test_domain_page_has_a_guarded_delete_affordance():
    """刪除是唯一會摧毀資料的動作，必須先過確認，且真的送 DELETE。"""
    assert 'id="delete-domain"' in _html("domain.html")
    body = _handler_body(_static("domain.js"), "delete-domain")
    assert "confirm(" in body
    assert "method: 'DELETE'" in body


def test_goal_type_options_come_from_the_domain_not_a_fixed_list():
    """領域只有某些目標時，題型下拉不該列出它沒有的。"""
    src = _static("domain.js")
    assert "state.domain.goals" in _function_body(src, "selectConcept")


def test_late_agent_responses_are_dropped_when_the_concept_changed():
    """說明與出題都要跑 Agent（數秒到十幾秒），期間使用者可能已經改點別的概念。

    慢回來的那一筆若照寫，右欄會是「B 的名字配 A 的說明」。
    """
    src = _static("domain.js")
    assert "isCurrent" in _function_body(src, "selectConcept")
    assert "isCurrent" in _handler_body(src, "ask-question")


def test_user_visible_error_paths_are_surfaced():
    """出題失敗（422/502）、批改失敗（408/503/502）、說明失敗（502）都要看得見。"""
    src = _static("domain.js")
    for text in ("出題失敗", "批改失敗", "說明生成失敗"):
        assert text in src, text


def test_api_errors_carry_the_server_message():
    """408/503 等狀態各有自己的說明，只顯示 HTTP 代碼等於沒說。"""
    src = _static("domain.js")
    assert "body.error" in _function_body(src, "api")


def test_poll_is_guarded_and_self_healing():
    """生成中每 3 秒一輪；沒有守衛請求會堆積，沒有 finally 一次失敗就永久停擺。"""
    src = _static("domain.js")
    assert "inflight" in src
    assert "catch" in src
    assert "finally" in src


def test_post_buttons_are_disabled_while_their_request_is_in_flight():
    """出題與批改都要跑 Agent（數秒到十幾秒），按鈕不關掉時第二次點擊會再送一個 POST。

    exam／read／principle 題的後果不只是白花錢：兩次批改會落兩筆 attempt，而
    mastery.compute_status 只看最近 5 筆，重複的那筆會直接扭曲掌握度，也會在
    錯題本裡出現兩次。故兩顆按鈕在請求期間都必須關掉，並在 finally 放回來
    （成功、失敗、提早 return 三條路徑都會經過）。
    """
    src = _static("domain.js")
    for element_id in ("ask-question", "submit-answer"):
        body = _handler_body(src, element_id)
        assert "disabled = true" in body, element_id
        assert "finally" in body, element_id


def test_disabled_buttons_look_disabled():
    """關掉卻看不出來，使用者只會以為壞掉而猛點。"""
    assert re.search(r"button:disabled\b", _static("style.css"))


def test_page_polls_only_while_the_map_is_generating():
    """failed 是終態（要靠「重新生成」離開），ready 也沒有東西會再變。

    原本的條件是 `status !== 'ready'`，對 failed 的領域永遠成立——每 3 秒白打
    一次後端，直到分頁關掉為止。
    """
    src = _static("domain.js")
    assert re.search(r"status\s*===\s*'generating'\s*\)\s*poll\(\)", src)
    assert not re.search(r"status\s*!==\s*'ready'", src)


def test_failed_map_offers_regeneration():
    """生成失敗要看得見，而且要能重試（spec §5.2），不是只能重新載入整頁。"""
    assert 'id="regenerate"' in _html("domain.html")
    assert 'id="map-failed"' in _html("domain.html")
    src = _static("domain.js")
    assert re.search(r"status\s*!==\s*'failed'", _function_body(src, "renderMap"))
    body = _handler_body(src, "regenerate")
    assert "/regenerate" in body
    assert "method: 'POST'" in body
    # 後端已把狀態寫回 generating，本機快取不改就永遠不會再輪詢
    assert re.search(r"status\s*=\s*'generating'", body)


def test_regeneration_asks_for_confirmation():
    """重新生成是破壞性動作：failed 的領域可能已經有練習紀錄。

    生成是一個概念一個概念 commit 的，跑到一半失敗會讓領域停在 failed 卻留下
    已建立的概念，而那些概念在 failed 狀態下照樣畫得出來、可以作答。
    """
    body = _handler_body(_static("domain.js"), "regenerate")
    assert "confirm(" in body
    assert body.index("confirm(") < body.index("/regenerate")


def test_first_load_failure_is_visible():
    """首次載入失敗（領域被刪、後端掛掉）不能留一片空白。

    輪詢路徑刻意吞掉錯誤是對的（停在最後一次成功的畫面），但「從未成功過」
    時吞掉錯誤的結果是整頁空白、沒有任何線索。
    """
    assert 'id="load-error"' in _html("domain.html")
    src = _static("domain.js")
    body = _function_body(src, "poll")
    assert "load-error" in body
    assert "loaded" in body


def test_untrusted_source_urls_are_only_linked_when_http():
    """source_urls 來自模型，是不可信輸入。

    `javascript:` 會在點擊時執行腳本、`data:` 能載入任意內容，兩者都不能進
    href；只有真的解析成 http/https 的才升級成連結，其餘原樣以文字呈現。
    """
    src = _static("domain.js")
    guard = _function_body(src, "httpUrlOrNull")
    assert "'http:'" in guard
    assert "'https:'" in guard
    render = _function_body(src, "renderSources")
    assert "httpUrlOrNull" in render
    # 進 href 的必須是通過檢查的那個值（不是原始字串）
    assert "a.href = safe" in render
    assert "innerHTML" not in src


def test_concept_sources_are_rendered_next_to_the_explanation():
    assert 'id="concept-sources"' in _html("domain.html")
    src = _static("domain.js")
    assert "renderSources" in _function_body(src, "selectConcept")


def test_chat_is_scoped_to_the_selected_concept():
    src = _static("domain.js")
    handler = src.split("chat-form")[1]
    assert re.search(r"concept_id:\s*state\.current", handler)


def test_style_defines_the_meta_rule():
    """兩頁的 JS 都輸出 class="meta"，缺規則時它會跟正文一樣大。"""
    assert re.search(r"^\.meta\b", _static("style.css"), re.M)


def test_style_defines_the_mistake_log_rule():
    assert ".mistake" in _static("style.css")
