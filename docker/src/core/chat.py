import os
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, TYPE_CHECKING

from langchain_core.documents import Document
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG
from src.prompts import get_rag_chat_prompt, HYDE_PROMPT, RAG_CHAT_PROMPT, RAG_CHAT_WITH_HISTORY_PROMPT
from src.ingestion.kb_loader import load_knowledge_base
from src.core.retrieval import (
    RankedCandidate,
    SparseBM25Index,
    compute_doc_key,
    mmr_select,
    normalize_scores,
)

from langchain_community.vectorstores import FAISS

# 初始化日志
logger = logging.getLogger("app.chat")

VECTOR_STORE_PATH: str = os.getenv("VECTOR_STORE_PATH", "").strip()
CATEGORY_TOP_K: Dict[str, int] = {
    "code": 5,
    "text": 2,
}
HYBRID_DENSE_WEIGHT = 0.6
HYBRID_SPARSE_WEIGHT = 0.4
MMR_TARGET = 12
MMR_LAMBDA = 0.5
MIN_METHOD_K = 6
MAX_METHOD_K = 30
DENSE_K_MULTIPLIER = 6  # code 指令通常需要更大的候选池
SPARSE_K_MULTIPLIER = 4
MAX_TOTAL_CANDIDATES = 60
RAG_PROMPT = get_rag_chat_prompt()

# HyDE 配置
HYDE_ENABLED = True  # 是否启用 HyDE

# 缓存配置
CACHE_TTL_SECONDS = 3600  # 1 小时缓存过期
CACHE_MAX_ENTRIES = 10    # 最多缓存 10 个仓库的向量库


@dataclass
class CategoryRetrievalUnit:
    dense_store: "FAISS"
    sparse_index: SparseBM25Index | None


@dataclass
class CachedStoreEntry:
    """缓存的向量库条目"""
    stores: Dict[str, CategoryRetrievalUnit]
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    
    def is_expired(self) -> bool:
        return time.time() - self.created_at > CACHE_TTL_SECONDS
    
    def touch(self) -> None:
        self.last_accessed = time.time()


class VectorStoreCache:
    """
    进程级向量库缓存
    
    避免每次 RAG 请求都重新加载 FAISS 和重建 BM25 索引。
    使用 LRU 策略管理缓存条目。
    """
    
    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES):
        self._cache: Dict[str, CachedStoreEntry] = {}
        self._lock = threading.RLock()
        self._max_entries = max_entries
    
    def get(self, root_path: str) -> Optional[Dict[str, CategoryRetrievalUnit]]:
        """获取缓存的向量库，如果不存在或已过期则返回 None"""
        with self._lock:
            entry = self._cache.get(root_path)
            if entry is None:
                return None
            
            if entry.is_expired():
                logger.info(f"[VectorStoreCache] Cache expired for {root_path}")
                del self._cache[root_path]
                return None
            
            entry.touch()
            logger.debug(f"[VectorStoreCache] Cache hit for {root_path}")
            return entry.stores
    
    def put(self, root_path: str, stores: Dict[str, CategoryRetrievalUnit]) -> None:
        """存入向量库缓存"""
        with self._lock:
            # LRU 清理：如果超过最大条目数，移除最久未访问的
            if len(self._cache) >= self._max_entries and root_path not in self._cache:
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].last_accessed)
                logger.info(f"[VectorStoreCache] Evicting LRU entry: {oldest_key}")
                del self._cache[oldest_key]
            
            self._cache[root_path] = CachedStoreEntry(stores=stores)
            logger.info(f"[VectorStoreCache] Cached stores for {root_path}, total entries: {len(self._cache)}")
    
    def invalidate(self, root_path: str) -> None:
        """使指定路径的缓存失效"""
        with self._lock:
            if root_path in self._cache:
                del self._cache[root_path]
                logger.info(f"[VectorStoreCache] Invalidated cache for {root_path}")
    
    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._cache.clear()
            logger.info("[VectorStoreCache] Cache cleared")
    
    def stats(self) -> Dict[str, any]:
        """返回缓存统计信息"""
        with self._lock:
            return {
                "entries": len(self._cache),
                "max_entries": self._max_entries,
                "paths": list(self._cache.keys())
            }


