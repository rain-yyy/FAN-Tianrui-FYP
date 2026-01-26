from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, TYPE_CHECKING

from dotenv import load_dotenv
from langchain_core.documents import Document
from src.ai_client_factory import get_ai_client
from src.prompts import get_rag_chat_prompt, RAG_CHAT_PROMPT
from src.ingestion.kb_loader import load_knowledge_base
from src.core.retrieval import (
    RankedCandidate,
    SparseBM25Index,
    compute_doc_key,
    mmr_select,
    normalize_scores,
)

if TYPE_CHECKING:
    from langchain_community.vectorstores import FAISS
else:  # pragma: no cover - runtime fallback for type checking convenience
    FAISS = object  # type: ignore[assignment]

load_dotenv()

openai_key = os.getenv("OPENAI_API_KEY")

DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"
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


@dataclass
class CategoryRetrievalUnit:
    dense_store: "FAISS"
    sparse_index: SparseBM25Index | None


def _ensure_openai_key() -> None:
    if not openai_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable not set. Please set it before running."
        )


def _resolve_vector_store_root(base_path: str) -> str:
    """
    校验并返回向量库根目录。
    """
    if not base_path:
        raise ValueError(
            "请通过环境变量 VECTOR_STORE_PATH 指定向量库根目录或具体目录。"
        )

    candidate = os.path.abspath(base_path)
    if not os.path.exists(candidate):
        raise FileNotFoundError(f"指定的向量库目录不存在：{candidate}")
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
        f"未找到 {category} 向量库。请确认目录 {root_path}/{category} 下已生成 index.faiss。"
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
    root_path: str, categories: Iterable[str]
) -> Dict[str, CategoryRetrievalUnit]:
    """
    加载多个类别的向量库，包括密集向量存储和稀疏检索索引。
    为每个类别创建 CategoryRetrievalUnit，包含 FAISS 向量库和 BM25 索引。
    返回类别名称到检索单元的映射字典。
    """
    stores: Dict[str, CategoryRetrievalUnit] = {}
    for category in categories:
        category_path = _resolve_category_path(root_path, category)
        print(f"Loading vector store for '{category}' from {category_path} ...")
        dense_store = load_knowledge_base(category_path)
        docs = _extract_store_documents(dense_store)
        sparse_index = SparseBM25Index.build(docs) if docs else None
        stores[category] = CategoryRetrievalUnit(
            dense_store=dense_store,
            sparse_index=sparse_index,
        )
    return stores


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
        header_parts = [f"[片段 {idx}]"]
        if category:
            header_parts.append(f"类型：{category}")
        if source:
            header_parts.append(f"来源：{source}")
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
) -> Dict[str, object]:
    """
    基于本地多类别 FAISS 向量库执行检索增强问答（RAG）。
    加载向量库，执行混合检索，使用 MMR 选择文档，然后调用 LLM 生成答案。
    返回包含答案文本和参考来源的字典。
    """
    if not question or not question.strip():
        raise ValueError("问题不能为空。")

    _ensure_openai_key()

    root_path = _resolve_vector_store_root(db_path)
    top_k_plan = dict(category_top_k or CATEGORY_TOP_K)
    stores = _load_vector_stores(root_path, top_k_plan.keys())

    return _answer_with_stores(stores, question, category_top_k=top_k_plan)


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
        "加载本地向量库成功，输入问题开始对话（输入 exit/quit 退出）。"
        f" 当前检索计划：{', '.join(f'{cat}:{k}' for cat, k in top_k_plan.items())}"
    )

    while True:
        try:
            question = input("问题> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n中断，退出对话。")
            break

        if not question:
            continue
        if question.lower() in {"exit", "quit"}:
            print("结束对话。")
            break

        try:
            result = _answer_with_stores(
                stores, question, category_top_k=top_k_plan
            )
        except Exception as exc:
            print(f"发生错误：{exc}")
            continue

        print("\nAI 回答：")
        print(result["answer"])
        if result["sources"]:
            print("\n参考来源：")
            for src in result["sources"]:
                print(f"- {src}")
        print("-" * 40)


def _answer_with_stores(
    stores: Mapping[str, CategoryRetrievalUnit],
    question: str,
    *,
    category_top_k: Mapping[str, int],
) -> Dict[str, object]:
    """
    使用已加载的向量库回答问题。
    执行混合检索收集候选文档，使用 MMR 算法选择最相关且多样化的文档，
    格式化上下文后调用 LLM 生成答案，并提取参考来源信息。
    返回包含答案和来源的字典。
    """
    _ensure_openai_key()

    candidates = _gather_hybrid_candidates(
        stores,
        question,
        category_top_k=category_top_k,
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
        chosen = mmr_pick or candidates[: min(MMR_TARGET, len(candidates))]
        docs = [cand.doc for cand in chosen]

    context = _format_documents(docs)
    if not context:
        print("Warning: No relevant context retrieved. The answer may be limited.")

    llm = get_ai_client("openai", model=DEFAULT_MODEL)

    print("Calling AI model to generate answer...")
    messages = RAG_CHAT_PROMPT.format_messages(
        context=context or "无检索结果。", 
        question=question
    )
    answer_text = llm.chat(messages, temperature=0.1)

    if not isinstance(answer_text, str):
        answer_text = str(answer_text)

    sources: List[str] = []
    for doc in docs:
        source = doc.metadata.get("source") or doc.metadata.get("file_path")
        category = doc.metadata.get("kb_category")
        if source:
            if category:
                sources.append(f"{category}:{source}")
            else:
                sources.append(source)
        elif category:
            sources.append(category)

    return {
        "answer": answer_text.strip(),
        "sources": sources,
    }


def main() -> None:
    """
    直接启动命令行对话，无需传入额外参数。
    """
    root_path = _resolve_vector_store_root(VECTOR_STORE_PATH)
    print(f"使用向量库根目录：{root_path}")
    interactive_chat(root_path, category_top_k=CATEGORY_TOP_K)


if __name__ == "__main__":
    main()

