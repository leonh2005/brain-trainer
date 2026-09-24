#!/usr/bin/env python3
"""compactor / privacy 的單元測試。不呼叫真實 API。

執行：python3 -m unittest test_compactor -v
"""
from __future__ import annotations

import unittest

import compactor
from compactor import (
    REMOVAL_NOTICE, _block_text, _replace_block_text, current_task, select_candidates,
)
from privacy import Pseudonyms, apply_privacy, list_entities, redact


def _long(n: int = 300, ch: str = "x") -> str:
    return ch * n


def _msg(role: str, content):
    return {"role": role, "content": content}


def _tool_result(text: str, tid: str = "toolu_1"):
    return {"type": "tool_result", "tool_use_id": tid, "content": text}


class TestCandidateSelection(unittest.TestCase):
    def test_protects_last_user_message(self):
        msgs = [
            _msg("user", [_tool_result(_long())]),
            _msg("assistant", [{"type": "text", "text": "ok"}]),
            _msg("user", "現在的提問"),
        ]
        self.assertEqual(select_candidates(msgs), [])

    def test_protects_most_recent_tool_exchange(self):
        """最近一輪的 tool_result 要保留，較舊的才是候選。"""
        msgs = [
            _msg("user", [_tool_result(_long(300, "a"), "toolu_old")]),
            _msg("assistant", [{"type": "text", "text": "thinking"}]),
            _msg("user", [_tool_result(_long(300, "b"), "toolu_recent")]),
            _msg("assistant", [{"type": "text", "text": "done"}]),
            _msg("user", "現在的提問"),
        ]
        cands = select_candidates(msgs)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].tool_use_id, "toolu_old")

    def test_skips_short_content(self):
        msgs = [
            _msg("user", [_tool_result(_long(199))]),
            _msg("assistant", [{"type": "text", "text": "x"}]),
            _msg("user", [_tool_result(_long(200))]),
            _msg("user", "問題"),
        ]
        cands = select_candidates(msgs)
        # 199 字元被跳過，200 字元那筆也因「最近一輪保護」而保留 → 0 筆
        self.assertEqual(len(cands), 0)

    def test_string_content_short_skipped(self):
        msgs = [
            _msg("user", [_tool_result("short")]),
            _msg("assistant", [{"type": "text", "text": "x"}]),
            _msg("user", "問題"),
        ]
        self.assertEqual(select_candidates(msgs), [])

    def test_candidates_have_sequential_idx(self):
        msgs = [
            _msg("user", [_tool_result(_long(300, "a"), "t1")]),
            _msg("assistant", [{"type": "text", "text": "x"}]),
            _msg("user", [_tool_result(_long(300, "b"), "t2")]),
            _msg("assistant", [{"type": "text", "text": "x"}]),
            _msg("user", [_tool_result(_long(300, "c"), "t3")]),
            _msg("user", "問題"),
        ]
        cands = select_candidates(msgs)
        self.assertEqual([c.idx for c in cands], list(range(len(cands))))


class TestContentHandling(unittest.TestCase):
    def test_reads_string_content(self):
        self.assertEqual(_block_text({"content": "abc"}), "abc")

    def test_reads_array_content(self):
        block = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
        self.assertEqual(_block_text(block), "ab")

    def test_replace_keeps_tool_use_id_and_shape(self):
        block = {"type": "tool_result", "tool_use_id": "toolu_9",
                 "content": [{"type": "text", "text": "old"}]}
        _replace_block_text(block, REMOVAL_NOTICE)
        self.assertEqual(block["tool_use_id"], "toolu_9")
        self.assertEqual(block["type"], "tool_result")
        self.assertIsInstance(block["content"], list)
        self.assertEqual(block["content"][0]["text"], REMOVAL_NOTICE)

    def test_replace_string_stays_string(self):
        block = {"type": "tool_result", "tool_use_id": "t", "content": "old"}
        _replace_block_text(block, REMOVAL_NOTICE)
        self.assertEqual(block["content"], REMOVAL_NOTICE)