# 全局缓存实例
_vector_store_cache = VectorStoreCache()


def _ensure_api_key() -> None:
    """检查 OpenRouter API Key 是否设置"""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable not set. Please set it before running."
        )


def _generate_hyde_document(question: str) -> str:
    """
    使用 HyDE (Hypothetical Document Embeddings) 技术生成假设性文档。
    
    HyDE 的核心思想是：先让 LLM 根据问题生成一个假设性的答案文档，
    然后用这个假设文档去进行向量检索，而不是直接用用户的问题。
    这样可以缩小问题和答案之间的语义鸿沟，提高检索效果。
    
    Args:
        question: 用户的原始问题
        
    Returns:
        假设性答案文档，用于后续检索
    """
    try:
        provider, model = get_model_config(CONFIG, "hyde_generation")
        llm = get_ai_client(provider, model=model)
        
        messages = HYDE_PROMPT.format_messages(question=question)
        hyde_doc = llm.chat(messages, temperature=0.3, max_tokens=500)
        
        if not isinstance(hyde_doc, str):
            hyde_doc = str(hyde_doc)
        
        logger.info(f"[HyDE] 生成假设文档成功，长度: {len(hyde_doc)} 字符")
        return hyde_doc.strip()
        
    except Exception as e:
        logger.error(f"[HyDE] 生成假设文档失败: {e}，回退到原始问题")
        return question


def _format_conversation_history(history: List[Dict[str, str]]) -> str:
    """
    格式化对话历史为可读文本。
    
    Args:
        history: 对话历史列表 [{"role": "user/assistant", "content": "..."}]
        
    Returns:
        格式化后的对话历史字符串
    """
    if not history:
        return ""
    
    formatted_parts = []
    for msg in history[-6:]:  # 只保留最近 6 轮对话
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            formatted_parts.append(f"User: {content}")
        elif role == "assistant":
            formatted_parts.append(f"Assistant: {content}")
    
    return "\n".join(formatted_parts)


def _resolve_vector_store_root(base_path: str) -> str:
    """
    校验并返回向量库根目录。
    """
    if not base_path:
        raise ValueError(
            "Set VECTOR_STORE_PATH to the vector store root or a specific category directory."
        )

    candidate = os.path.abspath(base_path)
    if not os.path.exists(candidate):
        raise FileNotFoundError(f"Vector store directory does not exist: {candidate}")
    return candidate


def _resolve_category_path(root_path: str, category: str) -> str:
    """
    解析并返回指定类别向量库的目录路径。
    支持标准目录结构和直接指定类别目录的情况。
    如果找不到对应的向量库文件则抛出异常。
    """
    candidate = os.path.join(root_path, category)
    index_file = os.path.join(candidate, "index.faiss")
    if os.path.exists(index_file):
        return candidate

    # 兼容直接指定到具体类别目录的情况
    root_index = os.path.join(root_path, "index.faiss")
    if os.path.exists(root_index) and os.path.basename(root_path).lower() == category:
        return root_path

    raise FileNotFoundError(
        f"No '{category}' vector store under {root_path}/{category} (missing index.faiss)."
    )


def _extract_store_documents(store: "FAISS") -> List[Document]:
    """
    从 FAISS 向量库中提取所有缓存的 Document 对象。
    这些文档用于构建稀疏检索索引（BM25）和执行 MMR（最大边际相关性）选择。
    返回文档的副本列表，包含内容和元数据。
    """
    docstore = getattr(store, "docstore", None)
    if docstore is None:
        return []

    raw_docs: Iterable[Document]
    if hasattr(docstore, "_dict"):
        raw_docs = docstore._dict.values()  # type: ignore[attr-defined]
    else:
        raw_docs = []

    extracted: List[Document] = []
    for doc in raw_docs:
        if not isinstance(doc, Document):
            continue
        extracted.append(Document(page_content=doc.page_content, metadata=dict(doc.metadata)))
    return extracted


