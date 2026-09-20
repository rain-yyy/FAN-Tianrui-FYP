"""
Shared sync-to-async bridge.

Supabase (`supabase-py`) and the Qdrant-backed retrieval stack are all
blocking Python calls; FastAPI's route handlers are `async def`. Previously
every call site (`api/routers/chat.py`, `api/routers/agent.py`,
`core/chat.py`, ...) built its own `loop.run_in_executor(None, ...)` ad hoc.
This is the one place that offload happens for the chat module, so tool
calls dispatched by `langgraph.prebuilt.ToolNode` can run concurrently via
`asyncio.gather` instead of each reconstructing a `ThreadPoolExecutor`.
"""
from __future__ import annotations

import asyncio
import functools
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.getenv("SYNC_BRIDGE_WORKERS", "8")))


async def run_sync(func: Callable[..., T], *args, **kwargs) -> T:
    """Run a blocking callable off the event loop and await its result."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, functools.partial(func, *args, **kwargs))


__all__ = ["run_sync"]
