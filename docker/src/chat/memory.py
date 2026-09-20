"""
Server-authoritative conversation memory.

The client no longer sends `conversation_history` (see `docker/src/chat/models.py`) —
every turn, `build_message_window` rebuilds it from Supabase `chat_messages`,
budgets it to fit the model's context window, and prepends a rolling summary
once the session grows long enough to trigger one. This replaces the old
`core/chat.py::_format_conversation_history` (a hard last-6-messages cutoff
with no token awareness) and the agent's `SessionMemory`, which was rebuilt
fresh and discarded every request despite being named like it persisted.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, List, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.chat.prompts import build_summary_messages
from src.clients import get_llm
from src.config import (
    get_chat_context_token_budget,
    get_chat_history_summary_trigger_messages,
    get_chat_reserved_output_tokens,
)
from src.storage.supabase_client import SupabaseClient
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.memory")

CountTokens = Callable[[BaseMessage], int]


def _approx_token_count(text: str) -> int:
    """~4 chars/token heuristic — a soft budget check, not exact accounting."""
    return max(1, len(text) // 4)


def count_message_tokens(message: BaseMessage) -> int:
    content = message.content if isinstance(message.content, str) else str(message.content)
    return _approx_token_count(content)


def truncate_to_budget(
    messages: Sequence[BaseMessage],
    budget_tokens: int,
    count_tokens: CountTokens = count_message_tokens,
) -> List[BaseMessage]:
    """
    Pure, no I/O: walk newest-to-oldest and keep whatever fits under
    `budget_tokens`. Always keeps at least the single newest message, even if
    it alone exceeds the budget, so a very long last turn doesn't vanish
    entirely.
    """
    kept: List[BaseMessage] = []
    used = 0
    for message in reversed(messages):
        cost = count_tokens(message)
        if used + cost > budget_tokens and kept:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return kept


def row_to_message(row: dict) -> Optional[BaseMessage]:
    """
    Reconstruct history as plain Human/AI turns — prior tool calls are
    already resolved into the final answer text, so there's no need to
    faithfully replay `tool_calls`/`ToolMessage` pairs across HTTP requests.
    """
    role = row.get("role")
    content = row.get("content") or ""
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return None


def _unsummarized_rows(rows: List[dict], summary_up_to_created_at: Optional[str]) -> List[dict]:
    """
    `chat_messages.id` is a uuid, not sequential, so "unsummarized" is tracked
    by `created_at` instead. Postgres returns `timestamptz` as ISO 8601 in a
    consistent format, so plain string comparison matches chronological order
    without needing to parse to `datetime`.
    """
    if summary_up_to_created_at is None:
        return rows
    return [r for r in rows if (r.get("created_at") or "") > summary_up_to_created_at]


async def build_message_window(
    supabase: SupabaseClient,
    chat_id: str,
    system_prompt: str,
    question_for_model: str,
    *,
    model_context_budget: Optional[int] = None,
    reserved_output_tokens: Optional[int] = None,
) -> List[BaseMessage]:
    """
    Build the full message list for one turn. `question_for_model` is the
    (possibly page-context-enhanced) question actually sent to the model;
    the raw question is already persisted as the latest `chat_messages` row
    by `chat_session.prepare_chat_turn` before this is called, so that row is
    excluded from the reconstructed tail and re-added here instead.
    """
    budget_total = model_context_budget if model_context_budget is not None else get_chat_context_token_budget()
    reserved = reserved_output_tokens if reserved_output_tokens is not None else get_chat_reserved_output_tokens()

    session = await run_sync(supabase.get_chat_session, chat_id) or {}
    rows = await run_sync(supabase.get_chat_messages, chat_id) or []

    # The current turn's user message was already inserted by prepare_chat_turn
    # right before this runs, so it's the last row — drop it, it's re-added below.
    history_rows = rows[:-1] if rows and rows[-1].get("role") == "user" else rows
    tail_rows = _unsummarized_rows(history_rows, session.get("summary_up_to_created_at"))

    reconstructed = [m for m in (row_to_message(r) for r in tail_rows) if m is not None]

    budget = budget_total - reserved - _approx_token_count(system_prompt) - _approx_token_count(question_for_model)
    windowed = truncate_to_budget(reconstructed, max(budget, 0))

    messages: List[BaseMessage] = [SystemMessage(content=system_prompt)]
    summary = session.get("session_summary")
    if summary:
        messages.append(SystemMessage(content=f"Summary of earlier conversation:\n{summary}"))
    messages.extend(windowed)
    messages.append(HumanMessage(content=question_for_model))
    return messages


async def maybe_trigger_summarization(supabase: SupabaseClient, chat_id: str) -> None:
    """
    Fire a best-effort background summarization pass once the unsummarized
    tail grows past the configured trigger. Non-blocking: the caller (the
    turn that just finished) does not wait on this.
    """
    try:
        session = await run_sync(supabase.get_chat_session, chat_id) or {}
        rows = await run_sync(supabase.get_chat_messages, chat_id) or []
    except Exception:
        logger.exception("Failed to check summarization trigger for chat_id=%s", chat_id)
        return

    tail_rows = _unsummarized_rows(rows, session.get("summary_up_to_created_at"))
    if len(tail_rows) < get_chat_history_summary_trigger_messages():
        return

    asyncio.create_task(_summarize_and_store(supabase, chat_id, session.get("session_summary") or "", tail_rows))


async def _summarize_and_store(supabase: SupabaseClient, chat_id: str, prior_summary: str, tail_rows: List[dict]) -> None:
    try:
        history_text = "\n".join(f"{r.get('role')}: {r.get('content')}" for r in tail_rows)
        latest_question = next((r.get("content", "") for r in reversed(tail_rows) if r.get("role") == "user"), "")
        messages = build_summary_messages(history_text, prior_summary, latest_question)

        llm = get_llm("chat_session_compressor", temperature=0.2)
        response = await llm.ainvoke(messages)
        summary = (response.content or "").strip() if isinstance(response.content, str) else str(response.content)
        if not summary:
            return

        # tail_rows preserves the ascending created_at order of get_chat_messages,
        # so the last row is the most recent one covered by this summary.
        last_created_at = tail_rows[-1].get("created_at")
        await run_sync(supabase.update_chat_session_summary, chat_id, summary, last_created_at)
        logger.info("Updated rolling summary for chat_id=%s up to created_at=%s", chat_id, last_created_at)
    except Exception:
        logger.exception("Background summarization failed for chat_id=%s", chat_id)


__all__ = [
    "build_message_window",
    "maybe_trigger_summarization",
    "truncate_to_budget",
    "row_to_message",
    "count_message_tokens",
]