def _load_vector_stores(
    root_path: str, categories: Iterable[str], use_cache: bool = True
) -> Dict[str, CategoryRetrievalUnit]:
    """
    加载多个类别的向量库，包括密集向量存储和稀疏检索索引。
    为每个类别创建 CategoryRetrievalUnit，包含 FAISS 向量库和 BM25 索引。
    返回类别名称到检索单元的映射字典。
    
    性能优化：使用进程级缓存避免重复加载。
    """
    # 尝试从缓存获取
    if use_cache:
        cached = _vector_store_cache.get(root_path)
        if cached is not None:
            logger.info(f"[RAG] Using cached vector stores for {root_path}")
            return cached
    
    # 缓存未命中，执行加载
    logger.info(f"[RAG] Loading vector stores from {root_path} (cache miss)")
    load_start = time.time()
    
    stores: Dict[str, CategoryRetrievalUnit] = {}
    for category in categories:
        try:
            category_path = _resolve_category_path(root_path, category)
            logger.info(f"Loading vector store for '{category}' from {category_path} ...")
            dense_store = load_knowledge_base(category_path)
            docs = _extract_store_documents(dense_store)
            sparse_index = SparseBM25Index.build(docs) if docs else None
            stores[category] = CategoryRetrievalUnit(
                dense_store=dense_store,
                sparse_index=sparse_index,
            )
        except FileNotFoundError as e:
            logger.warning(f"Category '{category}' not found: {e}")
            continue
    
    load_elapsed = time.time() - load_start
    logger.info(f"[RAG] Vector stores loaded in {load_elapsed:.2f}s, categories: {list(stores.keys())}")
    
    # 存入缓存
    if use_cache and stores:
        _vector_store_cache.put(root_path, stores)
    
    return stores


def invalidate_vector_store_cache(root_path: Optional[str] = None) -> None:
    """
    使向量库缓存失效。
    
    Args:
        root_path: 指定要失效的路径，None 表示清空所有缓存
    """
    if root_path:
        _vector_store_cache.invalidate(root_path)
    else:
        _vector_store_cache.clear()


def get_vector_store_cache_stats() -> Dict[str, any]:
    """获取向量库缓存统计信息"""
    return _vector_store_cache.stats()


def _format_documents(docs: List[Document]) -> str:
    """
    将检索到的文档列表格式化为可读的字符串格式。
    每个文档包含片段编号、类型和来源信息，用于构建 RAG 提示的上下文。
    返回格式化后的字符串，文档之间用双换行分隔。
    """
    if not docs:
        return ""

    formatted_chunks: List[str] = []
    for idx, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source") or doc.metadata.get("file_path") or ""
        category = doc.metadata.get("kb_category", "")
        header_parts = [f"[chunk {idx}]"]
        if category:
            header_parts.append(f"type: {category}")
        if source:
            header_parts.append(f"source: {source}")
        header = " | ".join(header_parts)
        formatted_chunks.append(f"{header}\n{doc.page_content.strip()}")
    return "\n\n".join(formatted_chunks)


def _attach_category(doc: Document, category: str) -> Document:
    """
    为文档对象附加类别信息到元数据中。
    返回新的 Document 对象，包含原始内容和添加了类别信息的元数据。
    """
    metadata = dict(doc.metadata)
    metadata["kb_category"] = category
    return Document(page_content=doc.page_content, metadata=metadata)


def _plan_method_k(base: int, multiplier: int) -> int:
    """
    根据基础值和倍数计算检索方法所需的 k 值。
    结果会被限制在最小值和最大值之间，确保检索数量在合理范围内。
    返回规划后的 k 值。
    """
    base = max(base, 1)
    planned = base * multiplier
    if planned < MIN_METHOD_K:
        return MIN_METHOD_K
    if planned > MAX_METHOD_K:
        return MAX_METHOD_K
    return planned


