"""
Shared fixtures for the core-flow test suite: project input -> task creation,
task management (cancel/delete), vector knowledge base upload, and retrieval.

These tests run against the *real* Supabase and Qdrant instances configured
in the repo-root `.env`/`.env.local` (see CLAUDE.md) — there is no mocking
and no local stub. Every fixture that creates a Supabase row or Qdrant point
registers it for cleanup so repeated runs don't pile up junk data.

Run from `docker/` (same convention as the app itself):

    cd docker
    pip install -r requirements.txt
    pytest -m "not slow"   # fast subset: task lifecycle + synthetic vector store tests
    pytest -m slow         # real git clone -> index -> search, against TEST_REPO_URL
    pytest                 # everything

Set TEST_REPO_URL to a small public repo before running the `slow` tests;
defaults to octocat/Hello-World (tiny, public, stable) if unset.
"""
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

# docker/tests/conftest.py -> parents[0]=tests, [1]=docker, [2]=repo root
DOCKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = DOCKER_ROOT.parent

if str(DOCKER_ROOT) not in sys.path:
    sys.path.insert(0, str(DOCKER_ROOT))

# Load the real env explicitly by absolute path so tests behave the same
# regardless of the CWD pytest was invoked from (scripts/api.py's own
# load_dotenv("../../.env.local") is CWD-relative and unreliable for this).
load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")

DEFAULT_TEST_REPO_URL = "https://github.com/octocat/Hello-World.git"


@pytest.fixture(scope="session")
def test_repo_url() -> str:
    """A small public GitHub repo used by the ingestion/vector-store tests.

    Override with the TEST_REPO_URL env var to point at whatever tiny repo
    you want to use instead of the default.
    """
    return os.getenv("TEST_REPO_URL", DEFAULT_TEST_REPO_URL)


@pytest.fixture
def unique_user_id() -> str:
    """A real profiles/auth.users row id that satisfies tasks.user_id's FK
    constraint. No longer actually "unique" per call -- tasks.task_id
    (generated fresh per test, see test_task_lifecycle.py) is what keeps
    task rows from colliding across test runs; reusing one real account for
    every test is fine since a user having many tasks is normal.

    Set TEST_USER_ID to a real id from your own `profiles` table.
    """
    user_id = os.getenv("TEST_USER_ID")
    if not user_id:
        pytest.skip(
            "TEST_USER_ID not set; tasks.user_id has an FK constraint to "
            "profiles/auth.users, so task-management tests need a real "
            "user id to actually create a task row."
        )
    return user_id


@pytest.fixture(scope="session")
def supabase_client():
    from src.storage.supabase_client import get_supabase_client

    client = get_supabase_client()
    if client.client is None:
        pytest.skip("SUPABASE_URL/SUPABASE_KEY not configured; skipping tests that need Supabase")
    return client


@pytest.fixture
def task_cleanup(supabase_client):
    """Registers (task_id, user_id) pairs to delete from `tasks` after the test."""
    created = []
    yield created
    for task_id, user_id in created:
        try:
            supabase_client.delete_task(task_id, user_id)
        except Exception:
            pass


@pytest.fixture
def repo_row_cleanup(supabase_client):
    """Registers repo_urls to remove from the `repositories` table after the test."""
    repo_urls = []
    yield repo_urls
    for repo_url in repo_urls:
        try:
            normalized = supabase_client._normalize_repo_url(repo_url)
            supabase_client.client.table("repositories").delete().eq("repo_url", normalized).execute()
        except Exception:
            pass


@pytest.fixture(scope="session")
def qdrant_client():
    from src.ingestion.vector_store import get_qdrant_client

    try:
        return get_qdrant_client()
    except ValueError:
        pytest.skip("QDRANT_URL not configured; skipping tests that need Qdrant")


@pytest.fixture
def vector_repo_cleanup(qdrant_client):
    """Registers repo_ids to delete from every Qdrant collection after the test."""
    from qdrant_client import models

    from src.ingestion.vector_store import COLLECTIONS, repo_filter

    repo_ids = []
    yield repo_ids
    for repo_id in repo_ids:
        for collection_name in COLLECTIONS.values():
            try:
                if qdrant_client.collection_exists(collection_name):
                    qdrant_client.delete(
                        collection_name=collection_name,
                        points_selector=models.FilterSelector(filter=repo_filter(repo_id)),
                    )
            except Exception:
                pass


@pytest.fixture
async def api_client():
    import httpx

    from scripts.api import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
