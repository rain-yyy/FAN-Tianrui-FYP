"""
Full "project input -> vector knowledge base upload -> retrieval" pipeline
against a real (small) public repo: clone via setup_repository(), index via
run_rag_indexing() (the exact function POST /generate's background task
calls), then search via qdrant_search_category(). No wiki structure/content
generation, no LLM synthesis, no chat/agent — just the ingestion + retrieval
slice this test folder is scoped to.

Marked `slow`: does a real git clone, real OpenRouter embedding calls, and
real Qdrant writes. Deselected unless run explicitly:

    pytest -m slow
    TEST_REPO_URL=https://github.com/<owner>/<tiny-repo>.git pytest -m slow
"""
import shutil
from pathlib import Path

import pytest

from scripts.setup_repository import setup_repository
from src.core.retrieval import qdrant_search_category
from src.core.wiki_pipeline import run_rag_indexing
from src.ingestion.vector_store import get_qdrant_client
from src.paths import VECTOR_STORE_ROOT, repo_disk_dirname

pytestmark = pytest.mark.slow


def test_clone_index_and_search_round_trip(test_repo_url, vector_repo_cleanup, repo_row_cleanup):
    repo_id = repo_disk_dirname(test_repo_url)
    vector_repo_cleanup.append(repo_id)
    repo_row_cleanup.append(test_repo_url)

    repo_path = setup_repository(test_repo_url)
    try:
        vector_store_path = run_rag_indexing(
            repo_path=repo_path,
            repo_url=test_repo_url,
            config_path=Path("."),  # unused inside run_rag_indexing; kept only for its signature
        )
        assert Path(vector_store_path).is_dir()

        results = qdrant_search_category(
            get_qdrant_client(), category="text", repo_id=repo_id,
            query="What does this project do?", dense_k=5, sparse_k=5,
        )
        if not results:
            # fall back to the code collection in case the chosen test repo
            # has no README/docs, only source files
            results = qdrant_search_category(
                get_qdrant_client(), category="code", repo_id=repo_id,
                query="function", dense_k=5, sparse_k=5,
            )

        assert results, "expected at least one chunk to have been indexed for the test repo"
        assert all(c.doc.metadata.get("repo_id") == repo_id for c in results)
        assert all(c.doc.page_content.strip() for c in results)
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)
        shutil.rmtree(VECTOR_STORE_ROOT / repo_id, ignore_errors=True)


def test_reindexing_replaces_rather_than_duplicates(test_repo_url, vector_repo_cleanup, repo_row_cleanup):
    """Running the indexing step twice for the same repo (e.g. a second
    /generate call) must not double the point count in Qdrant."""
    from src.ingestion.vector_store import COLLECTIONS, repo_filter

    repo_id = repo_disk_dirname(test_repo_url)
    vector_repo_cleanup.append(repo_id)
    repo_row_cleanup.append(test_repo_url)

    repo_path = setup_repository(test_repo_url)
    try:
        run_rag_indexing(repo_path=repo_path, repo_url=test_repo_url, config_path=Path("."))

        client = get_qdrant_client()
        counts_after_first = {
            category: client.count(
                collection_name=name, count_filter=repo_filter(repo_id), exact=True
            ).count
            for category, name in COLLECTIONS.items()
        }

        run_rag_indexing(repo_path=repo_path, repo_url=test_repo_url, config_path=Path("."))

        counts_after_second = {
            category: client.count(
                collection_name=name, count_filter=repo_filter(repo_id), exact=True
            ).count
            for category, name in COLLECTIONS.items()
        }

        assert counts_after_second == counts_after_first
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)
        shutil.rmtree(VECTOR_STORE_ROOT / repo_id, ignore_errors=True)