def _collect_candidates_for_category(
    category: str,
    unit: CategoryRetrievalUnit,
    question: str,
    *,
    dense_k: int,
    sparse_k: int,
) -> List[RankedCandidate]:
    """
    为指定类别收集混合检索候选文档。
    同时执行密集向量检索和稀疏 BM25 检索，合并结果并计算混合分数。
    返回去重后的候选文档列表，每个候选包含密集分数、稀疏分数和最终混合分数。
    """
    candidates: Dict[str, RankedCandidate] = {}

    dense_hits = unit.dense_store.similarity_search_with_relevance_scores(
        question, k=dense_k
    )
    dense_scores = normalize_scores([score for _, score in dense_hits])
    for idx, (doc, _) in enumerate(dense_hits):
        norm_score = dense_scores[idx] if idx < len(dense_scores) else 0.0
        enriched_doc = _attach_category(doc, category)
        key = f"{category}|{compute_doc_key(enriched_doc)}"
        current = candidates.get(key)
        if current is None:
            current = RankedCandidate(
                key=key,
                category=category,
                doc=enriched_doc,
            )
            candidates[key] = current
        current.dense_score = max(current.dense_score, norm_score)

    if unit.sparse_index is not None and sparse_k > 0:
        sparse_hits = unit.sparse_index.search(question, top_k=sparse_k)
        sparse_scores = normalize_scores([score for _, score in sparse_hits])
        for idx, (doc, _) in enumerate(sparse_hits):
            norm_score = sparse_scores[idx] if idx < len(sparse_scores) else 0.0
            enriched_doc = _attach_category(doc, category)
            key = f"{category}|{compute_doc_key(enriched_doc)}"
            current = candidates.get(key)
            if current is None:
                current = RankedCandidate(
                    key=key,
                    category=category,
                    doc=enriched_doc,
                )
                candidates[key] = current
            current.sparse_score = max(current.sparse_score, norm_score)

    for candidate in candidates.values():
        candidate.final_score = (
            HYBRID_DENSE_WEIGHT * candidate.dense_score
            + HYBRID_SPARSE_WEIGHT * candidate.sparse_score
        )

    return list(candidates.values())


def _gather_hybrid_candidates(
    stores: Mapping[str, CategoryRetrievalUnit],
    question: str,
    *,
    category_top_k: Mapping[str, int],
) -> List[RankedCandidate]:
    """
    从所有类别的向量库中收集混合检索候选文档。
    所有类别都会参与召回，随后统一归一化与排序，确保问题同时看到代码与文档视角。
    返回排序后的候选列表，数量限制在最大候选数以内。
    """
    final_candidates: List[RankedCandidate] = []

    for category, unit in stores.items():
        planned_base = max(int(category_top_k.get(category, 0)), 1)
        dense_k = _plan_method_k(planned_base, DENSE_K_MULTIPLIER)
        sparse_k = _plan_method_k(planned_base, SPARSE_K_MULTIPLIER)
        category_candidates = _collect_candidates_for_category(
            category,
            unit,
            question,
            dense_k=dense_k,
            sparse_k=sparse_k,
        )
        final_candidates.extend(category_candidates)

    if not final_candidates:
        return []

    normalized_final = normalize_scores([cand.final_score for cand in final_candidates])
    for cand, score in zip(final_candidates, normalized_final):
        cand.final_score = score

    final_candidates.sort(key=lambda item: item.final_score, reverse=True)
    return final_candidates[:MAX_TOTAL_CANDIDATES]


def answer_question(
    db_path: str,
    question: str,
    *,
    category_top_k: Mapping[str, int] | None = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    use_hyde: bool = True,
) -> Dict[str, object]:
    """
    基于本地多类别 FAISS 向量库执行检索增强问答（RAG）。
    
    支持 HyDE (Hypothetical Document Embeddings) 和多轮对话。
    加载向量库，执行混合检索，使用 MMR 选择文档，然后调用 LLM 生成答案。
    
    Args:
        db_path: 向量库根目录路径
        question: 用户问题
        category_top_k: 各类别检索的 top-k 配置
        conversation_history: 对话历史列表 [{"role": "user/assistant", "content": "..."}]
        use_hyde: 是否使用 HyDE 技术增强检索
        
    Returns:
        包含答案文本和参考来源的字典
    """
    if not question or not question.strip():
        raise ValueError("Question must not be empty.")

    _ensure_api_key()

    root_path = _resolve_vector_store_root(db_path)
    top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
    stores = _load_vector_stores(root_path, top_k_plan.keys())

    return _answer_with_stores(
        stores, 
        question, 
        category_top_k=top_k_plan,
        conversation_history=conversation_history,
        use_hyde=use_hyde and HYDE_ENABLED,
    )


