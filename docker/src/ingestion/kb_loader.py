import logging
from langchain_community.vectorstores import FAISS
from pathlib import Path

from src.ingestion.embedding_utils import get_openrouter_embeddings

logger = logging.getLogger("app.ingestion.kb_loader")

def _ensure_vector_store_exists(db_path: str) -> None:
    if not db_path:
        raise ValueError("Vector store path is empty.")

    store_dir = Path(db_path)
    if not store_dir.exists() or not store_dir.is_dir():
        raise FileNotFoundError(f"Vector store directory not found: {db_path}")

    index_file = store_dir / "index.faiss"
    store_file = store_dir / "index.pkl"
    if not index_file.exists() or not store_file.exists():
        raise FileNotFoundError(
            f"Vector store files missing under: {db_path}. "
            "Please ensure index.faiss and index.pkl have been generated."
        )


def load_knowledge_base(db_path: str) -> FAISS:
    """
    从本地加载 FAISS 向量数据库和嵌入模型。
    使用 OpenRouter (qwen/qwen3-embedding-8b) 进行 embedding。
    """
    _ensure_vector_store_exists(db_path)

    logger.info(f"Loading knowledge base from {db_path}...")
    # 必须使用与创建时相同的嵌入模型（OpenRouter qwen/qwen3-embedding-8b）
    embeddings = get_openrouter_embeddings()

    db = FAISS.load_local(db_path, embeddings, allow_dangerous_deserialization=True)

    logger.info("Knowledge base loaded successfully.")
    return db
