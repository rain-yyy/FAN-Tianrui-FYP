"""
RAG 语义检索工具

封装现有的 RAG 检索能力，为 Agent 提供语义搜索接口：
- 支持混合检索（密集 + 稀疏）
- 支持 HyDE 增强
- 返回结构化的上下文片段
- 使用全局缓存避免重复加载
"""

from __future__ import annotations

import os
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Any, Optional, Mapping

from langchain_core.documents import Document

from src.agent.state import ContextPiece
from src.core.retrieval import (
    RankedCandidate,
    SparseBM25Index,
    compute_doc_key,
    mmr_select,
    normalize_scores,
)
from src.core.chat import _vector_store_cache, CategoryRetrievalUnit as ChatCategoryRetrievalUnit
from src.ingestion.kb_loader import load_knowledge_base
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG
from src.prompts import HYDE_PROMPT

logger = logging.getLogger("app.agent.tools.rag")


CATEGORY_TOP_K: Dict[str, int] = {
    "code": 5,
    "text": 2,
}
HYBRID_DENSE_WEIGHT = 0.6
HYBRID_SPARSE_WEIGHT = 0.4
MMR_TARGET = 8
MMR_LAMBDA = 0.5


class CategoryRetrievalUnit:
    """类别检索单元，包含密集和稀疏检索器"""
    def __init__(self, dense_store, sparse_index):
        self.dense_store = dense_store
        self.sparse_index = sparse_index