class TestCurrentTask(unittest.TestCase):
    def test_takes_last_user_text(self):
        msgs = [_msg("user", "第一個"), _msg("assistant", "回覆"), _msg("user", "最後的提問")]
        self.assertEqual(current_task(msgs), "最後的提問")

    def test_skips_tool_result_only_message(self):
        """最後一則 user 若只含 tool_result，要往回找到真正的提問。"""
        msgs = [
            _msg("user", "真正的提問"),
            _msg("assistant", [{"type": "text", "text": "x"}]),
            _msg("user", [_tool_result(_long())]),
        ]
        self.assertEqual(current_task(msgs), "真正的提問")

    def test_handles_array_text(self):
        msgs = [_msg("user", [{"type": "text", "text": "陣列形式的提問"}])]
        self.assertEqual(current_task(msgs), "陣列形式的提問")

    def test_empty_when_no_user_text(self):
        self.assertEqual(current_task([_msg("assistant", "只有回覆")]), "")


class TestPrivacy(unittest.TestCase):
    def setUp(self):
        self.p = Pseudonyms()
        self.entities = ["command-center", "chip-tracker", "telebot"]

    def _anon(self, text):
        return apply_privacy(text, self.p, True, self.entities)

    def test_paths_replaced(self):
        out = self._anon("檔案在 /Users/steven/CCProject/command-center/app.py 裡")
        self.assertNotIn("/Users/steven", out)
        self.assertNotIn("command-center", out)
        self.assertIn("app.py", out)   # 有副檔名的最後一層保留，維持語意

    def test_tilde_and_absolute_same_code(self):
        a = self._anon("~/CCProject/foo/x.py")
        b = self._anon("/Users/steven/CCProject/foo/x.py")
        # 取出 <PATH_n> 的部分比對
        import re
        ca = re.search(r"<PATH_\d+>", a)
        cb = re.search(r"<PATH_\d+>", b)
        self.assertIsNotNone(ca)
        self.assertIsNotNone(cb)
        self.assertEqual(ca.group(0), cb.group(0))

    def test_host_replaced(self):
        out = self._anon("連到 http://localhost:5950/ 看看")
        self.assertNotIn("localhost", out)
        self.assertIn("<HOST_", out)

    def test_project_name_in_plain_text(self):
        out = self._anon("telebot 的卡片壞了")
        self.assertNotIn("telebot", out)
        self.assertIn("<PROJECT_", out)

    def test_credentials_redacted(self):
        out = self._anon("key 是 sk-EXAMPLEONLYNOTAREALKEY000000")
        self.assertNotIn("sk-EXAMPLEONLYNOTAREALKEY000000", out)
        self.assertIn("[REDACTED_OPENAI_KEY]", out)

    def test_generic_dirs_are_not_entities(self):
        """scripts/logs 這類通用目錄名不該被代號化，否則 Jev 看不懂。"""
        ents = list_entities("/Users/steven/CCProject")
        for generic in ("scripts", "logs", "data", "node_modules"):
            self.assertNotIn(generic, ents)

    def test_shell_commands_are_not_entities(self):
        """本機真的有一個叫 mkdir 的目錄——把它當專案名會污染每一行 Bash 輸出。"""
        ents = list_entities("/Users/steven/CCProject")
        for cmd in ("mkdir", "grep", "find", "python3"):
            self.assertNotIn(cmd, ents)

    def test_non_path_absolute_untouched(self):
        """一般英文句子不該被亂改。"""
        src = "The quick brown fox jumps over the lazy dog."
        self.assertEqual(self._anon(src), src)

    def test_version_numbers_not_treated_as_ip(self):
        out = self._anon("第 10.5 章與 20~30 頁，售價 10.99")
        self.assertIn("10.5", out)
        self.assertIn("10.99", out)


