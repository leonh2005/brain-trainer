#!/usr/bin/env python3
"""context 壓縮核心：挑出不再相關的舊 tool 輸出，替換成移除通知。

設計對齊 LiteLLM 的 typesafe compaction guardrail：
  - system / 最後一則 user 訊息永不觸碰
  - 保護最近一輪的 tool exchange
  - 只處理 ≥200 字元的 tool_result
  - 低於門檻者替換內容，但保留 tool_use_id 與結構
"""
from __future__ import annotations

import copy
import hashlib
import logging
import threading
from dataclasses import dataclass

import jev_client
from privacy import Pseudonyms, apply_privacy, list_entities

REMOVAL_NOTICE = (
    "[Tool result removed by TypeSafe compaction: "
    "judged no longer relevant to the current task]"
)

MIN_CHARS = 200        # 太短的內容不值得壓（LiteLLM 的預設門檻）
TRUNC = 600            # 送給 Jev 的每筆字元上限
THRESHOLD = 0.2        # 低於此機率即視為可移除
MAX_CANDIDATES = 120   # 單次最多評幾筆，控制延遲
CACHE_MAX = 2000       # 內容 hash 快取上限

log = logging.getLogger("jev-compaction")


@dataclass
class Candidate:
    idx: int          # 在候選清單中的序號，用來當評分結果的 key
    msg_idx: int
    block_idx: int
    tool_use_id: str
    text: str
    chars: int


@dataclass
class CompactStats:
    candidates: int = 0
    dropped: int = 0
    chars_before: int = 0
    chars_after: int = 0
    cached: int = 0
    batches: int = 0
    error: str = ""
    entity_names: int = 0

    @property
    def saved_chars(self) -> int:
        return max(0, self.chars_before - self.chars_after)

    def summary(self) -> str:
        pct = (100 * self.saved_chars / self.chars_before) if self.chars_before else 0
        s = (f"候選 {self.candidates} 筆 → 移除 {self.dropped} 筆，"
             f"省 {self.saved_chars:,} 字元 ({pct:.1f}%)，"
             f"快取命中 {self.cached}，批次 {self.batches}")
        if self.error:
            s += f"  ⚠ {self.error}"
        return s