def interactive_chat(
    db_path: str, *, category_top_k: Mapping[str, int] | None = None
) -> None:
    """
    启动命令行交互式对话模式，用于快速验证 RAG 问答效果。
    持续接收用户输入的问题，返回 AI 答案和参考来源，直到用户输入 exit/quit 退出。
    """
    root_path = _resolve_vector_store_root(db_path)
    top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
    stores = _load_vector_stores(root_path, top_k_plan.keys())

    print(
        "Vector store loaded. Type a question (exit/quit to stop). "
        f"Retrieval plan: {', '.join(f'{cat}:{k}' for cat, k in top_k_plan.items())}"
    )

    while True:
        try:
            question = input("Q> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nInterrupted, exiting.")
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            print("Goodbye.")
            break

        try:
            result = _answer_with_stores(
                stores, question, category_top_k=top_k_plan
            )
        except Exception as exc:
            print(f"Error: {exc}")
            continue

        print("\nAnswer:")
        print(result["answer"])
        if result["sources"]:
            print("\nSources:")
            for src in result["sources"]:
                print(f"- {src}")
        print("-" * 40)


def _answer_with_stores(
    stores: Mapping[str, CategoryRetrievalUnit],
    question: str,
    *,
    category_top_k: Mapping[str, int],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    use_hyde: bool = True,
) -> Dict[str, object]:
    """
    使用已加载的向量库回答问题。
    
    支持 HyDE 增强检索和多轮对话上下文。
    执行混合检索收集候选文档，使用 MMR 算法选择最相关且多样化的文档，
    格式化上下文后调用 LLM 生成答案，并提取参考来源信息。
    
    Args:
        stores: 类别到检索单元的映射
        question: 用户问题
        category_top_k: 各类别检索的 top-k 配置
        conversation_history: 对话历史列表
        use_hyde: 是否使用 HyDE 技术
        
    Returns:
        包含答案和来源的字典
    """
    _ensure_api_key()

    # HyDE: 生成假设性文档用于检索
    retrieval_query = question
    if use_hyde:
        logger.info("[RAG] 使用 HyDE 增强检索...")
        hyde_doc = _generate_hyde_document(question)
        # 结合原始问题和假设文档进行检索
        retrieval_query = f"{question}\n\n{hyde_doc}"

    candidates = _gather_hybrid_candidates(
        stores,
        retrieval_query,
        category_top_k=category_top_k,
    )
    if not candidates:
        docs: List[Document] = []
    else:
        mmr_pick = mmr_select(
            candidates,
            question,  # MMR 使用原始问题进行多样性选择
            top_n=min(MMR_TARGET, len(candidates)),
            lambda_mult=MMR_LAMBDA,
        )
        chosen = mmr_pick or candidates[: min(MMR_TARGET, len(candidates))]
        docs = [cand.doc for cand in chosen]

    context = _format_documents(docs)
    if not context:
        logger.warning("No relevant context retrieved. The answer may be limited.")
    
    provider, model = get_model_config(CONFIG, "rag_answer")
    llm = get_ai_client(provider, model=model)

    logger.info("Calling AI model to generate answer...")
    
    no_result_text = "No relevant results found."

    # 根据是否有对话历史选择不同的 prompt
    if conversation_history and len(conversation_history) > 0:
        history_text = _format_conversation_history(conversation_history)
        messages = RAG_CHAT_WITH_HISTORY_PROMPT.format_messages(
            context=context or no_result_text, 
            conversation_history=history_text,
            question=question,
        )
    else:
        messages = RAG_CHAT_PROMPT.format_messages(
            context=context or no_result_text, 
            question=question,
        )
    
    answer_text = llm.chat(messages, temperature=0.1)

    if not isinstance(answer_text, str):
        answer_text = str(answer_text)

    sources: List[str] = []
    seen_sources = set()  # 去重
    for doc in docs:
        source = doc.metadata.get("source") or doc.metadata.get("file_path")
        category = doc.metadata.get("kb_category")
        
        source_key = f"{category}:{source}" if category and source else (source or category)
        if source_key and source_key not in seen_sources:
            seen_sources.add(source_key)
            sources.append(source_key)

    return {
        "answer": answer_text.strip(),
        "sources": sources,
    }


from typing import Generator, Tuple


