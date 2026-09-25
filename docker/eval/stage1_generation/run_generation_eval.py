"""Stage 1: drive `/generate` for each candidate repo, poll it to completion, and
record wall-clock time (total + per-stage, from `current_step` transitions),
LOC, and $ cost (via `common.llm_usage_tracker`).

Runs the FastAPI app in-process (see `common/api_client.py` for why) so cost
instrumentation actually captures the real LLM/embedding calls -- there is no
need for a separately-running `python scripts/api.py`; this script is its own
process talking to the same `app` object the server would run.

Usage (from `docker/eval/`):
    python -m stage1_generation.run_generation_eval --pilot 3
    python -m stage1_generation.run_generation_eval               # full repos.json run
    python -m stage1_generation.run_generation_eval --repo-url https://github.com/foo/bar

Requires EVAL_USER_ID (or TEST_USER_ID) set to a real profiles/auth.users id
(tasks.user_id has a foreign-key constraint).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

_EVAL_ROOT = Path(__file__).resolve().parents[1]
if str(_EVAL_ROOT) not in sys.path:
    sys.path.insert(0, str(_EVAL_ROOT))

from common import (
    bootstrap,  # noqa: E402,F401
    manifest,  # noqa: E402
)
from common import llm_usage_tracker as tracker  # noqa: E402
from common.api_client import (  # noqa: E402
    GenerationTimeout,
    poll_task_to_completion,
    trigger_generate,
)
from common.config import (  # noqa: E402
    DEFAULT_MAX_COST_USD,
    GENERATION_RESULTS_PATH,
    REPOS_CONFIG_PATH,
    require_eval_user_id,
)
from common.loc_counter import count_ingested_loc  # noqa: E402

tracker.install()

from src.paths import REPO_STORE_ROOT, repo_disk_dirname  # noqa: E402
from src.storage.supabase_client import get_supabase_client  # noqa: E402


def _load_candidate_repos() -> list[dict[str, str]]:
    data = json.loads(REPOS_CONFIG_PATH.read_text(encoding="utf-8"))
    return data["repos"]


def _already_ingested(supabase, repo_url: str) -> bool:
    """Isolation rule: never eval-ingest a repo a real user already has."""
    info = supabase.get_repo_information(repo_url)
    return bool(info and info.get("vector_store_path"))


def _stage_timings(snapshots) -> dict[str, float]:
    """First-seen timestamp (seconds since poll start) per distinct `current_step` label."""
    seen: dict[str, float] = {}
    for snap in snapshots:
        if snap.current_step and snap.current_step not in seen:
            seen[snap.current_step] = snap.ts
    return seen


async def run_one(repo_url: str, tier_hint: str, user_id: str) -> dict[str, Any]:
    print(f"\n=== {repo_url} (tier={tier_hint}) ===")
    tracker.reset()
    t0 = time.monotonic()

    task_id = await trigger_generate(repo_url, user_id)
    manifest.add_task_id(repo_url, task_id)
    repo_id = repo_disk_dirname(repo_url)
    manifest.upsert_repo(repo_url, repo_id, tier_hint)

    try:
        outcome = await poll_task_to_completion(task_id)
    except GenerationTimeout as exc:
        manifest.set_generation_status(repo_url, "timeout")
        return {
            "repo_url": repo_url,
            "repo_id": repo_id,
            "tier": tier_hint,
            "status": "timeout",
            "error": str(exc),
        }

    wall_clock_sec = time.monotonic() - t0

    if outcome.cache_hit:
        print(
            f"  ! cache hit on first poll -- excluding from timing/cost stats: {repo_url}"
        )
        manifest.set_generation_status(repo_url, "excluded_cache_hit")
        return {
            "repo_url": repo_url,
            "repo_id": repo_id,
            "tier": tier_hint,
            "status": "excluded_cache_hit",
            "task_id": task_id,
        }

    if outcome.final_status != "completed":
        manifest.set_generation_status(repo_url, "failed")
        return {
            "repo_url": repo_url,
            "repo_id": repo_id,
            "tier": tier_hint,
            "status": "failed",
            "task_id": task_id,
            "error": outcome.error,
        }

    manifest.set_generation_status(repo_url, "completed")

    repo_checkout = REPO_STORE_ROOT / repo_id
    loc = count_ingested_loc(str(repo_checkout)) if repo_checkout.is_dir() else None

    records = tracker.get_records()
    total_cost = tracker.total_cost_usd()
    cost_by_model = tracker.cost_breakdown_by_model()

    row = {
        "repo_url": repo_url,
        "repo_id": repo_id,
        "tier": tier_hint,
        "status": "completed",
        "task_id": task_id,
        "loc": loc,
        "wall_clock_sec": round(wall_clock_sec, 1),
        "stage_timings_sec": _stage_timings(outcome.snapshots),
        "total_cost_usd": round(total_cost, 4),
        "cost_by_model_usd": {k: round(v, 4) for k, v in cost_by_model.items()},
        "num_llm_calls": len(records),
        "vector_store_path": outcome.result.get("vector_store_path"),
    }
    print(f"  done: loc={loc} time={wall_clock_sec:.0f}s cost=${total_cost:.4f}")
    return row


async def main_async(args: argparse.Namespace) -> None:
    user_id = require_eval_user_id()
    supabase = get_supabase_client()

    if args.repo_url:
        candidates = [{"repo_url": u, "tier_hint": "manual"} for u in args.repo_url]
    else:
        candidates = _load_candidate_repos()

    to_run = []
    for c in candidates:
        if _already_ingested(supabase, c["repo_url"]):
            print(f"[skip] already ingested (isolation rule): {c['repo_url']}")
            continue
        to_run.append(c)
        if args.pilot and len(to_run) >= args.pilot:
            break

    print(
        f"Running {len(to_run)} repo(s) sequentially, budget ceiling ${args.max_cost_usd:.2f}"
    )

    cumulative_cost = 0.0
    with GENERATION_RESULTS_PATH.open("a", encoding="utf-8") as out:
        for c in to_run:
            if cumulative_cost >= args.max_cost_usd:
                print(
                    f"\n[stop] cumulative cost ${cumulative_cost:.2f} reached ceiling "
                    f"${args.max_cost_usd:.2f}; remaining repos left unrun."
                )
                break
            row = await run_one(c["repo_url"], c.get("tier_hint", "unknown"), user_id)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            cumulative_cost += row.get("total_cost_usd") or 0.0

    print(f"\nTotal tracked cost this run: ${cumulative_cost:.4f}")
    print(f"Results appended to {GENERATION_RESULTS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pilot",
        type=int,
        default=None,
        help="Only run the first N not-yet-ingested repos",
    )
    parser.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    parser.add_argument(
        "--repo-url",
        action="append",
        default=None,
        help="Override repos.json with specific URL(s)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
