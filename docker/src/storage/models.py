"""Typed row models for SupabaseClient reads.

Keeps table-shape quirks (e.g. `tasks.progress` being a text column, or
`tasks.result` sometimes coming back as a JSON string) as coercions in one
place instead of ad hoc `.get(...)` parsing at every call site.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class TaskRecord(BaseModel):
    """A row from the `tasks` table."""

    model_config = ConfigDict(extra="ignore")

    id: UUID
    user_id: UUID
    task_id: str
    repo_url: str
    status: str = "pending"
    created_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    progress: float = 0.0
    current_step: Optional[str] = None
    result: dict = {}
    error: Optional[str] = None

    @field_validator("progress", mode="before")
    @classmethod
    def _coerce_progress(cls, v: Any) -> float:
        # `progress` is a `text` column in Supabase (it's always written as a
        # number, but the DB stores/returns it as a string), so reads need coercion.
        if v is None or v == "":
            return 0.0
        return float(v)

    @field_validator("result", mode="before")
    @classmethod
    def _coerce_result(cls, v: Any) -> dict:
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except (TypeError, ValueError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
