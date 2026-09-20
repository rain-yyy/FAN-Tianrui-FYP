"""
LangGraph definition for the chat agent: a two-node loop (agent <-> tools)
driven entirely by the model's own native tool-calling decisions.

There is no intent classifier and no DIRECT/LIGHT/DEEP routing table like the
old `docker/src/agent/graph.py` — if the model's first response carries no
`tool_calls`, `route_after_agent` goes straight to END, which *is* the fast
path. The only external control is a hard cap on tool-calling rounds
(`max_tool_iterations`), replacing the old hand-rolled `check_stop_conditions`
/`_hard_gate_check` gating trees.

Hitting the cap never drops a pending tool call and never ends the turn on an
unresolved `AIMessage(tool_calls=...)` — every tool call the model makes is
always executed (`tools_node` always runs before the turn can end), and once
the cap is reached the loop routes to `final_answer_node`, which calls the
*unbound* LLM (no tools available, so it structurally cannot ask for another
one) with an explicit "stop investigating, answer now" instruction. This
guarantees the turn always ends on a real text answer instead of silently
returning "" when a question needed more tool rounds than the cap allows.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from src.chat.state import ChatState
from src.chat.tools import build_tools_for_session
from src.clients import get_llm
from src.config import get_chat_max_tool_iterations

_ITERATION_LIMIT_NOTICE = (
    "You have reached the maximum number of tool calls allowed for this turn. "
    "Do not request any more tools — answer now using only the information "
    "already gathered above, noting explicitly which parts remain unverified."
)


def create_chat_graph(
    vector_store_path: str,
    graph_path: Optional[str],
    repo_root: Optional[str],
    web_search_config: Optional[Dict[str, Any]] = None,
    *,
    llm: Optional[BaseChatModel] = None,
    tools: Optional[List[BaseTool]] = None,
) -> CompiledStateGraph:
    """
    Build a fresh graph (fresh tools + fresh in-memory checkpointer) for one
    chat turn. Not reused across turns/requests — see the memory design in
    `docker/src/chat/memory.py` for why cross-turn state lives in Supabase,
    not in a persistent LangGraph checkpointer.

    `llm`/`tools` are injection points, not production knobs: real callers
    (`chat/service.py`) always omit them and get the real chat-agent model
    plus the real per-session tool list built from `vector_store_path` etc.
    Tests pass a scripted fake model (and/or fake tools) here to exercise
    the iteration-cap / never-drop-a-tool-call / forced-final-answer routing
    in `route_after_agent`/`route_after_tools` without a real LLM or repo —
    see `docker/tests/test_chat_graph.py`.
    """
    if tools is None:
        tools = build_tools_for_session(vector_store_path, graph_path, repo_root, web_search_config)
    if llm is None:
        # max_tokens must be set explicitly: some OpenRouter backends default an unset
        # max_tokens to "rest of the context window" for the *completion*, which then
        # fails outright once input + that default exceeds the model's context length.
        llm = get_llm("chat_agent", temperature=0.2, max_tokens=4096)
    bound_llm = llm.bind_tools(tools) if tools else llm
    tool_node = ToolNode(tools) if tools else None

    async def agent_node(state: ChatState) -> Dict[str, Any]:
        response = await bound_llm.ainvoke(state["messages"])
        return {"messages": [response]}

    async def tools_node(state: ChatState) -> Dict[str, Any]:
        result = await tool_node.ainvoke(state)
        return {**result, "tool_call_count": state["tool_call_count"] + 1}

    async def final_answer_node(state: ChatState) -> Dict[str, Any]:
        # Deliberately the unbound `llm`, not `bound_llm`: with no tools
        # available the model cannot emit another tool_calls-only message,
        # so this always produces a real text answer.
        forced_messages = list(state["messages"]) + [SystemMessage(content=_ITERATION_LIMIT_NOTICE)]
        response = await llm.ainvoke(forced_messages)
        return {"messages": [response]}

    def route_after_agent(state: ChatState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END
        return "tools"

    def route_after_tools(state: ChatState) -> str:
        if state["tool_call_count"] >= state["max_tool_iterations"]:
            return "final_answer"
        return "agent"

    builder = StateGraph(ChatState)
    builder.add_node("agent", agent_node)
    if tool_node is not None:
        builder.add_node("tools", tools_node)
        builder.add_node("final_answer", final_answer_node)
        builder.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
        builder.add_conditional_edges("tools", route_after_tools, {"agent": "agent", "final_answer": "final_answer"})
        builder.add_edge("final_answer", END)
    else:
        builder.add_conditional_edges("agent", route_after_agent, {END: END})
    builder.set_entry_point("agent")

    return builder.compile(checkpointer=MemorySaver())


def initial_state(messages: list, max_tool_iterations: Optional[int] = None) -> ChatState:
    return ChatState(
        messages=messages,
        tool_call_count=0,
        max_tool_iterations=max_tool_iterations or get_chat_max_tool_iterations(),
    )


__all__ = ["create_chat_graph", "initial_state"]
