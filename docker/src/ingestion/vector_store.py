import logging
import math
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

from src.ingestion.embedding_utils import get_openrouter_embeddings

logger = logging.getLogger("app.ingestion.vector_store")

EMBEDDING_BATCH_SIZE = 300


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

    logger.info("Initializing embeddings model (OpenRouter baai/bge-m3)...")
    embeddings = get_openrouter_embeddings()

    logger.info("Creating vector store from documents. This may take a while...")
    total_batches = math.ceil(len(docs) / EMBEDDING_BATCH_SIZE)
    db = None

    for batch_index, batch_docs in enumerate(_batch_iter(docs, EMBEDDING_BATCH_SIZE), start=1):
        logger.info(f"Embedding batch {batch_index}/{total_batches} (size={len(batch_docs)})")
        if db is None:
            db = FAISS.from_documents(batch_docs, embeddings)
        else:
            db.add_documents(batch_docs)

    if db is None:
        logger.warning("No documents were processed. Skipping save step.")
        return

    logger.info(f"Saving vector store to: {db_path}")
    db.save_local(db_path)
    logger.info("Vector store created and saved successfully.")
