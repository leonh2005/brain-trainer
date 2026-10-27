import os
import tempfile
from datetime import UTC, datetime, timedelta, timezone

import pytest
from prune_db import MAX_EXAMPLES, prune_old_articles, run_prune, sync_examples
from storage import db_session, get_irrelevant_examples, init_db, set_irrelevant


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    yield path
    os.unlink(path)


def _insert(conn, title, days_ago, published_at=None, irrelevant=0):
    """插入一篇文章；days_ago 決定 fetched_at，published_at 預設沿用 fetched_at。"""
    fetched = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    conn.execute(
        """INSERT INTO articles (source, title, url, content, published_at, fetched_at, irrelevant)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ("ptt", title, f"https://ptt.cc/{title}", "內容", published_at or fetched, fetched, irrelevant),
    )
    conn.commit()


def test_prune_deletes_articles_older_than_retention(db_path):
    with db_session(db_path) as conn:
        _insert(conn, "舊聞", days_ago=40)
        _insert(conn, "新���", days_ago=3)

    result = run_prune(days=30, db_path=db_path)

    assert result["deleted"] == 1
    assert result["before"] == 2
    assert result["after"] == 1


def test_prune_keeps_articles_inside_window(db_path):
    with db_session(db_path) as conn:
        for i in range(5):
            _insert(conn, f"近{i}", days_ago=i)

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 5


def test_prune_falls_back_to_fetched_at_when_published_at_null(db_path):
    """published_at 為 NULL 時（7 千多筆實測資料屬於這種），用 fetched_at 判斷新舊。"""
    with db_session(db_path) as conn:
        _insert(conn, "無日期舊聞", days_ago=40, published_at=None)
        conn.execute("UPDATE articles SET published_at=NULL WHERE title='無日期舊聞'")
        conn.commit()

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles WHERE title='無日期舊聞'").fetchone()[0] == 0


def test_prune_clamps_future_published_at_to_fetched_at(db_path):
    """published_at 是未來日期（實測有 384 筆跑到 2050 年）時，不能因此永久保留。"""
    with db_session(db_path) as conn:
        _insert(conn, "未來聞", days_ago=40, published_at="2050-08-17T14:05:55+00:00")

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles WHERE title='未來聞'").fetchone()[0] == 0


def test_sync_examples_moves_irrelevant_titles_into_pool(db_path):
    """人工標記的不相關文章，標題要搬進範例池，這樣修剪後 few-shot 不會變空。"""
    with db_session(db_path) as conn:
        _insert(conn, "無關新聞", days_ago=100, irrelevant=1)
        _insert(conn, "相關新聞", days_ago=100, irrelevant=0)

    with db_session(db_path) as conn:
        added = sync_examples(conn)

    with db_session(db_path) as conn:
        titles = [r[0] for r in conn.execute("SELECT title FROM irrelevant_examples")]
    assert added == 1
    assert titles == ["無關新聞"]


def test_sync_examples_trims_pool_to_max(db_path):
    with db_session(db_path) as conn:
        for i in range(MAX_EXAMPLES + 20):
            _insert(conn, f"樣本{i}", days_ago=i % 60, irrelevant=1)

    with db_session(db_path) as conn:
        sync_examples(conn)

    with db_session(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM irrelevant_examples").fetchone()[0]
    assert count == MAX_EXAMPLES


def test_get_irrelevant_examples_reads_from_pool(db_path):
    """修剪後 articles 裡的不相關文章會消失，但範例池仍應供應 few-shot。"""
    with db_session(db_path) as conn:
        _insert(conn, "舊的負樣本", days_ago=100, irrelevant=1)
    with db_session(db_path) as conn:
        sync_examples(conn)

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
    assert get_irrelevant_examples(limit=5, db_path=db_path) == ["舊的負樣本"]


def test_dry_run_does_not_delete(db_path):
    with db_session(db_path) as conn:
        _insert(conn, "舊聞", days_ago=40)

    result = run_prune(days=30, db_path=db_path, dry_run=True)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    assert result["deleted"] == 1
    assert result["after"] == 1


def test_dry_run_reports_actual_pool_size(db_path):
    """dry-run 的 examples 要是池子實際筆數，不能固定回 0。"""
    with db_session(db_path) as conn:
        _insert(conn, "舊的負樣本", days_ago=100, irrelevant=1)
    with db_session(db_path) as conn:
        sync_examples(conn)

    result = run_prune(days=30, db_path=db_path, dry_run=True)
    assert result["examples"] == 1


def test_dry_run_reports_examples_that_would_be_synced(db_path):
    """dry-run 要預告實際執行會搬進池子的筆數，不能回 0。"""
    with db_session(db_path) as conn:
        _insert(conn, "負樣本甲", days_ago=100, irrelevant=1)
        _insert(conn, "負樣本乙", days_ago=100, irrelevant=1)

    result = run_prune(days=30, db_path=db_path, dry_run=True)
    assert result["new_examples"] == 2


def test_unmark_keeps_pool_entry_when_another_article_shares_title(db_path):
    """同標題有兩篇都被標記，取消其中一篇不該把池子裡的標題刪掉。"""
    with db_session(db_path) as conn:
        _insert(conn, "同標題", days_ago=1, irrelevant=0)
        conn.execute(
            """INSERT INTO articles (source, title, url, content, published_at, fetched_at, irrelevant)
               VALUES ('ptt', '同標題', 'https://ptt.cc/dup2', 'c',
                       datetime('now'), datetime('now'), 1)"""
        )
        conn.commit()
        ids = [r[0] for r in conn.execute("SELECT id FROM articles WHERE title='同標題' ORDER BY id")]

    set_irrelevant(ids[0], True, db_path)
    set_irrelevant(ids[1], True, db_path)
    set_irrelevant(ids[0], False, db_path)

    with db_session(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM irrelevant_examples WHERE title='同標題'"
        ).fetchone()[0]
    assert count == 1


def test_malformed_published_at_is_still_pruned(db_path):
    """published_at 存成非 ISO 字串時不能變成永遠刪不掉幽靈文章。"""
    with db_session(db_path) as conn:
        _insert(conn, "壞日期", days_ago=40, published_at="unknown")

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles WHERE title='壞日期'").fetchone()[0] == 0


def test_malformed_fetched_at_is_still_pruned(db_path):
    """published_at 為 NULL 且 fetched_at 也是壞字串時，不能變成永遠刪不掉幽靈文章。

    注意：published_at 必須是 NULL 或壞值，否則 CASE 會走 published_at 分支，
    測的就不是 fetched_at 的 fallback 路徑了。"""
    with db_session(db_path) as conn:
        conn.execute(
            """INSERT INTO articles (source, title, url, content, published_at, fetched_at)
               VALUES ('ptt', '壞抓取時間', 'https://ptt.cc/badfetch', 'c', NULL, 'garbage')"""
        )
        conn.commit()

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles WHERE title='壞抓取時間'").fetchone()[0] == 0


def test_valid_published_at_survives_broken_fetched_at(db_path):
    """反向：published_at 有效時，即使 fetched_at 壞掉也要依 published_at 判斷。"""
    with db_session(db_path) as conn:
        _insert(conn, "新文章", days_ago=1, published_at=datetime.now(UTC).isoformat())
        conn.execute("UPDATE articles SET fetched_at='garbage' WHERE title='新文章'")
        conn.commit()

    run_prune(days=30, db_path=db_path)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles WHERE title='新文章'").fetchone()[0] == 1


def test_sync_examples_returns_inserted_count_not_net_change(db_path):
    """回傳值要是「本次實際插入筆數」，不是淨增減——池子被修剪時淨值會是負數。"""
    with db_session(db_path) as conn:
        # 池子手動塞超過上限，且沒有新的 irrelevant 文章
        for i in range(MAX_EXAMPLES + 20):
            conn.execute(
                "INSERT INTO irrelevant_examples (title, source, marked_at) VALUES (?, 'ptt', ?)",
                (f"舊範例{i}", f"2026-01-{i % 28 + 1:02d}T00:00:00+00:00"),
            )
        conn.commit()

    with db_session(db_path) as conn:
        added = sync_examples(conn)

    assert added == 0          # 淨變化是 -20，但實際沒有插入任何一筆
    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM irrelevant_examples").fetchone()[0] == MAX_EXAMPLES


def test_sync_examples_keeps_pool_at_cap(db_path):
    with db_session(db_path) as conn:
        for i in range(MAX_EXAMPLES + 20):
            _insert(conn, f"樣本{i}", days_ago=i % 60, irrelevant=1)

    with db_session(db_path) as conn:
        sync_examples(conn)

    with db_session(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM irrelevant_examples").fetchone()[0] == MAX_EXAMPLES


def test_set_irrelevant_records_mark_time_and_removes_on_unmark(db_path):
    """人工標記要寫入真實標記時間；取消標記要從池子移除。"""
    with db_session(db_path) as conn:
        _insert(conn, "會被標記", days_ago=1, irrelevant=0)
    with db_session(db_path) as conn:
        article_id = conn.execute(
            "SELECT id FROM articles WHERE title='會被標記'"
        ).fetchone()[0]

    set_irrelevant(article_id, True, db_path)

    with db_session(db_path) as conn:
        row = conn.execute(
            "SELECT title, marked_at FROM irrelevant_examples WHERE title='會被標記'"
        ).fetchone()
    assert row is not None
    assert row["marked_at"] >= datetime.now(UTC).isoformat()[:19]

    set_irrelevant(article_id, False, db_path)

    with db_session(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM irrelevant_examples WHERE title='會被標記'"
        ).fetchone()[0]
    assert count == 0
