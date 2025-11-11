from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from dotenv import load_dotenv
import os
from pathlib import Path

load_dotenv()


openai_key = os.getenv("OPENAI_API_KEY")


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
            "Please ensure index.faiss 和 index.pkl 已生成。"
        )


def load_knowledge_base(db_path: str) -> FAISS:
    """
    从本地加载FAISS向量数据库和嵌入模型。
    """
    _ensure_vector_store_exists(db_path)

    print("Loading knowledge base...")
    # 必须使用与创建时相同的嵌入模型
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=openai_key
    )
    
    db = FAISS.load_local(db_path, embeddings, allow_dangerous_deserialization=True)

    print("Knowledge base loaded successfully.")
    return db