"""
SSE event formatting for `/chat/stream`.

Event vocabulary (payload shapes documented alongside each `emit_*` call in
`docker/src/chat/service.py`): `turn_start`, `iteration_start`,
`tool_call_start`, `tool_call_result`, `answer_token`, `answer_done`,
`complete`, `error`. This replaces the RAG path's `retrieval_start/hyde_generated/
retrieval_done/answer_delta/answer_done` and the agent path's
`planning/subtask_parallel/tool_call/evaluation/synthesis` — one vocabulary
for the one unified endpoint.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from src.utils.json_utils import to_jsonable


def format_sse(event: str, data: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(to_jsonable(data), ensure_ascii=False)}\n\n"


__all__ = ["format_sse"]
