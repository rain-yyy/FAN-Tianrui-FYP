from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
import math
import os
from dotenv import load_dotenv

load_dotenv()

openai_key = os.getenv("OPENAI_API_KEY")

EMBEDDING_BATCH_SIZE = 300


def _batch_iter(documents: list[Document], batch_size: int):
    for start in range(0, len(documents), batch_size):
        yield documents[start:start + batch_size]


def create_and_save_vector_store(docs: list[Document], db_path: str):
    """
    使用文档块创建FAISS向量数据库并保存到本地。
    """
    if not docs:
        print("No documents to process. Skipping vector store creation.")
        return

    print("Initializing embeddings model...")

    if not openai_key:
        raise ValueError("OPENAI_API_KEY environment variable not set. Please set it before running.")

    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=openai_key
    )

    print("Creating vector store from documents. This may take a while...")
    total_batches = math.ceil(len(docs) / EMBEDDING_BATCH_SIZE)
    db = None

    for batch_index, batch_docs in enumerate(_batch_iter(docs, EMBEDDING_BATCH_SIZE), start=1):
        print(f"Embedding batch {batch_index}/{total_batches} (size={len(batch_docs)})")
        if db is None:
            db = FAISS.from_documents(batch_docs, embeddings)
        else:
            db.add_documents(batch_docs)

    if db is None:
        print("No documents were processed. Skipping save step.")
        return

    print(f"Saving vector store to: {db_path}")
    db.save_local(db_path)
    print("Vector store created and saved successfully.")
