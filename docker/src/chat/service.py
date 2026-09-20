"""
`ChatTurnService`: ties session prep, server-authoritative memory, the
LangGraph agent, and Supabase persistence together for one chat turn.

This is the single place both `/chat` (non-streaming) and `/chat/stream`
(SSE) go through — there is no longer a separate RAG-only pipeline and
Agent-only pipeline (`core/chat.py` vs. `agent/graph.py` + `agent/runner.py`).
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi import HTTPException
from langchain_core.messages import AIMessage, ToolMessage

from src.chat.events import format_sse
from src.chat.graph import create_chat_graph, initial_state
from src.chat.memory import build_message_window, maybe_trigger_summarization
from src.chat.models import ChatTurnRequest, ChatTurnResponse, ToolTrajectoryStep
from src.chat.prompts import build_system_prompt, render_repo_facts
from src.chat.repo_memory import maybe_update_repo_memory
from src.config import get_chat_max_tool_iterations, get_web_search_config
from src.core.chat_session import prepare_chat_turn
from src.core.path_resolver import resolve_agent_paths
from src.storage.supabase_client import SupabaseClient, get_supabase_client
from src.utils.async_utils import run_sync
from src.utils.json_utils import to_jsonable
from src.utils.logger import setup_logger

logger = setup_logger("app.chat.service")


def _extract_final_answer(messages: List[Any]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
    return ""


def _extract_sources_from_artifact(tool_name: str, artifact: Dict[str, Any]) -> List[str]:
    sources: List[str] = []
    if tool_name == "rag_search":
        sources.extend(r["source"] for r in artifact.get("results", []) if r.get("source"))
    elif tool_name == "grep_search":
        sources.extend(artifact.get("sources", []))
    elif tool_name == "code_graph":
        sources.extend(m["file"] for m in artifact.get("matches", []) if m.get("file"))
    elif tool_name == "file_read":
        file_path = artifact.get("file_path")
        line_range = artifact.get("line_range")
        if file_path and line_range:
            sources.append(f"{file_path}:{line_range[0]}-{line_range[1]}")
        elif file_path:
            sources.append(file_path)
    elif tool_name == "web_search":
        sources.extend(f"web:{u}" for u in artifact.get("urls", []))
    return sources


def _extract_trajectory_and_sources(messages: List[Any]) -> Tuple[List[ToolTrajectoryStep], List[str]]:
    call_meta: Dict[str, Dict[str, Any]] = {}
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                call_meta[tc["id"]] = {"tool": tc["name"], "arguments": tc.get("args", {}) or {}}

    trajectory: List[ToolTrajectoryStep] = []
    sources: List[str] = []
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        meta = call_meta.get(msg.tool_call_id, {"tool": msg.name or "unknown", "arguments": {}})
        artifact = msg.artifact if isinstance(msg.artifact, dict) else {}
        content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
        status = "error" if artifact.get("error") else "success"
        trajectory.append(ToolTrajectoryStep(
            tool=meta["tool"],
            arguments=meta["arguments"],
            status=status,
            summary=content_str[:400],
        ))
        sources.extend(_extract_sources_from_artifact(meta["tool"], artifact))

    seen: set = set()
    unique_sources = [s for s in sources if not (s in seen or seen.add(s))]
    return trajectory, unique_sources


class ChatTurnService:
    def __init__(self, supabase_client: Optional[SupabaseClient] = None):
        self.supabase = supabase_client or get_supabase_client()

    async def _prepare(self, request: ChatTurnRequest):
        turn = await run_sync(
            prepare_chat_turn,
            self.supabase,
            question=request.question,
            repo_url=request.repo_url,
            user_id=request.user_id,
            chat_id=request.chat_id,
            current_page_context=request.current_page_context,
        )
        graph_path, repo_root = resolve_agent_paths(request.repo_url, turn.vector_store_path)
        return turn, graph_path, repo_root

    async def _build_graph_and_state(self, request: ChatTurnRequest, turn, graph_path, repo_root):
        repo_memory_row = await run_sync(self.supabase.get_repo_memory, request.repo_url)
        repo_facts_text = render_repo_facts((repo_memory_row or {}).get("facts") or {})
        system_prompt = build_system_prompt(request.repo_url, repo_facts_text)
        messages = await build_message_window(self.supabase, turn.chat_id, system_prompt, turn.enhanced_question)
        max_iterations = get_chat_max_tool_iterations()
        graph = create_chat_graph(turn.vector_store_path, graph_path, repo_root, get_web_search_config())
        state = initial_state(messages, max_iterations)
        return graph, state, max_iterations

    async def _persist_assistant_turn(self, chat_id: str, answer: str, sources: List[str], trajectory: List[ToolTrajectoryStep], iterations: int) -> None:
        metadata = to_jsonable({
            "sources": sources,
            "tool_trajectory": [t.model_dump() for t in trajectory],
            "iterations": iterations,
            "mode": "agent",
        })
        saved = await run_sync(self.supabase.add_chat_message, chat_id, "assistant", answer, metadata)
        if saved is None:
            raise HTTPException(status_code=500, detail="Failed to save assistant message")
        await maybe_trigger_summarization(self.supabase, chat_id)

    async def run(self, request: ChatTurnRequest) -> ChatTurnResponse:
        turn, graph_path, repo_root = await self._prepare(request)
        graph, state, _ = await self._build_graph_and_state(request, turn, graph_path, repo_root)

        config = {"configurable": {"thread_id": "turn"}}
        final_state = await graph.ainvoke(state, config=config)

        answer = _extract_final_answer(final_state["messages"])
        trajectory, sources = _extract_trajectory_and_sources(final_state["messages"])
        iterations = final_state.get("tool_call_count", 0)

        await self._persist_assistant_turn(turn.chat_id, answer, sources, trajectory, iterations)
        await maybe_update_repo_memory(self.supabase, request.repo_url, final_state["messages"], trajectory)

        return ChatTurnResponse(
            chat_id=turn.chat_id,
            repo_url=request.repo_url,
            answer=answer,
            sources=sources,
            tool_trajectory=trajectory,
            iterations=iterations,
        )

    async def run_streaming(self, request: ChatTurnRequest) -> AsyncGenerator[str, None]:
        try:
            turn, graph_path, repo_root = await self._prepare(request)
        except HTTPException as e:
            yield format_sse("error", {"detail": e.detail})
            return

        yield format_sse("turn_start", {"chat_id": turn.chat_id, "repo_url": request.repo_url})

        try:
            graph, state, max_iterations = await self._build_graph_and_state(request, turn, graph_path, repo_root)
            config = {"configurable": {"thread_id": "turn"}}
            iteration = 0

            async for event in graph.astream_events(state, version="v2", config=config):
                kind = event["event"]
                name = event.get("name")

                if kind == "on_chain_start" and name in ("agent", "final_answer"):
                    iteration += 1
                    yield format_sse("iteration_start", {"iteration": iteration, "max_iterations": max_iterations})

                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    delta = chunk.content if isinstance(chunk.content, str) else ""
                    if delta:
                        yield format_sse("answer_token", {"delta": delta})

                elif kind == "on_tool_start":
                    yield format_sse("tool_call_start", {
                        "tool": name,
                        "arguments": event["data"].get("input") or {},
                        "iteration": iteration,
                    })

                elif kind == "on_chain_end" and name == "tools":
                    output = event["data"].get("output") or {}
                    for msg in output.get("messages") or []:
                        if not isinstance(msg, ToolMessage):
                            continue
                        artifact = msg.artifact if isinstance(msg.artifact, dict) else {}
                        content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
                        yield format_sse("tool_call_result", {
                            "tool": msg.name or "unknown",
                            "status": "error" if artifact.get("error") else "success",
                            "summary": content_str[:400],
                        })

            snapshot = await graph.aget_state(config)
            final_messages = snapshot.values["messages"]
            answer = _extract_final_answer(final_messages)
            trajectory, sources = _extract_trajectory_and_sources(final_messages)
            iterations = snapshot.values.get("tool_call_count", 0)

            yield format_sse("answer_done", {"answer": answer, "sources": sources})

            await self._persist_assistant_turn(turn.chat_id, answer, sources, trajectory, iterations)
            await maybe_update_repo_memory(self.supabase, request.repo_url, final_messages, trajectory)

            yield format_sse("complete", {"chat_id": turn.chat_id, "repo_url": request.repo_url})

        except HTTPException as e:
            yield format_sse("error", {"detail": e.detail})
        except Exception as e:
            logger.exception("Chat streaming turn failed")
            yield format_sse("error", {"detail": str(e)})


__all__ = ["ChatTurnService"]
