"""
LangGraph state for the chat agent.

Deliberately small: native tool-calling means the model itself tracks intent,
what evidence it has, and when it's confident enough to stop — there is no
need to hand-track anchors/evidence-cards/confidence-scores the way the old
`docker/src/agent/state.py` (~40 fields) did. Everything the model needs is
either in `messages` already (a `ToolMessage` *is* the evidence) or is one of
the small scalars below.

`repo_url`/`vector_store_path`/`graph_path`/`repo_root` are deliberately NOT
state fields: no graph node reads them back — `create_chat_graph` closes over
them directly when building the tools/LLM, exactly as the design called for
("passed through tools/LLM construction, not baked into serializable state").
Putting them in state would just be dead weight nothing consumes.
"""
from __future__ import annotations

from typing import Annotated, List

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class ChatState(TypedDict):
    messages: Annotated[List[AnyMessage], add_messages]
    tool_call_count: int
    max_tool_iterations: int