def answer_question_stream(
    db_path: str,
    question: str,
    *,
    category_top_k: Mapping[str, int] | None = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    use_hyde: bool = True,
) -> Generator[Tuple[str, Dict[str, any]], None, None]:
    """
    流式版本的 RAG 问答，逐步返回检索阶段和答案生成阶段的事件。
    
    事件类型：
    - ("retrieval_start", {"query": str}) - 开始检索
    - ("hyde_generated", {"hyde_doc": str}) - HyDE 文档已生成
    - ("retrieval_done", {"sources": List[str], "doc_count": int}) - 检索完成
    - ("answer_delta", {"delta": str}) - 答案增量
    - ("answer_done", {"answer": str, "sources": List[str]}) - 答案完成
    - ("error", {"error": str}) - 错误
    
    Yields:
        Tuple[str, Dict]: (事件类型, 事件数据)
    """
    if not question or not question.strip():
        yield ("error", {"error": "Question must not be empty."})
        return
    
    try:
        _ensure_api_key()
        
        yield ("retrieval_start", {"query": question[:100]})
        
        root_path = _resolve_vector_store_root(db_path)
        top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
        stores = _load_vector_stores(root_path, top_k_plan.keys())
        
        # HyDE 生成
        retrieval_query = question
        if use_hyde and HYDE_ENABLED:
            logger.info("[RAG Stream] 使用 HyDE 增强检索...")
            hyde_doc = _generate_hyde_document(question)
            retrieval_query = f"{question}\n\n{hyde_doc}"
            yield ("hyde_generated", {"hyde_doc": hyde_doc[:200] + "..." if len(hyde_doc) > 200 else hyde_doc})
        
        # 检索
        candidates = _gather_hybrid_candidates(
            stores,
            retrieval_query,
            category_top_k=top_k_plan,
        )
        
        if not candidates:
            docs: List[Document] = []
        else:
            mmr_pick = mmr_select(
                candidates,
                question,
                top_n=min(MMR_TARGET, len(candidates)),
                lambda_mult=MMR_LAMBDA,
            )
            chosen = mmr_pick or candidates[:min(MMR_TARGET, len(candidates))]
            docs = [cand.doc for cand in chosen]
        
        # 提取来源
        sources: List[str] = []
        seen_sources = set()
        for doc in docs:
            source = doc.metadata.get("source") or doc.metadata.get("file_path")
            category = doc.metadata.get("kb_category")
            source_key = f"{category}:{source}" if category and source else (source or category)
            if source_key and source_key not in seen_sources:
                seen_sources.add(source_key)
                sources.append(source_key)
        
        yield ("retrieval_done", {"sources": sources[:5], "doc_count": len(docs)})
        
        # 准备 LLM 调用
        context = _format_documents(docs)
        provider, model = get_model_config(CONFIG, "rag_answer")
        llm = get_ai_client(provider, model=model)

        no_result_text = "No relevant results found."
        
        if conversation_history and len(conversation_history) > 0:
            history_text = _format_conversation_history(conversation_history)
            messages = RAG_CHAT_WITH_HISTORY_PROMPT.format_messages(
                context=context or no_result_text,
                conversation_history=history_text,
                question=question,
            )
        else:
            messages = RAG_CHAT_PROMPT.format_messages(
                context=context or no_result_text,
                question=question,
            )
        
        # 流式生成答案
        full_answer = ""
        if llm.supports_streaming():
            for delta in llm.stream_chat(messages, temperature=0.1):
                full_answer += delta
                yield ("answer_delta", {"delta": delta})
        else:
            # 回退到阻塞式
            full_answer = llm.chat(messages, temperature=0.1)
            yield ("answer_delta", {"delta": full_answer})
        
        yield ("answer_done", {"answer": full_answer.strip(), "sources": sources})
        
    except Exception as e:
        logger.exception("[RAG Stream] 流式问答失败")
        yield ("error", {"error": str(e)})


def main() -> None:
    """
    直接启动命令行对话，无需传入额外参数。
    """
    root_path = _resolve_vector_store_root(VECTOR_STORE_PATH)
    print(f"使用向量库根目录：{root_path}")
    interactive_chat(root_path, category_top_k=CATEGORY_TOP_K)


if __name__ == "__main__":
    main()

