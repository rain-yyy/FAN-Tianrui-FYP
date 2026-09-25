"""OpenRouter per-model pricing cache, used as a fallback cost calculator for any
LLM/embedding call where OpenRouter didn't return its own `cost` field (e.g.
the embeddings endpoint, or a model/provider that doesn't support the
`usage: {"include": true}` opt-in). Prefer the real `cost` field when present
(see `llm_usage_tracker.py`); this is only the fallback estimate.
"""

from __future__ import annotations

import json
import time

import httpx

from .config import RESULTS_DIR

_CACHE_PATH = RESULTS_DIR / "openrouter_pricing_cache.json"
_CACHE_TTL_SEC = 24 * 3600
_MODELS_URL = "https://openrouter.ai/api/v1/models"

_pricing_by_model: dict[str, dict[str, float]] | None = None


def _load_cache() -> dict[str, dict[str, float]] | None:
    if not _CACHE_PATH.exists():
        return None
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if time.time() - data.get("fetched_at", 0) > _CACHE_TTL_SEC:
        return None
    pricing = data.get("pricing")
    return pricing if isinstance(pricing, dict) else None


def _fetch_and_cache() -> dict[str, dict[str, float]]:
    resp = httpx.get(_MODELS_URL, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    pricing: dict[str, dict[str, float]] = {}
    for entry in payload.get("data", []):
        model_id = entry.get("id")
        price = entry.get("pricing") or {}
        if not model_id:
            continue
        try:
            pricing[model_id] = {
                # OpenRouter publishes these as $ per single token.
                "prompt": float(price.get("prompt") or 0),
                "completion": float(price.get("completion") or 0),
            }
        except (TypeError, ValueError):
            continue
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(
        json.dumps({"fetched_at": time.time(), "pricing": pricing}), encoding="utf-8"
    )
    return pricing


def get_pricing_table() -> dict[str, dict[str, float]]:
    global _pricing_by_model
    if _pricing_by_model is not None:
        return _pricing_by_model
    cached = _load_cache()
    if cached is not None:
        _pricing_by_model = cached
        return cached
    try:
        _pricing_by_model = _fetch_and_cache()
    except Exception:
        _pricing_by_model = {}
    return _pricing_by_model


def estimate_cost_usd(
    model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    table = get_pricing_table()
    prices = table.get(model)
    if not prices:
        return None
    return prompt_tokens * prices["prompt"] + completion_tokens * prices["completion"]
