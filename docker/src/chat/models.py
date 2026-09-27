"""Pydantic request/response models for the unified chat endpoint.

Replaces the manual `await request.json()` + `.get(...)` + hand-rolled 400
parsing that both `/chat` and `/agent/chat` did independently.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatTurnRequest(BaseModel):
    question: str
    repo_url: str
    user_id: str
    chat_id: str | None = None
    current_page_context: str | None = None


class ToolTrajectoryStep(BaseModel):
    tool: str
    arguments: dict
    status: str = Field(description="'success' or 'error'")
    summary: str
    duration_ms: int | None = None


class ChatTurnResponse(BaseModel):
    chat_id: str
    repo_url: str
    answer: str
    sources: list[str] = Field(default_factory=list)
    tool_trajectory: list[ToolTrajectoryStep] = Field(default_factory=list)
    iterations: int = 0


__all__ = ["ChatTurnRequest", "ToolTrajectoryStep", "ChatTurnResponse"]
