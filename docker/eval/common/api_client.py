"""Async HTTP-shaped wrappers around the FastAPI app, driven **in-process** via
`httpx.ASGITransport` (the same pattern as `docker/tests/conftest.py`'s
`api_client` fixture) rather than over a real socket to a separately-running
`python scripts/api.py`.

This matters for cost accounting: `/generate` and `/chat` do their real LLM
work in a fire-and-forget `asyncio.create_task` / inside the request handler
itself. If the eval script talked to a *separate* server process over HTTP,
`common.llm_usage_tracker`'s monkeypatches (installed in the eval process)
would never see those calls -- they'd be running in the other process's
interpreter. Driving the same `FastAPI` `app` object in-process means the
patched `src.clients`/`langchain_openrouter`/`embedding_utils` are the exact
modules the request handlers import, so tracked cost is real.

Because `/generate` schedules a background `asyncio.Task` on the running loop,
callers must run everything (`trigger_generate`, the poll loop, `/chat`) on
one `asyncio.run(...)` so the loop keeps yielding time to that task.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import POLL_INTERVAL_SEC, POLL_TIMEOUT_SEC

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Process-wide in-process ASGI client, built lazily so `common.bootstrap`
    (sys.path + .env) and `common.llm_usage_tracker.install()` can run first.
    """
    global _client
    if _client is None:
        from scripts.api import app

        transport = httpx.ASGITransport(app=app)
        _client = httpx.AsyncClient(
            transport=transport, base_url="http://eval", timeout=180
        )
    return _client


class GenerationTimeout(Exception):
    pass


@dataclass
class PollSnapshot:
    ts: float
    status: str
    progress: float
    current_step: str | None


@dataclass
class GenerationOutcome:
    task_id: str
    final_status: str
    result: dict[str, Any]
    error: str | None
    snapshots: list[PollSnapshot]
    wall_clock_sec: float
    cache_hit: bool


async def trigger_generate(repo_url: str, user_id: str) -> str:
    resp = await get_client().post(
        "/generate", json={"url_link": repo_url, "user_id": user_id}
    )
    resp.raise_for_status()
    return resp.json()["task_id"]


async def poll_task_to_completion(
    task_id: str,
    *,
    interval_sec: float = POLL_INTERVAL_SEC,
    timeout_sec: float = POLL_TIMEOUT_SEC,
) -> GenerationOutcome:
    client = get_client()
    started = time.monotonic()
    snapshots: list[PollSnapshot] = []
    while True:
        resp = await client.post(f"/task/{task_id}")
        resp.raise_for_status()
        row = resp.json()
        status = row.get("status", "unknown")
        snapshots.append(
            PollSnapshot(
                ts=time.monotonic() - started,
                status=status,
                progress=row.get("progress", 0.0),
                current_step=row.get("current_step"),
            )
        )
        if status in ("completed", "cached", "failed"):
            elapsed = time.monotonic() - started
            cache_hit = status == "cached"
            return GenerationOutcome(
                task_id=task_id,
                final_status=status,
                result=row.get("result") or {},
                error=row.get("error"),
                snapshots=snapshots,
                wall_clock_sec=elapsed,
                cache_hit=cache_hit,
            )
        if time.monotonic() - started > timeout_sec:
            raise GenerationTimeout(
                f"task {task_id} did not finish within {timeout_sec}s"
            )
        await asyncio.sleep(interval_sec)


async def delete_task(task_id: str, user_id: str) -> None:
    await get_client().delete(f"/task/{task_id}", params={"user_id": user_id})


async def chat_turn(
    question: str,
    repo_url: str,
    user_id: str,
    *,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Non-streaming `/chat` turn. Returns the raw ChatTurnResponse dict
    (chat_id, repo_url, answer, sources, tool_trajectory, iterations).
    """
    payload: dict[str, Any] = {
        "question": question,
        "repo_url": repo_url,
        "user_id": user_id,
    }
    if chat_id:
        payload["chat_id"] = chat_id
    resp = await get_client().post("/chat", json=payload)
    resp.raise_for_status()
    return resp.json()
