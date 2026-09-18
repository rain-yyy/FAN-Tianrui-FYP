"""
RAG 语义检索工具

封装现有的 RAG 检索能力，为 Agent 提供语义搜索接口：
- 支持混合检索（密集 + 稀疏）
- 支持 HyDE 增强
- 返回结构化的上下文片段
- 检索后端为 Qdrant（远程托管，无需进程内缓存）
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Mapping

from src.agent.state import ContextPiece
from src.core.retrieval import (
    RankedCandidate,
    mmr_select,
    normalize_scores,
    qdrant_search_category,
)
from src.ingestion.vector_store import get_qdrant_client
from src.clients import get_llm, StrOutputParser
from src.prompts import HYDE_PROMPT

logger = logging.getLogger("app.agent.tools.rag")


CATEGORY_TOP_K: Dict[str, int] = {
    "code": 20,
    "text": 20,
}
HYBRID_DENSE_WEIGHT = 0.6
HYBRID_SPARSE_WEIGHT = 0.4
MMR_TARGET = 8
MMR_LAMBDA = 0.5


class RAGSearchTool:
    """
    RAG 语义检索工具

    封装 Qdrant 的 dense + sparse(bm25) 混合检索能力，支持 HyDE 增强。

    性能优化：
    - HyDE 结果缓存（同一查询只生成一次）
    - 短查询（<30字符）跳过 HyDE
    - 支持轻量模式（禁用 HyDE）
    """

    HYDE_MIN_QUERY_LENGTH = 30

    def __init__(self, vector_store_path: str):
        """
        初始化 RAG 检索工具

        Args:
            vector_store_path: 向量库根目录路径（其最后一段目录名即 Qdrant 的 repo_id）
        """
        self.vector_store_path = vector_store_path
        self.repo_id = Path(vector_store_path).name if vector_store_path else ""
        self._client = None

    def _ensure_loaded(self) -> None:
        """确保 Qdrant client 已就绪"""
        if self._client is None:
            self._client = get_qdrant_client()

    def _should_use_hyde(self, query: str) -> bool:
        """判断是否应该使用 HyDE（性能优化）
        
        改用词数启发式：≥ 3 个有效单词才启用 HyDE，避免按字符长度误
        跳过 "table extraction" 这类虽短但语义密集的多词查询。
        """
        stripped = query.strip()
        # 有效单词 = 长度 > 2 的词（排除冠词、介词等噪声）
        meaningful_words = [w for w in stripped.split() if len(w) > 2]
        if len(meaningful_words) < 3:
            return False
        simple_patterns = ["what is", "where is", "how to", "list all", "show me"]
        query_lower = stripped.lower()
        for pattern in simple_patterns:
            if query_lower.startswith(pattern):
                return False
        return True
    
    def _generate_hyde_document(self, question: str) -> str:
        """使用 HyDE 生成假设性文档"""
        try:
            chain = HYDE_PROMPT.build() | get_llm("hyde_generation", temperature=0.3, max_tokens=400) | StrOutputParser()
            hyde_doc = chain.invoke({"question": question})
            
            if not isinstance(hyde_doc, str):
                hyde_doc = str(hyde_doc)
            
            return hyde_doc.strip()
            
        except Exception as e:
            logger.error(f"[HyDE] Failed to generate: {e}")
            return question
    
    def execute(
        self,
        query: str,
        top_k: int = 20
    ) -> ContextPiece:
        """
        执行 RAG 检索
        
        Args:
            query: 搜索查询
            top_k: 返回结果数量
            
        Returns:
            ContextPiece: 包含检索结果的上下文片段
        """
        try:
            self._ensure_loaded()

            if not self.repo_id:
                return ContextPiece(
                    source="rag_search",
                    content="No vector store path configured. Please ensure the repository has been indexed.",
                    relevance_score=0.0,
                    metadata={"error": "no_stores"}
                )

            candidates = self._gather_hybrid_candidates(
                query,
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
                if len(content) > 2200:
                    content = content[:2200] + "..."
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
        """收集混合检索候选（跨类别并行，每个类别调用共享的 qdrant_search_category）"""
        final_candidates: List[RankedCandidate] = []

        def _fetch_category(category: str) -> List[RankedCandidate]:
            base_k = max(int(category_top_k.get(category, 3)), 1)
            dense_k = min(base_k * 4, 30)
            sparse_k = min(base_k * 3, 20)

            # HyDE 只增强 'text' 分类的 dense 查询，稀疏检索始终用原始问题
            dense_query = None
            if category == "text" and self._should_use_hyde(question):
                hyde_doc = self._generate_hyde_document(question)
                dense_query = f"{question}\n\n{hyde_doc}"
                logger.info("[HyDE] Used for 'text' category dense search")

            return qdrant_search_category(
                self._client,
                category,
                self.repo_id,
                question,
                dense_k=dense_k,
                sparse_k=sparse_k,
                dense_weight=HYBRID_DENSE_WEIGHT,
                sparse_weight=HYBRID_SPARSE_WEIGHT,
                dense_query=dense_query,
            )

        categories = list(category_top_k.keys())
        if len(categories) <= 1:
            for category in categories:
                final_candidates.extend(_fetch_category(category))
        else:
            with ThreadPoolExecutor(max_workers=len(categories)) as pool:
                futures = {pool.submit(_fetch_category, cat): cat for cat in categories}
                for future in as_completed(futures):
                    final_candidates.extend(future.result())

        if not final_candidates:
            return []

        normalized_final = normalize_scores([c.final_score for c in final_candidates])
        for cand, score in zip(final_candidates, normalized_final):
            cand.final_score = score

        final_candidates.sort(key=lambda x: x.final_score, reverse=True)
        return final_candidates[:50]

    def is_loaded(self) -> bool:
        """检查是否已加载"""
        return self._client is not None and bool(self.repo_id)
