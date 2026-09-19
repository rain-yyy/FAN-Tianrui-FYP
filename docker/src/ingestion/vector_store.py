import logging
import math
import os
import time
import uuid
from typing import Dict, List, Optional, Tuple

from fastembed import SparseTextEmbedding
from langchain_core.documents import Document
from qdrant_client import QdrantClient, models

from src.config import get_ingestion_config
from src.ingestion.embedding_utils import get_openrouter_embeddings
from src.utils.doc_identity import compute_doc_key, payload_to_document

logger = logging.getLogger("app.ingestion.vector_store")

# 外层批次大小（每批送入 Qdrant），降低以减轻 OpenRouter 压力
EMBEDDING_BATCH_SIZE = get_ingestion_config().get("vector_store_batch_size", 50)

# 两次外层批次之间的等待时间（秒），避免瞬间并发过高
INTER_BATCH_SLEEP_SEC = get_ingestion_config().get("vector_store_inter_batch_sleep_sec", 2.0)

COLLECTIONS: Dict[str, str] = {
    "code": "code_chunks",
    "text": "text_chunks",
}

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "bm25"
REPO_ID_PAYLOAD_FIELD = "repo_id"

# point ID 命名空间：uuid5(NAMESPACE, doc_key) 保证同一 chunk 幂等
_POINT_ID_NAMESPACE = uuid.UUID("6f7d1a2e-6c2b-4a3d-9f1e-2b8c5d4a7e10")

_qdrant_client: Optional[QdrantClient] = None
_sparse_model: Optional[SparseTextEmbedding] = None


def get_qdrant_client() -> QdrantClient:
    """模块级单例 QdrantClient，读取 QDRANT_URL/QDRANT_API_KEY。"""
    global _qdrant_client
    if _qdrant_client is None:
        url = os.getenv("QDRANT_URL", "").strip()
        api_key = os.getenv("QDRANT_API_KEY", "").strip()
        if not url:
            raise ValueError("QDRANT_URL environment variable not set.")
        _qdrant_client = QdrantClient(url=url, api_key=api_key or None)
    return _qdrant_client


def get_sparse_model() -> SparseTextEmbedding:
    global _sparse_model
    if _sparse_model is None:
        _sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    return _sparse_model


def compute_point_id(doc_key: str) -> str:
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, doc_key))


def repo_filter(repo_id: str) -> models.Filter:
    """按 repo_id 过滤的 Qdrant Filter，写入删除、检索、scroll 共用同一构造逻辑。"""
    return models.Filter(
        must=[models.FieldCondition(key=REPO_ID_PAYLOAD_FIELD, match=models.MatchValue(value=repo_id))]
    )


def ensure_collection(category: str) -> str:
    """幂等创建 collection（dense + sparse 命名向量 + repo_id payload index）。"""
    collection_name = COLLECTIONS[category]
    client = get_qdrant_client()

    if client.collection_exists(collection_name):
        return collection_name

    dense_dim = len(get_openrouter_embeddings().embed_query("dimension probe"))
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(
                size=dense_dim,
                distance=models.Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: models.SparseVectorParams(),
        },
    )
    client.create_payload_index(
        collection_name=collection_name,
        field_name=REPO_ID_PAYLOAD_FIELD,
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    logger.info("Created Qdrant collection '%s' (dense_dim=%d)", collection_name, dense_dim)
    return collection_name


def _build_payload(doc: Document, repo_id: str, category: str) -> dict:
    metadata = doc.metadata or {}
    payload = {
        "repo_id": repo_id,
        "content": doc.page_content,
        "source": metadata.get("source") or metadata.get("file_path"),
        "chunk_type": metadata.get("chunk_type"),
    }
    if category == "code":
        payload.update({
            "start_line": metadata.get("start_line"),
            "end_line": metadata.get("end_line"),
            "node_type": metadata.get("node_type"),
            "name": metadata.get("name"),
            "chunk_part": metadata.get("chunk_part"),
        })
    else:
        payload.update({
            "section_heading": metadata.get("section_heading"),
            "heading_level": metadata.get("heading_level"),
            "breadcrumb": metadata.get("breadcrumb"),
            "chunk_index": metadata.get("chunk_index"),
        })
    return payload


def _batch_iter(items: list, batch_size: int):
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]


