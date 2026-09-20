"""
Pydantic request/response models for the unified chat endpoint.

Replaces the manual `await request.json()` + `.get(...)` + hand-rolled 400
parsing that both `/chat` and `/agent/chat` did independently.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ChatTurnRequest(BaseModel):
    question: str
    repo_url: str
    user_id: str
    chat_id: Optional[str] = None
    current_page_context: Optional[str] = None


class ToolTrajectoryStep(BaseModel):
    tool: str
    arguments: dict
    status: str = Field(description="'success' or 'error'")
    summary: str
    duration_ms: Optional[int] = None


class ChatTurnResponse(BaseModel):
    chat_id: str
    repo_url: str
    answer: str
    sources: List[str] = Field(default_factory=list)
    tool_trajectory: List[ToolTrajectoryStep] = Field(default_factory=list)
    iterations: int = 0


__all__ = ["ChatTurnRequest", "ToolTrajectoryStep", "ChatTurnResponse"]
