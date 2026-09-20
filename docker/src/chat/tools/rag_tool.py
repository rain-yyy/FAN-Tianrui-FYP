"""
RAG semantic search tool: hybrid (dense + sparse) retrieval over the repo's
Qdrant index, with optional HyDE query expansion. Wraps `core/retrieval.py`'s
fusion primitives — this module owns only the tool-facing shape.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from langchain_core.tools import StructuredTool

from src.clients import StrOutputParser, get_llm
from src.config import (
    get_category_top_k,
    get_hybrid_dense_weight,
    get_hybrid_sparse_weight,
    get_max_total_candidates,
    get_mmr_lambda,
    get_retrieval_k_multipliers,
    should_use_hyde,
)
from src.core.retrieval import RankedCandidate, mmr_select, normalize_scores, qdrant_search_category
from src.ingestion.vector_store import get_qdrant_client
from src.prompts import HYDE_PROMPT
from src.chat.tools.schemas import RagSearchArgs
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.tools.rag")


class RAGSearchEngine:
    """Hybrid dense+sparse Qdrant search, with HyDE expansion on 'text' category queries."""

    HYDE_MIN_MEANINGFUL_WORDS = 3

    def __init__(self, vector_store_path: str):
        self.vector_store_path = vector_store_path
        self.repo_id = Path(vector_store_path).name if vector_store_path else ""
        self._client = None

    def _ensure_loaded(self) -> None:
        if self._client is None:
            self._client = get_qdrant_client()

    def _should_use_hyde(self, query: str) -> bool:
        if not should_use_hyde():
            return False
        stripped = query.strip()
        meaningful_words = [w for w in stripped.split() if len(w) > 2]
        if len(meaningful_words) < self.HYDE_MIN_MEANINGFUL_WORDS:
            return False
        simple_patterns = ("what is", "where is", "how to", "list all", "show me")
        return not stripped.lower().startswith(simple_patterns)

    def _generate_hyde_document(self, question: str) -> str:
        try:
            chain = HYDE_PROMPT.build() | get_llm("hyde_generation", temperature=0.3, max_tokens=400) | StrOutputParser()
            hyde_doc = chain.invoke({"question": question})
            return hyde_doc.strip() if isinstance(hyde_doc, str) else str(hyde_doc).strip()
        except Exception as e:
            logger.error("[HyDE] Failed to generate: %s", e)
            return question

    def search(self, query: str, top_k: int = 20) -> Tuple[str, Dict[str, Any]]:
        """Returns (content_for_model, artifact_for_service_layer)."""
        self._ensure_loaded()

        if not self.repo_id:
            return "No vector store path configured for this repository.", {"error": "no_stores"}

        candidates = self._gather_hybrid_candidates(query, category_top_k=get_category_top_k())
        if not candidates:
            return "No relevant documents found for the query.", {"query": query, "results": []}

        mmr_pick = mmr_select(
            candidates,
            query,
            top_n=min(top_k, len(candidates)),
            lambda_mult=get_mmr_lambda(),
        )
        chosen = mmr_pick or candidates[: min(top_k, len(candidates))]

        result_lines = [f"Found {len(chosen)} relevant document(s):\n"]
        results: List[Dict[str, Any]] = []
        for idx, cand in enumerate(chosen, 1):
            doc = cand.doc
            source = doc.metadata.get("source") or doc.metadata.get("file_path") or doc.metadata.get("id") or f"doc_{idx}"
            category = doc.metadata.get("kb_category", "")

            result_lines.append(f"[{idx}] Source: {source} | Type: {category}")
            result_lines.append("-" * 40)
            content = doc.page_content.strip()
            if len(content) > 2200:
                content = content[:2200] + "..."
            result_lines.append(content)
            result_lines.append("")

            results.append({
                "source": f"{category}:{source}" if category else source,
                "category": category,
                "score": cand.final_score,
            })

        artifact = {
            "query": query,
            "num_results": len(chosen),
            "top_score": candidates[0].final_score if candidates else 0.0,
            "results": results,
        }
        return "\n".join(result_lines), artifact

    def _gather_hybrid_candidates(self, question: str, category_top_k: Mapping[str, int]) -> List[RankedCandidate]:
        final_candidates: List[RankedCandidate] = []
        dense_multiplier, sparse_multiplier, max_method_k = get_retrieval_k_multipliers()

        def _fetch_category(category: str) -> List[RankedCandidate]:
            base_k = max(int(category_top_k.get(category, 3)), 1)
            dense_k = min(base_k * dense_multiplier, max_method_k)
            sparse_k = min(base_k * sparse_multiplier, max_method_k)

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
                dense_weight=get_hybrid_dense_weight(),
                sparse_weight=get_hybrid_sparse_weight(),
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

        normalized = normalize_scores([c.final_score for c in final_candidates])
        for cand, score in zip(final_candidates, normalized):
            cand.final_score = score

        final_candidates.sort(key=lambda c: c.final_score, reverse=True)
        return final_candidates[: get_max_total_candidates()]


def build_rag_search_tool(vector_store_path: str) -> StructuredTool:
    engine = RAGSearchEngine(vector_store_path)

    def _run(query: str, top_k: int = 20) -> Tuple[str, Dict[str, Any]]:
        try:
            return engine.search(query, top_k)
        except Exception as e:
            logger.exception("RAG search failed")
            return f"Search failed: {e}", {"error": str(e)}

    async def _arun(query: str, top_k: int = 20) -> Tuple[str, Dict[str, Any]]:
        return await run_sync(_run, query, top_k)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="rag_search",
        description=(
            "Semantic search over this repository's indexed code and documentation. "
            "Best for concept/documentation questions and finding relevant context by meaning."
        ),
        args_schema=RagSearchArgs,
        response_format="content_and_artifact",
    )
