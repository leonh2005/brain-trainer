#!/usr/bin/env python3
"""proxy 層的測試：fail-open 行為與快取並發。

執行：python3 -m unittest test_proxy -v
"""
from __future__ import annotations

import threading
import unittest

import compactor
import proxy


class TestMaybeCompactFailOpen(unittest.TestCase):
    """核心設計要求：任何情況都不能讓請求失敗。"""

    def test_passes_through_when_no_messages(self):
        data = {"model": "x", "foo": "bar"}
        out, reason = proxy.maybe_compact(data)
        self.assertEqual(out, data)
        self.assertTrue(reason.startswith("skip:"))

    def test_passes_through_when_messages_empty(self):
        data = {"messages": []}
        out, reason = proxy.maybe_compact(data)
        self.assertEqual(out, data)
        self.assertEqual(reason, "skip:no-messages")

    def test_passes_through_when_messages_not_list(self):
        data = {"messages": "not a list"}
        out, _ = proxy.maybe_compact(data)
        self.assertEqual(out, data)

    def test_passes_through_when_compactor_raises(self):
        """compactor 爆掉時，必須回傳原始 body。"""
        data = {"messages": [{"role": "user", "content": "hi"}]}
        saved = compactor.compact

        def boom(*a, **kw):
            raise RuntimeError("simulated")

        compactor.compact = boom
        try:
            out, reason = proxy.maybe_compact(data)
        finally:
            compactor.compact = saved

        self.assertEqual(out, data)
        self.assertTrue(reason.startswith("error:"))

    def test_preserves_other_fields(self):
        """壓縮只該動 messages，其他欄位（model、system、tools）原樣保留。"""
        data = {
            "model": "claude-x",
            "system": "you are helpful",
            "tools": [{"name": "t"}],
            "messages": [{"role": "user", "content": "hi"}],
        }
        out, _ = proxy.maybe_compact(data)
        for k in ("model", "system", "tools"):
            self.assertEqual(out[k], data[k])


class TestCacheConcurrency(unittest.TestCase):
    def test_concurrent_put_does_not_raise(self):
        """多執行緒同時觸發淘汰時，不該冒出 KeyError。"""
        cache = compactor._Cache(limit=50)
        errors: list[Exception] = []

        def worker(n: int):
            try:
                for i in range(300):
                    cache.put(f"text-{n}-{i}", 0.5)
                    cache.get(f"text-{n}-{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"並發時發生例外：{errors[:3]}")
        self.assertLessEqual(len(cache._d), 50)

    def test_cache_hit_returns_same_value(self):
        cache = compactor._Cache(limit=10)
        cache.put("hello", 0.25)
        self.assertEqual(cache.get("hello"), 0.25)
        self.assertIsNone(cache.get("never-seen"))


class TestStatsAccuracy(unittest.TestCase):
    def test_no_savings_when_no_candidates(self):
        msgs = [{"role": "user", "content": "只有提問"}]
        _, stats = compactor.compact(msgs, "fake", entities=[])
        self.assertEqual(stats.saved_chars, 0)
        self.assertIn("0.0%", stats.summary())

    def test_fail_open_reports_zero_savings(self):
        """Jev 失敗時不能誤報省了 100%（這是實作時踩到的 bug）。"""
        import jev_client

        msgs = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "x" * 300}]},
            {"role": "assistant", "content": [{"type": "text", "text": "y"}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t2", "content": "z" * 300}]},
            {"role": "user", "content": "問題"},
        ]

        def boom(*a, **kw):
            raise jev_client.JevError("simulated")

        saved = jev_client.score_relevance
        jev_client.score_relevance = boom
        try:
            _, stats = compactor.compact(msgs, "fake", entities=[])
        finally:
            jev_client.score_relevance = saved

        self.assertEqual(stats.saved_chars, 0)
        self.assertNotIn("100.0%", stats.summary())
        self.assertTrue(stats.error)


if __name__ == "__main__":
    unittest.main(verbosity=2)
