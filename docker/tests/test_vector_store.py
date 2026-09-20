"""
"Upload vector knowledge base" + "retrieval" core flow, exercised directly
against real Qdrant with a couple of hand-built Documents — no repo clone,
no wiki generation, no LLM synthesis/chat. This mirrors what
run_rag_indexing() does internally for a real repo (see
test_ingestion_to_retrieval.py for the full clone-to-search path).

Each test uses a throwaway repo_id and cleans its Qdrant points up via the
`vector_repo_cleanup` fixture, so this is safe to run against the real
collections repeatedly.
"""
import uuid

from langchain_core.documents import Document

from src.core.retrieval import qdrant_search_category
from src.ingestion.vector_store import COLLECTIONS, get_qdrant_client, repo_filter, upsert_vector_store


def _make_docs(texts: list[str], source_prefix: str) -> list[Document]:
    return [
        Document(
            page_content=text,
            metadata={
                "source": f"{source_prefix}/file_{i}.py",
                "chunk_type": "code",
                "start_line": 1,
                "end_line": 10,
            },
        )
        for i, text in enumerate(texts)
    ]


def test_upsert_and_search_round_trip(vector_repo_cleanup):
    repo_id = f"pytest-vec-{uuid.uuid4()}"
    vector_repo_cleanup.append(repo_id)

    docs = _make_docs(
        [
            "def calculate_shipping_cost(order): return order.weight * 4.5",
            "class InventoryManager: tracks warehouse stock levels across regions",
        ],
        source_prefix=repo_id,
    )
    upsert_vector_store(docs, repo_id=repo_id, category="code")

    results = qdrant_search_category(
        get_qdrant_client(), category="code", repo_id=repo_id,
        query="shipping cost calculation", dense_k=5, sparse_k=5,
    )

    assert results
    assert any("shipping" in c.doc.page_content.lower() for c in results)
    assert all(c.doc.metadata.get("repo_id") == repo_id for c in results)


def test_upsert_is_idempotent_per_repo_id(vector_repo_cleanup):
    """upsert_vector_store replaces a repo's points wholesale rather than
    accumulating duplicates across repeated indexing runs."""
    repo_id = f"pytest-vec-{uuid.uuid4()}"
    vector_repo_cleanup.append(repo_id)

    docs_v1 = _make_docs(["version one content about billing"], source_prefix=repo_id)
    docs_v2 = _make_docs(
        ["version two content about billing", "second chunk about invoices"],
        source_prefix=repo_id,
    )

    upsert_vector_store(docs_v1, repo_id=repo_id, category="text")
    upsert_vector_store(docs_v2, repo_id=repo_id, category="text")

    client = get_qdrant_client()
    count = client.count(
        collection_name=COLLECTIONS["text"],
        count_filter=repo_filter(repo_id),
        exact=True,
    ).count
    assert count == len(docs_v2)


def test_search_is_isolated_by_repo_id(vector_repo_cleanup):
    repo_a = f"pytest-vec-{uuid.uuid4()}"
    repo_b = f"pytest-vec-{uuid.uuid4()}"
    vector_repo_cleanup.extend([repo_a, repo_b])

    shared_query_text = "unique marker zzzqux alpha nonsense token"
    upsert_vector_store(_make_docs([shared_query_text], source_prefix=repo_a), repo_id=repo_a, category="code")
    upsert_vector_store(
        _make_docs(["completely unrelated content about gardening tools"], source_prefix=repo_b),
        repo_id=repo_b,
        category="code",
    )

    results = qdrant_search_category(
        get_qdrant_client(), category="code", repo_id=repo_a,
        query=shared_query_text, dense_k=5, sparse_k=5,
    )

    assert results
    assert all(c.doc.metadata.get("repo_id") == repo_a for c in results)


def test_upsert_empty_docs_is_a_noop(vector_repo_cleanup):
    repo_id = f"pytest-vec-{uuid.uuid4()}"
    vector_repo_cleanup.append(repo_id)

    upsert_vector_store([], repo_id=repo_id, category="code")

    results = qdrant_search_category(
        get_qdrant_client(), category="code", repo_id=repo_id,
        query="anything", dense_k=5, sparse_k=5,
    )
    assert results == []
