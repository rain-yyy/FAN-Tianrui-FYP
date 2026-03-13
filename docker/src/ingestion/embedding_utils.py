"""
OpenRouter 嵌入模型工具，供 kb_loader 和 vector_store 共用。
使用 baai/bge-m3：性价比高、多语言、8192 context、1024 维。
"""
import os
import logging
from typing import List
from openai import OpenAI
from langchain_core.embeddings import Embeddings
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("app.ingestion.embedding_utils")

OPENROUTER_EMBEDDING_MODEL = "baai/bge-m3"
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"


class OpenRouterEmbeddings(Embeddings):
    """
    专为 OpenRouter 优化的 Embedding 实现。
    解决了 LangChain OpenAIEmbeddings 默认发送 Token IDs 导致 OpenRouter 报错的问题。
    """
    def __init__(self, model: str, api_key: str, base_url: str):
        self.model = model
        self.client = OpenAI(
            api_key=api_key, 
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/FAN-Tianrui-FYP",
                "X-Title": "FYP Wiki Generator"
            }
        )

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """将一批文档转换为向量。"""
        if not texts:
            return []
            
        # 过滤掉可能的空字符串，避免 API 报错
        processed_texts = [t if t.strip() else " " for t in texts]
        
        embeddings = []
        # 使用较小的 batch 确保稳定性
        batch_size = 100
        for i in range(0, len(processed_texts), batch_size):
            batch = processed_texts[i:i+batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch
                )
                if not response.data:
                    logger.error(f"OpenRouter 返回了空数据: {response}")
                    raise ValueError("No embedding data received from OpenRouter")
                    
                embeddings.extend([r.embedding for r in response.data])
            except Exception as e:
                logger.error(f"OpenRouter Embedding 批量调用失败: {e}")
                raise

        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """将单个查询转换为向量。"""
        processed_text = text if text.strip() else " "
            
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=[processed_text]
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenRouter Embedding 单次调用失败: {e}")
            raise


def get_openrouter_embeddings() -> Embeddings:
    """使用 OpenRouter (baai/bge-m3) 获取兼容的 embedding 模型实例。"""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("未检测到 OPENROUTER_API_KEY，请设置环境变量。")
    return OpenRouterEmbeddings(
        model=OPENROUTER_EMBEDDING_MODEL,
        api_key=api_key,
        base_url=OPENROUTER_API_BASE,
    )
