<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.

## Jev 判斷層（MCP: jev_verify / jev_screen / jev_find）

Jev（TypeSafe System One）是「快速、便宜、帶校準機率」的結構化判斷模型。官方定位：給它**狹窄、結構化的決策**（narrow, structured decisions），它不生成文字、不寫程式。

**該呼叫 Jev 的時機**（封閉判斷、不需文字解釋）：
- 驗證主張／事實（claim 有沒有證據支持）
- 內容篩選（大量項目裡挑符合條件的）
- 分類／標記（多空、相關性、類別歸屬）
- 排序／re-rank 候選
- 是非判斷 + 機率

**不該用 Jev**：
- 生成文字、理由、摘要 → 用 DeepSeek/Groq
- 寫程式、長程推理、agent 動作 → 直接做

**門檻**：Jev 回傳機率分布 → 信心 ≥0.9 直接採納，偏低則轉人工或再問一次。