class RAGSearchTool:
    """
    RAG 语义检索工具
    
    封装现有的 FAISS + BM25 混合检索能力，支持 HyDE 增强。
    
    性能优化：
    - HyDE 结果缓存（同一查询只生成一次）
    - 短查询（<30字符）跳过 HyDE
    - 支持轻量模式（禁用 HyDE）
    """
    
    HYDE_CACHE_SIZE = 32
    HYDE_MIN_QUERY_LENGTH = 30
    
    def __init__(self, vector_store_path: str, use_hyde: bool = True):
        """
        初始化 RAG 检索工具
        
        Args:
            vector_store_path: 向量库根目录路径
            use_hyde: 是否启用 HyDE 增强
        """
        self.vector_store_path = vector_store_path
        self.use_hyde = use_hyde
        self.stores: Dict[str, CategoryRetrievalUnit] = {}
        self._loaded = False
        self._hyde_cache: Dict[str, str] = {}
    
    def _ensure_loaded(self) -> None:
        """确保向量库已加载，优先使用全局缓存"""
        if self._loaded:
            return
        
        root_path = self._resolve_vector_store_root(self.vector_store_path)
        
        # 尝试从全局缓存获取
        cached_stores = _vector_store_cache.get(root_path)
        if cached_stores is not None:
            # 转换缓存格式（ChatCategoryRetrievalUnit -> CategoryRetrievalUnit）
            for category, chat_unit in cached_stores.items():
                self.stores[category] = CategoryRetrievalUnit(
                    dense_store=chat_unit.dense_store,
                    sparse_index=chat_unit.sparse_index,
                )
            logger.info(f"[RAGTool] Using cached vector stores for {root_path}")
            self._loaded = True
            return
        
        # 缓存未命中，执行加载
        logger.info(f"[RAGTool] Loading vector stores from {root_path} (cache miss)")
        for category in CATEGORY_TOP_K.keys():
            category_path = self._resolve_category_path(root_path, category)
            if category_path:
                try:
                    dense_store = load_knowledge_base(category_path)
                    docs = self._extract_store_documents(dense_store)
                    sparse_index = SparseBM25Index.build(docs) if docs else None
                    self.stores[category] = CategoryRetrievalUnit(
                        dense_store=dense_store,
                        sparse_index=sparse_index,
                    )
                    logger.info(f"Loaded vector store for category '{category}'")
                except Exception as e:
                    logger.warning(f"Failed to load category '{category}': {e}")
        
        # 存入全局缓存
        if self.stores:
            chat_stores = {
                cat: ChatCategoryRetrievalUnit(
                    dense_store=unit.dense_store,
                    sparse_index=unit.sparse_index,
                )
                for cat, unit in self.stores.items()
            }
            _vector_store_cache.put(root_path, chat_stores)
        
        self._loaded = True
    
    def _resolve_vector_store_root(self, base_path: str) -> str:
        """解析向量库根目录"""
        if not base_path:
            raise ValueError("Vector store path is required")
        candidate = os.path.abspath(base_path)
        if not os.path.exists(candidate):
            raise FileNotFoundError(f"Vector store path not found: {candidate}")
        return candidate
    
    def _resolve_category_path(self, root_path: str, category: str) -> Optional[str]:
        """解析类别目录路径"""
        candidate = os.path.join(root_path, category)
        index_file = os.path.join(candidate, "index.faiss")
        if os.path.exists(index_file):
            return candidate
        
        root_index = os.path.join(root_path, "index.faiss")
        if os.path.exists(root_index) and os.path.basename(root_path).lower() == category:
            return root_path
        
        return None
    
    def _extract_store_documents(self, store) -> List[Document]:
        """从 FAISS 向量库提取文档"""
        docstore = getattr(store, "docstore", None)
        if docstore is None:
            return []
        
        if hasattr(docstore, "_dict"):
            raw_docs = docstore._dict.values()
        else:
            return []
        
        extracted = []
        for doc in raw_docs:
            if isinstance(doc, Document):
                extracted.append(Document(
                    page_content=doc.page_content, 
                    metadata=dict(doc.metadata)
                ))
        return extracted
    
    def _should_use_hyde(self, query: str) -> bool:
        """判断是否应该使用 HyDE（性能优化）"""
        if len(query.strip()) < self.HYDE_MIN_QUERY_LENGTH:
            return False
        simple_patterns = ["what is", "where is", "how to", "list all", "show me"]
        query_lower = query.lower()
        for pattern in simple_patterns:
            if query_lower.startswith(pattern):
                return False
        return True
    
    def _generate_hyde_document(self, question: str) -> str:
        """使用 HyDE 生成假设性文档（带缓存）"""
        cache_key = question[:200]
        if cache_key in self._hyde_cache:
            logger.info(f"[HyDE] Cache hit for query: {question[:50]}...")
            return self._hyde_cache[cache_key]
        
        try:
            provider, model = get_model_config(CONFIG, "hyde_generation")
            llm = get_ai_client(provider, model=model)
            
            messages = HYDE_PROMPT.format_messages(question=question)
            hyde_doc = llm.chat(messages, temperature=0.3, max_tokens=400)
            
            if not isinstance(hyde_doc, str):
                hyde_doc = str(hyde_doc)
            
            hyde_doc = hyde_doc.strip()
            
            if len(self._hyde_cache) >= self.HYDE_CACHE_SIZE:
                oldest_key = next(iter(self._hyde_cache))
                del self._hyde_cache[oldest_key]
            self._hyde_cache[cache_key] = hyde_doc
            
            logger.info(f"[HyDE] Generated hypothetical document: {len(hyde_doc)} chars")
            return hyde_doc
            
        except Exception as e:
            logger.error(f"[HyDE] Failed to generate: {e}")
            return question
    
    def execute(
        self,
        query: str,
        top_k: int = 5,
        use_hyde: Optional[bool] = None
    ) -> ContextPiece:
        """
        执行 RAG 检索
        
        Args:
            query: 搜索查询
            top_k: 返回结果数量
            use_hyde: 是否使用 HyDE（默认使用初始化时的设置）
            
        Returns:
            ContextPiece: 包含检索结果的上下文片段
        """
        try:
            self._ensure_loaded()
            
            if not self.stores:
                return ContextPiece(
                    source="rag_search",
                    content="No vector stores available. Please ensure the repository has been indexed.",
                    relevance_score=0.0,
                    metadata={"error": "no_stores"}
                )
            
            should_use_hyde = use_hyde if use_hyde is not None else self.use_hyde
            retrieval_query = query
            
            if should_use_hyde and self._should_use_hyde(query):
                hyde_doc = self._generate_hyde_document(query)
                retrieval_query = f"{query}\n\n{hyde_doc}"
            elif should_use_hyde:
                logger.info(f"[HyDE] Skipped for short/simple query: {query[:50]}...")
            
            candidates = self._gather_hybrid_candidates(
                retrieval_query,
                category_top_k=CATEGORY_TOP_K,
            )
            
            if not candidates:
                return ContextPiece(
                    source="rag_search",
                    content="No relevant documents found for the query.",
                    relevance_score=0.0,
                    metadata={"query": query}
                )
            
            mmr_pick = mmr_select(
                candidates,
                query,
                top_n=min(top_k, len(candidates)),
                lambda_mult=MMR_LAMBDA,
            )
            chosen = mmr_pick or candidates[:min(top_k, len(candidates))]
            
            result_lines = [f"Found {len(chosen)} relevant document(s):\n"]
            sources = []
            
            for idx, cand in enumerate(chosen, 1):
                doc = cand.doc
                source = doc.metadata.get("source") or doc.metadata.get("file_path") or ""
                category = doc.metadata.get("kb_category", "")
                
                if not source:
                    source = doc.metadata.get("id") or f"doc_{idx}"

                result_lines.append(f"[{idx}] Source: {source} | Type: {category}")
                result_lines.append("-" * 40)
                
                content = doc.page_content.strip()
                if len(content) > 1000:
                    content = content[:1000] + "..."
                result_lines.append(content)
                result_lines.append("")
                
                sources.append(f"{category}:{source}" if category else source)
            
            return ContextPiece(
                source="rag_search",
                content="\n".join(result_lines),
                relevance_score=candidates[0].final_score if candidates else 0.0,
                metadata={
                    "query": query,
                    "num_results": len(chosen),
                    "sources": sources,
                    "used_hyde": should_use_hyde,
                }
            )
            
        except Exception as e:
            logger.error(f"RAG search failed: {e}")
            return ContextPiece(
                source="rag_search",
                content=f"Search failed: {str(e)}",
                relevance_score=0.0,
                metadata={"error": str(e)}
            )
    
    def _gather_hybrid_candidates(
        self,
        question: str,
        category_top_k: Mapping[str, int],
    ) -> List[RankedCandidate]:
        """收集混合检索候选（跨类别并行）"""
        final_candidates: List[RankedCandidate] = []

        def _fetch_category(category, unit):
            base_k = max(int(category_top_k.get(category, 3)), 1)
            dense_k = min(base_k * 4, 20)
            sparse_k = min(base_k * 3, 15)
            return self._collect_candidates_for_category(
                category, unit, question, dense_k, sparse_k
            )

        if len(self.stores) <= 1:
            for category, unit in self.stores.items():
                final_candidates.extend(_fetch_category(category, unit))
        else:
            with ThreadPoolExecutor(max_workers=len(self.stores)) as pool:
                futures = {
                    pool.submit(_fetch_category, cat, unit): cat
                    for cat, unit in self.stores.items()
                }
                for future in as_completed(futures):
                    final_candidates.extend(future.result())
        
        if not final_candidates:
            return []
        
        normalized_final = normalize_scores([c.final_score for c in final_candidates])
        for cand, score in zip(final_candidates, normalized_final):
            cand.final_score = score
        
        final_candidates.sort(key=lambda x: x.final_score, reverse=True)
        return final_candidates[:30]
    
    def _collect_candidates_for_category(
        self,
        category: str,
        unit: CategoryRetrievalUnit,
        question: str,
        dense_k: int,
        sparse_k: int,
    ) -> List[RankedCandidate]:
        """为指定类别收集候选（dense + sparse 并行）"""
        candidates: Dict[str, RankedCandidate] = {}

        def _dense_search():
            try:
                return unit.dense_store.similarity_search_with_relevance_scores(
                    question, k=dense_k
                )
            except Exception as e:
                logger.warning(f"Dense search failed for {category}: {e}")
                return []

        def _sparse_search():
            if unit.sparse_index is None or sparse_k <= 0:
                return []
            try:
                return unit.sparse_index.search(question, top_k=sparse_k)
            except Exception as e:
                logger.warning(f"Sparse search failed for {category}: {e}")
                return []

        # 并行执行 dense 和 sparse
        with ThreadPoolExecutor(max_workers=2) as pool:
            dense_future = pool.submit(_dense_search)
            sparse_future = pool.submit(_sparse_search)
            dense_hits = dense_future.result()
            sparse_hits = sparse_future.result()

        # 处理 dense 结果
        if dense_hits:
            dense_scores = normalize_scores([score for _, score in dense_hits])
            for idx, (doc, _) in enumerate(dense_hits):
                norm_score = dense_scores[idx] if idx < len(dense_scores) else 0.0
                enriched_doc = self._attach_category(doc, category)
                key = f"{category}|{compute_doc_key(enriched_doc)}"
                if key not in candidates:
                    candidates[key] = RankedCandidate(key=key, category=category, doc=enriched_doc)
                candidates[key].dense_score = max(candidates[key].dense_score, norm_score)

        # 处理 sparse 结果
        if sparse_hits:
            sparse_scores = normalize_scores([score for _, score in sparse_hits])
            for idx, (doc, _) in enumerate(sparse_hits):
                norm_score = sparse_scores[idx] if idx < len(sparse_scores) else 0.0
                enriched_doc = self._attach_category(doc, category)
                key = f"{category}|{compute_doc_key(enriched_doc)}"
                if key not in candidates:
                    candidates[key] = RankedCandidate(key=key, category=category, doc=enriched_doc)
                candidates[key].sparse_score = max(candidates[key].sparse_score, norm_score)
        
        for candidate in candidates.values():
            candidate.final_score = (
                HYBRID_DENSE_WEIGHT * candidate.dense_score
                + HYBRID_SPARSE_WEIGHT * candidate.sparse_score
            )
        
        return list(candidates.values())
    
    def _attach_category(self, doc: Document, category: str) -> Document:
        """为文档附加类别信息"""
        metadata = dict(doc.metadata)
        metadata["kb_category"] = category
        return Document(page_content=doc.page_content, metadata=metadata)
    
    def is_loaded(self) -> bool:
        """检查是否已加载"""
        return self._loaded and len(self.stores) > 0
