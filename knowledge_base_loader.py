from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from dotenv import load_dotenv
import os

load_dotenv()


openai_key = os.getenv("OPENAI_API_KEY")

def load_knowledge_base(db_path: str):
    """
    从本地加载FAISS向量数据库和嵌入模型。
    """
    if not db_path or not FAISS.exist(db_path):
        raise FileNotFoundError(f"Vector store not found at path: {db_path}")

    print("Loading knowledge base...")
    # 必须使用与创建时相同的嵌入模型
    embeddings = OpenAIEmbeddings(
        model="text-embedding-3-small",
        openai_api_key=openai_key
    )
    
    db = FAISS.load_local(db_path, embeddings, allow_dangerous_deserialization=True)
    
    print("Knowledge base loaded successfully.")
    # retriever可以用来执行相似性搜索
    return db.as_retriever()