"""
Durable, cross-session repo facts — the read/write path for `repo_memory`
(see `docker/sql/2026_chat_memory.sql`), realizing what the old, deleted
`RepoFactsMemory` (`docker/src/agent/state.py`) always claimed to be but
never was: per that module's own admission, "it was rebuilt fresh and
discarded every request." Facts persist per `repo_url` in Supabase and reach
the agent via `chat/prompts.py::render_repo_facts` -> `build_system_prompt`'s
`repo_facts` argument.

Kept as its own module rather than folded into `memory.py`: the cadence and
trigger are different. Conversation summaries update on message count, per
session; repo facts update on "did this turn do real structural
investigation" with a cooldown, independent of any one session.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from src.chat.models import ToolTrajectoryStep
from src.chat.prompts import build_repo_memory_extraction_messages
from src.clients import get_llm
from src.config import get_chat_repo_memory_min_update_interval_hours
from src.storage.supabase_client import SupabaseClient
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.repo_memory")

# Only these tools' successful results are treated as strong-enough evidence
# to justify a fact-extraction pass; a pure rag_search or no-tool-call turn
# is low signal and skipped.
_STRUCTURAL_TOOLS = {"code_graph", "file_read", "repo_map", "grep_search"}

_FACT_KEYS = (
    "module_responsibilities",
    "key_entrypoints",
    "tech_stack",
    "core_constraints",
    "failed_approaches",
)


def _had_structural_success(trajectory: List[ToolTrajectoryStep]) -> bool:
    return any(step.tool in _STRUCTURAL_TOOLS and step.status == "success" for step in trajectory)


def _cooldown_elapsed(updated_at: Optional[str]) -> bool:
    if not updated_at:
        return True
    try:
        last = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    interval = timedelta(hours=get_chat_repo_memory_min_update_interval_hours())
    return datetime.now(timezone.utc) - last >= interval


def _build_transcript_text(messages: List[BaseMessage]) -> str:
    lines: List[str] = []
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                lines.append(f"tool_call: {tc['name']}({tc.get('args', {})})")
        elif isinstance(msg, ToolMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            lines.append(f"tool_result[{msg.name}]: {content[:500]}")
        elif isinstance(msg, AIMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if content:
                lines.append(f"final_answer: {content[:1000]}")
    return "\n".join(lines)


def _parse_facts(raw: str) -> Optional[Dict[str, Any]]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return {key: parsed[key] for key in _FACT_KEYS if key in parsed}


async def maybe_update_repo_memory(
    supabase: SupabaseClient,
    repo_url: str,
    messages: List[BaseMessage],
    trajectory: List[ToolTrajectoryStep],
) -> None:
    """
    Fire a best-effort background fact-extraction pass when this turn did
    real structural investigation and the per-repo cooldown has elapsed.
    Non-blocking: the caller (the turn that just finished) does not wait on
    the extraction LLM call, only on the cheap read used to check eligibility.
    """
    if not _had_structural_success(trajectory):
        return

    try:
        row = await run_sync(supabase.get_repo_memory, repo_url)
    except Exception:
        logger.exception("Failed to check repo_memory cooldown for repo_url=%s", repo_url)
        return

    if not _cooldown_elapsed((row or {}).get("updated_at")):
        return

    prior_facts = (row or {}).get("facts") or {}
    asyncio.create_task(_extract_and_store(supabase, repo_url, messages, prior_facts))


async def _extract_and_store(
    supabase: SupabaseClient,
    repo_url: str,
    messages: List[BaseMessage],
    prior_facts: Dict[str, Any],
) -> None:
    try:
        transcript_text = _build_transcript_text(messages)
        extraction_messages = build_repo_memory_extraction_messages(prior_facts, transcript_text)
        llm = get_llm("chat_repo_memory_extractor", temperature=0.2, max_tokens=800)
        response = await llm.ainvoke(extraction_messages)
        raw = response.content if isinstance(response.content, str) else str(response.content)

        facts = _parse_facts(raw)
        if facts is None:
            logger.warning("repo_memory extraction returned unparseable JSON for repo_url=%s", repo_url)
            return

        await run_sync(supabase.upsert_repo_memory, repo_url, facts)
        logger.info("Updated repo_memory facts for repo_url=%s", repo_url)
    except Exception:
        logger.exception("Background repo_memory extraction failed for repo_url=%s", repo_url)


__all__ = ["maybe_update_repo_memory"]
