"""Authoritative record of every repo_url / task_id / chat_id the eval run has
touched in the shared Supabase/Qdrant/disk environment. `cleanup.py` acts
*only* on entries recorded here -- never by pattern-matching -- so it can't
reach anything a real user created.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from .config import MANIFEST_PATH

_lock = threading.Lock()


@dataclass
class RepoEntry:
    repo_url: str
    repo_id: str
    tier: str | None = None
    task_ids: list[str] = field(default_factory=list)
    chat_ids: list[str] = field(default_factory=list)
    generation_status: str | None = None


def _load() -> dict[str, dict]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, dict]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_entries() -> dict[str, RepoEntry]:
    return {url: RepoEntry(**row) for url, row in _load().items()}


def upsert_repo(repo_url: str, repo_id: str, tier: str | None = None) -> None:
    with _lock:
        data = _load()
        row = data.get(
            repo_url,
            {
                "repo_url": repo_url,
                "repo_id": repo_id,
                "tier": tier,
                "task_ids": [],
                "chat_ids": [],
                "generation_status": None,
            },
        )
        row["repo_id"] = repo_id
        if tier is not None:
            row["tier"] = tier
        data[repo_url] = row
        _save(data)


def add_task_id(repo_url: str, task_id: str) -> None:
    with _lock:
        data = _load()
        row = data.setdefault(
            repo_url,
            {
                "repo_url": repo_url,
                "repo_id": "",
                "tier": None,
                "task_ids": [],
                "chat_ids": [],
                "generation_status": None,
            },
        )
        if task_id not in row["task_ids"]:
            row["task_ids"].append(task_id)
        _save(data)


def add_chat_id(repo_url: str, chat_id: str) -> None:
    with _lock:
        data = _load()
        row = data.setdefault(
            repo_url,
            {
                "repo_url": repo_url,
                "repo_id": "",
                "tier": None,
                "task_ids": [],
                "chat_ids": [],
                "generation_status": None,
            },
        )
        if chat_id not in row["chat_ids"]:
            row["chat_ids"].append(chat_id)
        _save(data)


def set_generation_status(repo_url: str, status: str) -> None:
    with _lock:
        data = _load()
        row = data.setdefault(
            repo_url,
            {
                "repo_url": repo_url,
                "repo_id": "",
                "tier": None,
                "task_ids": [],
                "chat_ids": [],
                "generation_status": None,
            },
        )
        row["generation_status"] = status
        _save(data)
