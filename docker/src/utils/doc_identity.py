"""
`ingestion` 与 `core` 两层共用的文档标识/还原工具。

独立存放以打破一个真实的循环依赖：ingestion/vector_store.py 需要 compute_doc_key
生成去重键，core/retrieval.py 需要 payload_to_document 把 Qdrant payload 还原成
Document —— 两边互相需要对方模块里的符号。此前双方都用函数内延迟 import 掩盖了这一点，
而不是让顶层导入直接失败。抽到这个不依赖 ingestion 或 core 任何内容的叶子模块后，
两边都可以在模块顶层正常导入。
"""
import hashlib
from typing import Optional

from langchain_core.documents import Document


def compute_doc_key(doc: Document) -> str:
    """通过来源+内容哈希为每个文档生成稳定的唯一 key，方便跨检索结果融合与去重。"""
    meta = doc.metadata or {}
    source = meta.get("source") or meta.get("file_path") or "unknown"
    anchor = meta.get("chunk_id") or meta.get("line_start") or meta.get("page") or ""
    digest = hashlib.md5(doc.page_content.encode("utf-8")).hexdigest()[:12]
    return f"{source}|{anchor}|{digest}"


def payload_to_document(payload: Optional[dict], category: str) -> Document:
    """将 Qdrant point payload 还原为 Document，供检索侧（retrieval.py/chat.py）共用。"""
    payload = payload or {}
    content = payload.get("content", "")
    metadata = {k: v for k, v in payload.items() if k != "content" and v is not None}
    metadata["kb_category"] = category
    return Document(page_content=content, metadata=metadata)


__all__ = ["compute_doc_key", "payload_to_document"]
