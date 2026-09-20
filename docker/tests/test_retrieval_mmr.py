"""
Regression tests for `mmr_select` in `src/core/retrieval.py`.

`mmr_select`'s only production caller is `RAGSearchEngine.search` in
`src/chat/tools/rag_tool.py`, which was calling it with a stray positional
`query` argument that no longer matches the function's keyword-only signature
(`candidates`, then `*`, `top_n`, `lambda_mult`, `tokenizer`) — every
`rag_search` tool call raised `TypeError` at runtime, silently swallowed by
the tool's own try/except into a "Search failed" message. These tests pin
the correct call shape and basic MMR behavior so a future signature change
can't reintroduce that mismatch without a fast, no-service-dependency test
catching it.
"""
from langchain_core.documents import Document

from src.core.retrieval import RankedCandidate, mmr_select


def _candidate(key: str, content: str, score: float) -> RankedCandidate:
    return RankedCandidate(
        key=key,
        category="code",
        doc=Document(page_content=content, metadata={"source": key}),
        final_score=score,
    )


def test_mmr_select_accepts_keyword_only_call():
    candidates = [
        _candidate("a", "def add(a, b): return a + b", 0.9),
        _candidate("b", "def subtract(a, b): return a - b", 0.7),
        _candidate("c", "def multiply(a, b): return a * b", 0.5),
    ]

    selected = mmr_select(candidates, top_n=2, lambda_mult=0.5)

    assert len(selected) == 2
    assert selected[0].key == "a"


def test_mmr_select_prefers_diversity_over_near_duplicates():
    near_dup_a = _candidate("dup1", "def process_user_data(user): validate(user); save(user)", 0.95)
    near_dup_b = _candidate("dup2", "def process_user_data(user): validate(user); save(user); log(user)", 0.94)
    distinct = _candidate("distinct", "class DatabaseConnectionPool: def __init__(self): pass", 0.6)

    selected = mmr_select([near_dup_a, near_dup_b, distinct], top_n=2, lambda_mult=0.3)

    selected_keys = {c.key for c in selected}
    assert "dup1" in selected_keys
    assert "distinct" in selected_keys
    assert "dup2" not in selected_keys


def test_mmr_select_caps_to_top_n():
    candidates = [_candidate(str(i), f"doc {i}", 1.0 - i * 0.01) for i in range(10)]

    selected = mmr_select(candidates, top_n=3, lambda_mult=0.5)

    assert len(selected) == 3


def test_mmr_select_empty_candidates_returns_empty():
    assert mmr_select([], top_n=5, lambda_mult=0.5) == []


def test_mmr_select_zero_top_n_returns_empty():
    candidates = [_candidate("a", "content", 1.0)]
    assert mmr_select(candidates, top_n=0, lambda_mult=0.5) == []