class _Cache:
    """內容 hash → 相關性機率。內容不變就不必重問 Jev。

    proxy 是多執行緒，get/put 必須加鎖：淘汰邏輯是
    check-then-delete-then-set，兩個執行緒同時觸發會讓其中一個 KeyError。
    """

    def __init__(self, limit: int = CACHE_MAX) -> None:
        self._d: dict[str, float] = {}
        self._limit = limit
        self._lock = threading.Lock()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:32]

    def get(self, text: str) -> float | None:
        with self._lock:
            return self._d.get(self._key(text))

    def put(self, text: str, value: float) -> None:
        key = self._key(text)
        with self._lock:
            if len(self._d) >= self._limit:
                # 簡單淘汰：清掉一部分（不需要精確 LRU）
                for k in list(self._d)[: self._limit // 2]:
                    self._d.pop(k, None)
            self._d[key] = value


def _block_text(block: dict) -> str:
    """取出 tool_result 的文字內容（可能是字串或 content 陣列）。"""
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return ""


def _replace_block_text(block: dict, new_text: str) -> None:
    """就地替換內容，但保留 tool_use_id 與既有的 content 型態。"""
    c = block.get("content")
    if isinstance(c, list):
        # 保留第一個 text 區塊的位置，其餘移除
        kept = [p for p in c if isinstance(p, dict) and p.get("type") != "text"]
        block["content"] = [{"type": "text", "text": new_text}] + kept
    else:
        block["content"] = new_text


def current_task(messages: list[dict]) -> str:
    """最後一則 user 訊息裡的文字＝當前任務。"""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        c = msg.get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
        if isinstance(c, list):
            parts = [
                p.get("text", "") for p in c
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            joined = "\n".join(t for t in parts if t.strip())
            if joined.strip():
                return joined.strip()
    return ""


def select_candidates(messages: list[dict]) -> list[Candidate]:
    """挑出可壓縮的 tool_result。

    保護規則：
      1. 最後一則 user 訊息整則跳過（那是當下的提問）
      2. 從尾端往前，第一則含 tool_result 的訊息整則保留（最近一輪的交換）
      3. 其餘 tool_result 且內容 ≥ MIN_CHARS 者為候選
    """
    last_user = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user = i
            break

    protected: set[int] = {last_user} if last_user >= 0 else set()
    for i in range(len(messages) - 1, -1, -1):
        if i in protected:
            continue
        c = messages[i].get("content")
        if isinstance(c, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in c
        ):
            protected.add(i)
            break

    out: list[Candidate] = []
    for i, msg in enumerate(messages):
        if i in protected:
            continue
        c = msg.get("content")
        if not isinstance(c, list):
            continue
        for j, block in enumerate(c):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            text = _block_text(block)
            if len(text) < MIN_CHARS:
                continue
            out.append(Candidate(-1, i, j, block.get("tool_use_id", ""), text, len(text)))

    # 只評最近 N 筆（極舊的多半早已被壓縮過）
    if len(out) > MAX_CANDIDATES:
        out = out[-MAX_CANDIDATES:]
    for n, c in enumerate(out):
        c.idx = n
    return out


def compact(messages: list[dict], api_key: str, anon: bool = True,
            threshold: float = THRESHOLD, timeout: float = 10.0,
            cache: _Cache | None = None,
            entities: list[str] | None = None) -> tuple[list[dict], CompactStats]:
    """主入口。回傳 (新的 messages, 統計)。失敗時回傳原 messages（fail-open）。"""
    stats = CompactStats()
    if entities is None:
        entities = list_entities()
    stats.entity_names = len(entities)
    cache = cache if cache is not None else _Cache()

    cands = select_candidates(messages)
    stats.candidates = len(cands)
    if not cands:
        return messages, stats

    task = current_task(messages)
    p = Pseudonyms()

    # 逐字元統計只算候選，避免把整段對話都算進來
    stats.chars_before = sum(c.chars for c in cands)

    # 先套隱私處理，再送去評分（同一個 pseudonyms 實例確保代號一致）
    safe_task = apply_privacy(task, p, anon, entities) or "（未提供任務描述）"
    safe_texts = {c.idx: apply_privacy(c.text[:TRUNC], p, anon, entities) for c in cands}

    scores: dict[int, float] = {}
    pending: list[Candidate] = []
    for c in cands:
        hit = cache.get(c.text)
        if hit is not None:
            scores[c.idx] = hit
            stats.cached += 1
        else:
            pending.append(c)

    for start in range(0, len(pending), jev_client.BATCH_SIZE):
        batch = pending[start:start + jev_client.BATCH_SIZE]
        items = [(str(c.idx), safe_texts[c.idx]) for c in batch]
        try:
            got = jev_client.score_relevance(items, safe_task, api_key, timeout=timeout)
        except Exception as e:  # noqa: BLE001 - fail-open 是設計要求
            stats.error = f"{type(e).__name__}: {e}"
            log.warning("Jev 呼叫失敗，這次不壓縮：%s", e)
            # 沒有壓縮就沒有省下任何字元——不設的話 stats 會誤報成省了 100%
            stats.chars_after = stats.chars_before
            return messages, stats
        stats.batches += 1
        for c in batch:
            v = got.get(str(c.idx))
            if v is None:
                stats.error = "回應缺少部分答案"
                stats.chars_after = stats.chars_before
                return messages, stats
            scores[c.idx] = v
            cache.put(c.text, v)

    # 套用替換
    new_messages = messages
    dropped_chars = 0
    for c in cands:
        if scores.get(c.idx, 1.0) >= threshold:
            continue
        if new_messages is messages:
            new_messages = copy.deepcopy(messages)   # 只在真的需要改時才複製
        _replace_block_text(new_messages[c.msg_idx]["content"][c.block_idx], REMOVAL_NOTICE)
        stats.dropped += 1
        dropped_chars += c.chars - len(REMOVAL_NOTICE)

    stats.chars_after = stats.chars_before - dropped_chars
    return new_messages, stats