class TestCredentialRedaction(unittest.TestCase):
    """憑證遮蔽是這個 proxy 唯一的安全要求，逐類別釘住。"""

    SAMPLES = {
        "openai": "OPENAI_API_KEY=sk-proj-EXAMPLEONLYNOTAREALKEY00000",
        "anthropic": "使用 sk-ant-api03-EXAMPLEONLYNOTAREALKEY0 呼叫",
        "typesafe": "key=apikey_EXAMPLEONLYNOTAREALKEY000000",
        "telegram": "TELEGRAM_BOT_TOKEN=0000000000:EXAMPLEONLYNOTAREALTELEGRAMTOKEN0",
        "aws_access": "AKIAEXAMPLEONLYNOTREAL",
        "aws_secret": 'aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"',
        "github": "ghp_EXAMPLEONLYNOTAREALTOKEN0000",
        "gitlab": "glpat-EXAMPLEONLYNOTAREAL00",
        "slack": "xoxb-EXAMPLEONLY-NOTAREALTOKEN00",
        "google": "AIzaEXAMPLEONLYNOTAREALKEY0000000000000",
        "jwt": "eyJEXAMPLEONLYAAAAAAAA.eyJEXAMPLEONLYBBBBBBBB.CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
        "ssh_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
        "basic_auth": "Authorization: Basic EXAMPLEONLYNOTREAL00",
        "curl_u": "curl -u admin:hunter2 https://api.example.com",
        "conn_string": "postgres://user:S3cretPwd@db.internal:5432/mydb",
        "password_assign": 'password = "MyPassw0rd!"',
        "single_quote": "api_key: 'abcdef1234567890'",
        "unquoted": "PASSWORD=hunter2hunter2",
        "env_upper": "FINMIND_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9",
        "tw_id": "身分證 A123456789",
        "email": "聯絡 admin@example.com",
    }

    def test_every_sample_is_masked(self):
        for name, text in self.SAMPLES.items():
            with self.subTest(sample=name):
                out = apply_privacy(text, Pseudonyms(), True, [])
                self.assertNotEqual(out, text, f"{name} 沒有被處理")
                self.assertIn("[REDACTED", out, f"{name} 沒有產生遮蔽記號")

    def test_conn_string_password_not_swallowed_as_email(self):
        """連線字串的密碼該標成一般遮蔽，不是 email（順序問題）。"""
        out = apply_privacy("postgres://user:S3cretPwd@db.internal:5432/mydb",
                            Pseudonyms(), True, [])
        self.assertNotIn("S3cretPwd", out)
        self.assertNotIn("REDACTED_EMAIL", out)

    def test_normal_text_untouched(self):
        """不能把正常內容也吃掉——過度遮蔽會讓 Jev 判斷失準。"""
        for s in [
            "The quick brown fox jumps over the lazy dog.",
            "def compute(x, y): return x + y",
            "總共 42 筆資料，耗時 1.9 秒",
            "http://localhost:5950/market-dashboard",
        ]:
            # localhost 那條會被偽名化，所以這裡只驗證「不會出現 REDACTED」
            self.assertNotIn("[REDACTED", redact(s), f"過度遮蔽：{s}")


class TestCompactFailOpen(unittest.TestCase):
    def test_returns_original_on_jev_error(self):
        """Jev 掛掉時必須原樣回傳，不能讓對話失敗。"""
        msgs = [
            _msg("user", [_tool_result(_long(300), "t1")]),
            _msg("assistant", [{"type": "text", "text": "x"}]),
            _msg("user", [_tool_result(_long(300), "t2")]),
            _msg("user", "問題"),
        ]
        original = str(msgs)

        import jev_client

        def boom(*a, **kw):
            raise jev_client.JevError("simulated failure")

        saved = jev_client.score_relevance
        jev_client.score_relevance = boom
        try:
            out, stats = compactor.compact(msgs, "fake-key", anon=True, entities=[])
        finally:
            jev_client.score_relevance = saved

        self.assertEqual(str(out), original)      # 內容未被改動
        self.assertTrue(stats.error)              # 有記錄錯誤
        self.assertEqual(stats.dropped, 0)

    def test_no_candidates_returns_early(self):
        msgs = [_msg("user", "只有一個提問")]
        out, stats = compactor.compact(msgs, "fake-key", entities=[])
        self.assertIs(out, msgs)
        self.assertEqual(stats.candidates, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