def upsert_vector_store(docs: list[Document], repo_id: str, category: str) -> None:
    """
    将文档块 embedding 后 upsert 到 Qdrant，替代旧的 create_and_save_vector_store。

    写入前先按 repo_id 过滤删除该 collection 里的旧点，保证语义上等价于"整份重建"。
    """
    if not docs:
        logger.warning("No documents to process. Skipping vector store upsert.")
        return

    collection_name = ensure_collection(category)
    client = get_qdrant_client()
    embeddings = get_openrouter_embeddings()
    sparse_model = get_sparse_model()

    client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(filter=repo_filter(repo_id)),
    )
    logger.info("Cleared existing points for repo_id=%s in collection=%s", repo_id, collection_name)

    total_docs = len(docs)
    total_batches = math.ceil(total_docs / EMBEDDING_BATCH_SIZE)
    logger.info(
        "Upserting vector store: total_docs=%d, batch_size=%d, total_batches=%d, collection=%s",
        total_docs, EMBEDDING_BATCH_SIZE, total_batches, collection_name,
    )

    for batch_index, batch_docs in enumerate(_batch_iter(docs, EMBEDDING_BATCH_SIZE), start=1):
        logger.info(
            ">>> Batch %d/%d | docs_in_batch=%d",
            batch_index, total_batches, len(batch_docs),
        )
        t0 = time.monotonic()
        try:
            texts = [doc.page_content for doc in batch_docs]
            dense_vectors = embeddings.embed_documents(texts)
            sparse_vectors = list(sparse_model.embed(texts))

            points = []
            for doc, dense_vec, sparse_vec in zip(batch_docs, dense_vectors, sparse_vectors):
                doc_key = compute_doc_key(doc)
                point_id = compute_point_id(doc_key)
                payload = _build_payload(doc, repo_id, category)
                points.append(
                    models.PointStruct(
                        id=point_id,
                        vector={
                            DENSE_VECTOR_NAME: dense_vec,
                            SPARSE_VECTOR_NAME: models.SparseVector(
                                indices=sparse_vec.indices.tolist(),
                                values=sparse_vec.values.tolist(),
                            ),
                        },
                        payload=payload,
                    )
                )

            client.upsert(collection_name=collection_name, points=points)
            elapsed = time.monotonic() - t0
            logger.info(
                "<<< Batch %d/%d completed in %.2fs",
                batch_index, total_batches, elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "!!! Batch %d/%d FAILED after %.2fs: %s",
                batch_index, total_batches, elapsed, e,
            )
            raise

        if batch_index < total_batches:
            logger.debug("Sleeping %.1fs before next batch...", INTER_BATCH_SLEEP_SEC)
            time.sleep(INTER_BATCH_SLEEP_SEC)

    logger.info("Vector store upsert completed for repo_id=%s, category=%s", repo_id, category)


# ---------------------------------------------------------------------------
# 检索原语：单类别、单种（dense/sparse）Qdrant 查询，返回原始 (payload, score) 列表。
# 供 retrieval.py（融合+归一化）与 chat.py（GraphRAG 稠密召回，保留原始分数）共用，
# 避免每个调用方各自重新拼 Filter/query_points。
# ---------------------------------------------------------------------------

def query_dense(
    client: QdrantClient, category: str, repo_id: str, query_text: str, k: int
) -> List[Tuple[dict, float]]:
    if k <= 0:
        return []
    collection_name = COLLECTIONS[category]
    try:
        vector = get_openrouter_embeddings().embed_query(query_text)
        result = client.query_points(
            collection_name=collection_name,
            query=vector,
            using=DENSE_VECTOR_NAME,
            query_filter=repo_filter(repo_id),
            limit=k,
            with_payload=True,
        )
        return [(point.payload, point.score) for point in result.points]
    except Exception as exc:
        logger.warning("[Qdrant] dense search failed for %s/%s: %s", collection_name, repo_id, exc)
        return []


def query_sparse(
    client: QdrantClient, category: str, repo_id: str, query_text: str, k: int
) -> List[Tuple[dict, float]]:
    if k <= 0:
        return []
    collection_name = COLLECTIONS[category]
    try:
        sparse_embedding = next(iter(get_sparse_model().embed([query_text])))
        result = client.query_points(
            collection_name=collection_name,
            query=models.SparseVector(
                indices=sparse_embedding.indices.tolist(),
                values=sparse_embedding.values.tolist(),
            ),
            using=SPARSE_VECTOR_NAME,
            query_filter=repo_filter(repo_id),
            limit=k,
            with_payload=True,
        )
        return [(point.payload, point.score) for point in result.points]
    except Exception as exc:
        logger.warning("[Qdrant] sparse search failed for %s/%s: %s", collection_name, repo_id, exc)
        return []
