"""
Canonical project path roots and repo-URL-to-disk-directory-name mapping.

Single source of truth for PROJECT_ROOT (the `docker/` directory — the
package root `src.*` imports resolve against) and the persistent data
directories derived from it. Previously these were computed independently
in `scripts/api.py`, `src/config.py`, `src/core/wiki_pipeline.py`, and
`scripts/setup_repository.py`, which would silently diverge if any of
those files moved.

Note: `scripts/api.py` still computes its own PROJECT_ROOT — it has to,
since that computation exists solely to bootstrap `sys.path` before
`src.*` (and therefore this module) can be imported at all.
"""
import os
from pathlib import Path

# docker/ — the directory containing `src/` and `scripts/`.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Repo root, one level above docker/ — where the persistent `data/` directory lives.
REPO_ROOT: Path = PROJECT_ROOT.parent

_DATA_ROOT_DEFAULT: Path = REPO_ROOT / "data"


def _resolve_data_root(env_var: str, default: Path) -> Path:
    """
    Resolve a persistent-data directory root from an env var.

    A relative override is anchored to REPO_ROOT rather than left to resolve
    against the caller's current working directory, which otherwise differs
    by entrypoint (api server vs. a script run directly).
    """
    raw = os.getenv(env_var)
    if not raw:
        return default
    resolved = Path(raw).expanduser()
    if not resolved.is_absolute():
        resolved = (REPO_ROOT / resolved).resolve()
    return resolved


VECTOR_STORE_ROOT: Path = _resolve_data_root("VECTOR_STORE_PATH", _DATA_ROOT_DEFAULT / "vector_stores")
REPO_STORE_ROOT: Path = _resolve_data_root("REPO_STORE_PATH", _DATA_ROOT_DEFAULT / "repos")


def repo_disk_dirname(repo_url: str) -> str:
    """Directory name for a repo on disk: the URL's last path segment, `.git` suffix stripped."""
    clean_url = repo_url.rstrip("/").replace(".git", "")
    return clean_url.split("/")[-1] if "/" in clean_url else clean_url
