"""Shared eval-run configuration: the fixed eval identity, budget ceiling, API
base URL, and filesystem locations for manifests/results.

Import `common.bootstrap` before this module in every entry-point script so
`.env`/`.env.local` are already loaded when `os.getenv` runs below.
"""

import os

from .bootstrap import EVAL_ROOT

# `tasks.user_id` has a foreign-key constraint to profiles/auth.users (see
# docker/tests/conftest.py's `unique_user_id` fixture) -- an arbitrary UUID
# will fail task creation, so this must be a real account id.
EVAL_USER_ID = os.getenv("EVAL_USER_ID") or os.getenv("TEST_USER_ID")


def require_eval_user_id() -> str:
    if not EVAL_USER_ID:
        raise RuntimeError(
            "Set EVAL_USER_ID (or TEST_USER_ID) to a real profiles/auth.users id "
            "before running the eval pipeline -- tasks.user_id has an FK constraint, "
            "so an arbitrary UUID will fail /generate and /chat calls."
        )
    return EVAL_USER_ID


API_BASE_URL = os.getenv("EVAL_API_BASE_URL", "http://localhost:8000").rstrip("/")

# Hard spend ceiling for a single eval invocation (Stage 1 + Stage 3 are the
# stages that actually spend money). Each runner checks cumulative tracked
# cost against this before starting the next unit of work.
DEFAULT_MAX_COST_USD = float(os.getenv("EVAL_MAX_COST_USD", "18.0"))

RESULTS_DIR = EVAL_ROOT / "results"
MANIFEST_PATH = RESULTS_DIR / "eval_manifest.json"
REPOS_CONFIG_PATH = EVAL_ROOT / "repos.json"

GENERATION_RESULTS_PATH = RESULTS_DIR / "generation_results.jsonl"
QUESTION_SET_PATH = RESULTS_DIR / "question_set.jsonl"
RETRIEVAL_RESULTS_PATH = RESULTS_DIR / "retrieval_results.jsonl"
CITATION_RESULTS_PATH = RESULTS_DIR / "citation_results.jsonl"

POLL_INTERVAL_SEC = float(os.getenv("EVAL_POLL_INTERVAL_SEC", "8"))
POLL_TIMEOUT_SEC = float(os.getenv("EVAL_POLL_TIMEOUT_SEC", "3600"))

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
