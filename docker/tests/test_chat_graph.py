"""
Routing-behavior tests for `src/chat/graph.py`'s `agent <-> tools` loop.

These exercise the graph's actual behavioral contract -- the fast path when
the model asks for no tools, the iteration cap, and the "never drop a
pending tool call" guarantee -- via the `llm`/`tools` injection points on
`create_chat_graph`. No real LLM call, no real vector store/repo: a
scripted fake model stands in for `get_llm("chat_agent", ...)` and a
trivial `@tool`-decorated function stands in for a real per-session tool.
"""
from typing import Any, Callable, List

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from src.chat.graph import create_chat_graph, initial_state


@tool
def echo_tool(text: str) -> str:
    """Echo the given text back, for tests."""
    return f"echo:{text}"


class ScriptedChatModel:
    """Minimal fake satisfying create_chat_graph's LLM interface.

    `bind_tools` is a no-op (the fake's responses are scripted directly,
    not derived from an actual tool schema sent to a real API) so the same
    instance can stand in for both the bound and unbound `llm` the graph
    calls.
    """

    def __init__(self, respond: Callable[[List[Any]], AIMessage]):
        self._respond = respond

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        return self._respond(messages)


async def _run(llm: ScriptedChatModel, max_tool_iterations: int = 5):
    graph = create_chat_graph(
        vector_store_path="unused",
        graph_path=None,
        repo_root=None,
        llm=llm,
        tools=[echo_tool],
    )
    state = initial_state([HumanMessage(content="hi")], max_tool_iterations=max_tool_iterations)
    config = {"configurable": {"thread_id": "test"}}
    return await graph.ainvoke(state, config=config)


async def test_no_tool_calls_takes_the_fast_path_to_end():
    llm = ScriptedChatModel(lambda messages: AIMessage(content="direct answer"))

    final_state = await _run(llm)

    assert final_state["tool_call_count"] == 0
    assert final_state["messages"][-1].content == "direct answer"


async def test_a_tool_call_is_executed_then_the_loop_ends_on_the_next_plain_answer():
    calls = {"n": 0}

    def respond(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return AIMessage(content="", tool_calls=[{"name": "echo_tool", "args": {"text": "hi"}, "id": "call_1"}])
        return AIMessage(content="final answer using tool result")

    final_state = await _run(ScriptedChatModel(respond))

    assert final_state["tool_call_count"] == 1
    tool_messages = [m for m in final_state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "echo:hi"
    assert final_state["messages"][-1].content == "final answer using tool result"


async def test_iteration_cap_forces_a_real_answer_without_dropping_the_last_tool_call():
    calls = {"n": 0}

    def respond(messages):
        if any(isinstance(m, SystemMessage) and "maximum number of tool calls" in m.content for m in messages):
            return AIMessage(content="final answer after cap")
        calls["n"] += 1
        return AIMessage(
            content="",
            tool_calls=[{"name": "echo_tool", "args": {"text": str(calls["n"])}, "id": f"call_{calls['n']}"}],
        )

    final_state = await _run(ScriptedChatModel(respond), max_tool_iterations=2)

    # Both tool calls -- including the one that pushed the count to the cap --
    # were actually executed, not dropped in favor of ending the turn early.
    assert final_state["tool_call_count"] == 2
    tool_messages = [m for m in final_state["messages"] if isinstance(m, ToolMessage)]
    assert [m.content for m in tool_messages] == ["echo:1", "echo:2"]

    # The turn ends on a real text answer, not an unresolved tool_calls message.
    last = final_state["messages"][-1]
    assert isinstance(last, AIMessage)
    assert not getattr(last, "tool_calls", None)
    assert last.content == "final answer after cap"
