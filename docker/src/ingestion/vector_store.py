import logging
import math
import time
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from src.ingestion.embedding_utils import get_openrouter_embeddings

logger = logging.getLogger("app.ingestion.vector_store")

# 外层 FAISS 批次大小（每批送入 FAISS），降低以减轻 OpenRouter 压力
EMBEDDING_BATCH_SIZE = 50

# 两次外层批次之间的等待时间（秒），避免瞬间并发过高
INTER_BATCH_SLEEP_SEC = 2.0


def _batch_iter(documents: list[Document], batch_size: int):
    for start in range(0, len(documents), batch_size):
        yield documents[start:start + batch_size]


def create_and_save_vector_store(docs: list[Document], db_path: str):
    """
    使用文档块创建 FAISS 向量数据库并保存到本地。
    """
    if not docs:
        logger.warning("No documents to process. Skipping vector store creation.")
        return

    logger.info("Initializing embeddings model (OpenRouter)...")
    embeddings = get_openrouter_embeddings()

    total_docs = len(docs)
    total_batches = math.ceil(total_docs / EMBEDDING_BATCH_SIZE)
    logger.info(
        "Creating vector store: total_docs=%d, outer_batch_size=%d, total_outer_batches=%d",
        total_docs, EMBEDDING_BATCH_SIZE, total_batches,
    )
    db = None

    for batch_index, batch_docs in enumerate(_batch_iter(docs, EMBEDDING_BATCH_SIZE), start=1):
        logger.info(
            ">>> Outer batch %d/%d | docs_in_batch=%d | total_embedded_so_far=%d",
            batch_index, total_batches, len(batch_docs), (batch_index - 1) * EMBEDDING_BATCH_SIZE,
        )
        t0 = time.monotonic()
        try:
            if db is None:
                db = FAISS.from_documents(batch_docs, embeddings)
            else:
                db.add_documents(batch_docs)
            elapsed = time.monotonic() - t0
            logger.info(
                "<<< Outer batch %d/%d completed in %.2fs",
                batch_index, total_batches, elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "!!! Outer batch %d/%d FAILED after %.2fs: %s",
                batch_index, total_batches, elapsed, e,
            )
            raise

        if batch_index < total_batches:
            logger.debug("Sleeping %.1fs before next outer batch...", INTER_BATCH_SLEEP_SEC)
            time.sleep(INTER_BATCH_SLEEP_SEC)

    if db is None:
        logger.warning("No documents were processed. Skipping save step.")
        return

    logger.info("Saving vector store to: %s", db_path)
    db.save_local(db_path)
    logger.info("Vector store created and saved successfully.")
