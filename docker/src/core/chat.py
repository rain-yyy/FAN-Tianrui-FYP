import json
import os
import logging
from pathlib import Path
from typing import Dict, Generator, List, Mapping, Optional, Tuple

from langchain_core.documents import Document
from src.clients import get_llm, StrOutputParser
from src.prompts import HYDE_PROMPT, RAG_CHAT_PROMPT, RAG_CHAT_WITH_HISTORY_PROMPT
from src.ingestion.vector_store import (
    COLLECTIONS,
    get_qdrant_client,
    payload_to_document,
    query_dense,
    repo_filter,
)
from src.core.retrieval import (
    RankedCandidate,
    compute_doc_key,
    create_community_retriever,
    mmr_select,
    normalize_scores,
    qdrant_search_category,
)

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
GRAPHRAG_COMMUNITIES_FILENAME = "graphrag_communities.json"

# HyDE 配置
HYDE_ENABLED = True  # 是否启用 HyDE


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
        chain = HYDE_PROMPT.build() | get_llm("hyde_generation", temperature=0.3, max_tokens=500) | StrOutputParser()
        hyde_doc = chain.invoke({"question": question})
        
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


def _load_graphrag_communities(
    root_path: str,
) -> Optional[Tuple[Dict[int, List[str]], Dict[int, str]]]:
    """
    读取与向量库同目录下的 GraphRAG 社区划分与摘要（wiki 流程中由 struct_gen 生成并写入）。
    """
    path = os.path.join(root_path, GRAPHRAG_COMMUNITIES_FILENAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        comm_raw = data.get("communities") or {}
        summ_raw = data.get("summaries") or {}
        communities: Dict[int, List[str]] = {}
        for k, v in comm_raw.items():
            try:
                cid = int(k)
            except (TypeError, ValueError):
                continue
            if isinstance(v, list):
                communities[cid] = [str(x) for x in v]
        summaries: Dict[int, str] = {}
        for k, v in summ_raw.items():
            try:
                cid = int(k)
            except (TypeError, ValueError):
                continue
            summaries[cid] = str(v) if v is not None else ""
        if not communities or not summaries:
            return None
        return communities, summaries
    except (OSError, json.JSONDecodeError, TypeError) as e:
        logger.warning("[RAG] 无法加载 GraphRAG 元数据 %s: %s", path, e)
        return None


def _collect_all_store_documents(
    client,
    repo_id: str,
    categories: Mapping[str, int],
) -> List[Document]:
    """
    scroll 出该 repo_id 在各 category collection 下的全部文档，
    供 CommunityFirstRetriever 构建"文档→社区"映射（等价于旧版从 FAISS docstore 全量取出）。
    """
    combined: List[Document] = []
    for category in categories:
        collection_name = COLLECTIONS[category]
        next_offset = None
        while True:
            points, next_offset = client.scroll(
                collection_name=collection_name,
                scroll_filter=repo_filter(repo_id),
                limit=256,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                combined.append(payload_to_document(point.payload, category))
            if next_offset is None:
                break
    return combined


def _gather_dense_qdrant_hits(
    client,
    repo_id: str,
    question: str,
    *,
    category_top_k: Mapping[str, int],
) -> List[Tuple[Document, float]]:
    """
    仅稠密向量检索命中（跨类别合并），供 CommunityFirstRetriever.hybrid_retrieve 与社区稀疏分融合。

    刻意不复用 qdrant_search_category：后者会对 dense 分数做单次调用内的归一化，
    而这里需要与社区稀疏分融合前保留原始 cosine 分数，因此只借用底层的 query_dense 原语。
    """
    best_by_key: Dict[str, Tuple[Document, float]] = {}
    for category in category_top_k:
        planned_base = max(int(category_top_k.get(category, 0)), 1)
        dense_k = _plan_method_k(planned_base, DENSE_K_MULTIPLIER)

        for payload, score in query_dense(client, category, repo_id, question, dense_k):
            doc = payload_to_document(payload, category)
            key = f"{category}|{compute_doc_key(doc)}"
            prev = best_by_key.get(key)
            if prev is None or float(score) > prev[1]:
                best_by_key[key] = (doc, float(score))

    if not best_by_key:
        return []
    return sorted(best_by_key.values(), key=lambda x: x[1], reverse=True)


def _gather_retrieval_candidates(
    client,
    repo_id: str,
    question: str,
    *,
    category_top_k: Mapping[str, int],
    graphrag: Optional[Tuple[Dict[int, List[str]], Dict[int, str]]] = None,
) -> List[RankedCandidate]:
    """
    若存在 GraphRAG 元数据，则用 CommunityFirstRetriever 做社区优先两阶段稀疏检索，
    并与全库稠密向量分按 HYBRID_DENSE_WEIGHT 融合；否则保持原按类 BM25+稠密混合。
    """
    if graphrag:
        communities, summaries = graphrag
        all_docs = _collect_all_store_documents(client, repo_id, category_top_k)
        if all_docs:
            try:
                retriever = create_community_retriever(communities, summaries, all_docs)
                dense_hits = _gather_dense_qdrant_hits(
                    client, repo_id, question, category_top_k=category_top_k
                )
                merged = retriever.hybrid_retrieve(
                    question,
                    dense_hits,
                    top_k_communities=3,
                    top_k_docs_per_community=5,
                    alpha=HYBRID_DENSE_WEIGHT,
                    top_k=MAX_TOTAL_CANDIDATES,
                )
                if merged:
                    logger.info("[RAG] 已启用 CommunityFirstRetriever 主路径（GraphRAG + 稠密）")
                    return merged
            except Exception as exc:
                logger.warning("[RAG] CommunityFirstRetriever 不可用，回退混合检索: %s", exc)

    return _gather_hybrid_candidates(
        client, repo_id, question, category_top_k=category_top_k
    )


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


def _gather_hybrid_candidates(
    client,
    repo_id: str,
    question: str,
    *,
    category_top_k: Mapping[str, int],
) -> List[RankedCandidate]:
    """
    从所有类别的 Qdrant collection 中收集混合检索候选文档。
    所有类别都会参与召回，随后统一归一化与排序，确保问题同时看到代码与文档视角。
    返回排序后的候选列表，数量限制在最大候选数以内。
    """
    final_candidates: List[RankedCandidate] = []

    for category in category_top_k:
        planned_base = max(int(category_top_k.get(category, 0)), 1)
        dense_k = _plan_method_k(planned_base, DENSE_K_MULTIPLIER)
        sparse_k = _plan_method_k(planned_base, SPARSE_K_MULTIPLIER)
        category_candidates = qdrant_search_category(
            client,
            category,
            repo_id,
            question,
            dense_k=dense_k,
            sparse_k=sparse_k,
            dense_weight=HYBRID_DENSE_WEIGHT,
            sparse_weight=HYBRID_SPARSE_WEIGHT,
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
    基于 Qdrant 托管的向量库执行检索增强问答（RAG）。

    支持 HyDE (Hypothetical Document Embeddings) 和多轮对话。
    执行混合检索，使用 MMR 选择文档，然后调用 LLM 生成答案。

    Args:
        db_path: 向量库根目录路径（本地仍保留 graphrag_communities.json 等结构化元数据；
                 其最后一段目录名即为 Qdrant 里的 repo_id）
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

    repo_id = Path(db_path).name
    top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
    client = get_qdrant_client()
    graphrag = _load_graphrag_communities(db_path)

    return _answer_with_stores(
        client,
        repo_id,
        question,
        category_top_k=top_k_plan,
        conversation_history=conversation_history,
        use_hyde=use_hyde and HYDE_ENABLED,
        graphrag=graphrag,
    )


def interactive_chat(
    db_path: str, *, category_top_k: Mapping[str, int] | None = None
) -> None:
    """
    启动命令行交互式对话模式，用于快速验证 RAG 问答效果。
    持续接收用户输入的问题，返回 AI 答案和参考来源，直到用户输入 exit/quit 退出。
    """
    repo_id = Path(db_path).name
    top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
    client = get_qdrant_client()
    graphrag = _load_graphrag_communities(db_path)

    print(
        f"Using Qdrant repo_id={repo_id!r}. Type a question (exit/quit to stop). "
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
                client,
                repo_id,
                question,
                category_top_k=top_k_plan,
                graphrag=graphrag,
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
    client,
    repo_id: str,
    question: str,
    *,
    category_top_k: Mapping[str, int],
    conversation_history: Optional[List[Dict[str, str]]] = None,
    use_hyde: bool = True,
    graphrag: Optional[Tuple[Dict[int, List[str]], Dict[int, str]]] = None,
) -> Dict[str, object]:
    """
    使用 Qdrant client 回答问题。

    支持 HyDE 增强检索和多轮对话上下文。
    执行混合检索收集候选文档，使用 MMR 算法选择最相关且多样化的文档，
    格式化上下文后调用 LLM 生成答案，并提取参考来源信息。

    Args:
        client: QdrantClient 实例
        repo_id: 仓库标识（Qdrant payload 过滤字段）
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

    candidates = _gather_retrieval_candidates(
        client,
        repo_id,
        retrieval_query,
        category_top_k=category_top_k,
        graphrag=graphrag,
    )
    if not candidates:
        docs = []
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

    logger.info("Calling AI model to generate answer...")

    no_result_text = "No relevant results found."
    llm = get_llm("rag_answer", temperature=0.1)

    # 根据是否有对话历史选择不同的 LCEL chain
    if conversation_history and len(conversation_history) > 0:
        history_text = _format_conversation_history(conversation_history)
        chain = RAG_CHAT_WITH_HISTORY_PROMPT.build() | llm | StrOutputParser()
        answer_text = chain.invoke({
            "context": context or no_result_text,
            "conversation_history": history_text,
            "question": question,
        })
    else:
        chain = RAG_CHAT_PROMPT.build() | llm | StrOutputParser()
        answer_text = chain.invoke({
            "context": context or no_result_text,
            "question": question,
        })

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

        repo_id = Path(db_path).name
        top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
        client = get_qdrant_client()
        graphrag = _load_graphrag_communities(db_path)

        # HyDE 生成
        retrieval_query = question
        if use_hyde and HYDE_ENABLED:
            logger.info("[RAG Stream] 使用 HyDE 增强检索...")
            hyde_doc = _generate_hyde_document(question)
            retrieval_query = f"{question}\n\n{hyde_doc}"
            yield ("hyde_generated", {"hyde_doc": hyde_doc[:200] + "..." if len(hyde_doc) > 200 else hyde_doc})

        # 检索
        candidates = _gather_retrieval_candidates(
            client,
            repo_id,
            retrieval_query,
            category_top_k=top_k_plan,
            graphrag=graphrag,
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
        
        # 准备 LCEL chain
        context = _format_documents(docs)
        no_result_text = "No relevant results found."
        llm = get_llm("rag_answer", temperature=0.1)

        if conversation_history and len(conversation_history) > 0:
            history_text = _format_conversation_history(conversation_history)
            chain = RAG_CHAT_WITH_HISTORY_PROMPT.build() | llm | StrOutputParser()
            chain_input = {
                "context": context or no_result_text,
                "conversation_history": history_text,
                "question": question,
            }
        else:
            chain = RAG_CHAT_PROMPT.build() | llm | StrOutputParser()
            chain_input = {
                "context": context or no_result_text,
                "question": question,
            }

        # 流式生成答案（StrOutputParser 已提取纯文本，chunk 直接是字符串）
        full_answer = ""
        for delta in chain.stream(chain_input):
            if delta:
                full_answer += delta
                yield ("answer_delta", {"delta": delta})
        
        yield ("answer_done", {"answer": full_answer.strip(), "sources": sources})
        
    except Exception as e:
        logger.exception("[RAG Stream] 流式问答失败")
        yield ("error", {"error": str(e)})


def main() -> None:
    """
    直接启动命令行对话，无需传入额外参数。
    """
    if not VECTOR_STORE_PATH:
        raise ValueError(
            "Set VECTOR_STORE_PATH to the vector store root (its dir name is used as the Qdrant repo_id)."
        )
    print(f"使用向量库根目录：{VECTOR_STORE_PATH}")
    interactive_chat(VECTOR_STORE_PATH, category_top_k=CATEGORY_TOP_K)


if __name__ == "__main__":
    main()

