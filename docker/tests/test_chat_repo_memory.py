"""
Tests for `src/chat/repo_memory.py`'s trigger/cooldown gating and JSON
parsing — no real Supabase or LLM calls. The background extraction call
itself (`_extract_and_store`) is fire-and-forget by design and not
deterministically testable without a real/mocked LLM; these tests instead
pin the logic that decides *whether* it gets scheduled.
"""
from datetime import datetime, timedelta, timezone

from src.chat.models import ToolTrajectoryStep
from src.chat.repo_memory import (
    _build_transcript_text,
    _cooldown_elapsed,
    _had_structural_success,
    _parse_facts,
    maybe_update_repo_memory,
)
from langchain_core.messages import AIMessage, ToolMessage


def _step(tool: str, status: str = "success") -> ToolTrajectoryStep:
    return ToolTrajectoryStep(tool=tool, arguments={}, status=status, summary="")


def test_had_structural_success_true_for_successful_code_graph():
    assert _had_structural_success([_step("code_graph")]) is True


def test_had_structural_success_false_for_rag_search_only():
    assert _had_structural_success([_step("rag_search")]) is False


def test_had_structural_success_false_for_failed_structural_call():
    assert _had_structural_success([_step("file_read", status="error")]) is False


def test_had_structural_success_false_for_empty_trajectory():
    assert _had_structural_success([]) is False


def test_cooldown_elapsed_true_when_no_prior_timestamp():
    assert _cooldown_elapsed(None) is True


def test_cooldown_elapsed_false_within_window():
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert _cooldown_elapsed(recent) is False


def test_cooldown_elapsed_true_after_window():
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    assert _cooldown_elapsed(old) is True


def test_parse_facts_plain_json():
    raw = '{"tech_stack": {"backend": "FastAPI"}, "key_entrypoints": ["a.py"]}'
    facts = _parse_facts(raw)
    assert facts == {"tech_stack": {"backend": "FastAPI"}, "key_entrypoints": ["a.py"]}


def test_parse_facts_strips_markdown_fence():
    raw = '```json\n{"core_constraints": ["no eval()"]}\n```'
    facts = _parse_facts(raw)
    assert facts == {"core_constraints": ["no eval()"]}


def test_parse_facts_drops_unknown_keys():
    raw = '{"tech_stack": {"a": "b"}, "not_a_real_field": 123}'
    facts = _parse_facts(raw)
    assert facts == {"tech_stack": {"a": "b"}}


def test_parse_facts_invalid_json_returns_none():
    assert _parse_facts("not json at all") is None


def test_parse_facts_non_object_returns_none():
    assert _parse_facts("[1, 2, 3]") is None


def test_build_transcript_text_includes_tool_calls_results_and_answer():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "grep_search", "args": {"pattern": "foo"}, "id": "1"}]),
        ToolMessage(content="found 3 matches", name="grep_search", tool_call_id="1"),
        AIMessage(content="foo appears in 3 places."),
    ]
    text = _build_transcript_text(messages)
    assert "tool_call: grep_search" in text
    assert "tool_result[grep_search]: found 3 matches" in text
    assert "final_answer: foo appears in 3 places." in text


class _FakeSupabase:
    def __init__(self, row):
        self._row = row

    def get_repo_memory(self, repo_url: str):
        return self._row


async def test_maybe_update_repo_memory_skips_when_no_structural_success(monkeypatch):
    scheduled = []
    monkeypatch.setattr("src.chat.repo_memory.asyncio.create_task", lambda coro: scheduled.append(coro))

    await maybe_update_repo_memory(_FakeSupabase(None), "https://example.com/repo", [], [_step("rag_search")])

    assert scheduled == []


async def test_maybe_update_repo_memory_skips_within_cooldown(monkeypatch):
    scheduled = []
    monkeypatch.setattr("src.chat.repo_memory.asyncio.create_task", lambda coro: scheduled.append(coro))
    recent_row = {"facts": {}, "updated_at": datetime.now(timezone.utc).isoformat()}

    await maybe_update_repo_memory(
        _FakeSupabase(recent_row), "https://example.com/repo", [], [_step("code_graph")]
    )

    assert scheduled == []


async def test_maybe_update_repo_memory_schedules_when_eligible(monkeypatch):
    scheduled = []

    def _fake_create_task(coro):
        scheduled.append(coro)
        coro.close()  # never actually run it — avoid a real LLM call in this test
        return None

    monkeypatch.setattr("src.chat.repo_memory.asyncio.create_task", _fake_create_task)

    await maybe_update_repo_memory(_FakeSupabase(None), "https://example.com/repo", [], [_step("code_graph")])

    assert len(scheduled) == 1
