"""Runtime-only cost/token instrumentation for the eval harness.

`get_llm()` chains in `src/` all pipe into `StrOutputParser()`, which discards
the raw `AIMessage` (and therefore `usage_metadata`/`response_metadata["cost"]`)
before any of our code ever sees it. Rather than touch production code, this
module monkeypatches two call sites at runtime, confined entirely to the eval
process:

1. `langchain_openrouter.chat_models.ChatOpenRouter._create_chat_result` --
   every chat LLM call (wiki_structure / wiki_content / community_summary /
   hyde_generation / chat_agent / chat_session_compressor) passes through
   here; usage_metadata + response_metadata["cost"] are set on the raw
   AIMessage inside this method, before any `| StrOutputParser()` runs.
2. `src.ingestion.embedding_utils.OpenRouterEmbeddings._embeddings_create` --
   the single call site wrapping the raw `embeddings.create(...)` request;
   captures `response.usage` (embeddings never carry a `cost` field, so these
   records always fall back to the pricing-table estimate).

Call `install()` once per eval process before invoking any pipeline code.
Use `reset()` / `get_records()` / `total_cost_usd()` around each measured unit
of work (e.g. one repo's `/generate` run) to attribute cost per-repo.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .pricing import estimate_cost_usd

_lock = threading.Lock()
_records: list[dict[str, Any]] = []
_installed = False


@dataclass
class UsageRecord:
    kind: str  # "chat" | "embedding"
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float | None = None
    cost_source: str = "none"  # "openrouter" | "estimated" | "none"
    generation_id: str | None = None
    ts: float = field(default_factory=time.time)


def _record(rec: UsageRecord) -> None:
    with _lock:
        _records.append(asdict(rec))


def get_records() -> list[dict[str, Any]]:
    with _lock:
        return list(_records)


def reset() -> None:
    with _lock:
        _records.clear()


def total_cost_usd() -> float:
    return sum(r.get("cost_usd") or 0.0 for r in get_records())


def cost_breakdown_by_model() -> dict[str, float]:
    totals: dict[str, float] = {}
    for r in get_records():
        totals[r["model"]] = totals.get(r["model"], 0.0) + (r.get("cost_usd") or 0.0)
    return totals


def install() -> None:
    """Idempotent -- safe to call at the top of every entry-point script."""
    global _installed
    if _installed:
        return
    _patch_chat_result()
    _patch_embeddings()
    _installed = True


def _patch_chat_result() -> None:
    from langchain_openrouter.chat_models import ChatOpenRouter

    original = ChatOpenRouter._create_chat_result

    def patched(self, response):
        result = original(self, response)
        try:
            llm_output = result.llm_output or {}
            model_name = llm_output.get("model_name") or getattr(
                self, "model_name", "unknown"
            )
            gen_id = llm_output.get("id")
            for gen in result.generations:
                message = gen.message
                usage = getattr(message, "usage_metadata", None) or {}
                prompt_tokens = usage.get("input_tokens", 0) or 0
                completion_tokens = usage.get("output_tokens", 0) or 0
                total_tokens = (
                    usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
                )

                response_metadata = getattr(message, "response_metadata", None) or {}
                cost = response_metadata.get("cost")
                cost_source = "openrouter" if cost is not None else "none"
                if cost is None and (prompt_tokens or completion_tokens):
                    cost = estimate_cost_usd(
                        model_name, prompt_tokens, completion_tokens
                    )
                    cost_source = "estimated" if cost is not None else "none"

                _record(
                    UsageRecord(
                        kind="chat",
                        model=model_name,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        total_tokens=total_tokens,
                        cost_usd=cost,
                        cost_source=cost_source,
                        generation_id=gen_id,
                    )
                )
        except Exception:
            pass  # instrumentation must never break the pipeline it observes
        return result

    ChatOpenRouter._create_chat_result = patched


def _patch_embeddings() -> None:
    from src.ingestion.embedding_utils import OpenRouterEmbeddings

    original = OpenRouterEmbeddings._embeddings_create

    def patched(self, input_payload, batch_label):
        response = original(self, input_payload, batch_label)
        try:
            usage = getattr(response, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                total_tokens = getattr(usage, "total_tokens", prompt_tokens) or 0
                cost = estimate_cost_usd(self.model, prompt_tokens, 0)
                _record(
                    UsageRecord(
                        kind="embedding",
                        model=self.model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=0,
                        total_tokens=total_tokens,
                        cost_usd=cost,
                        cost_source="estimated" if cost is not None else "none",
                    )
                )
        except Exception:
            pass
        return response

    OpenRouterEmbeddings._embeddings_create = patched
