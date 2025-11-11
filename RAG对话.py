from __future__ import annotations

import os
from typing import Dict, Iterable, List, Mapping, TYPE_CHECKING

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import ChatOpenAI

from knowledge_base_loader import load_knowledge_base
from prompts.prompts import get_rag_chat_prompt

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
RAG_PROMPT = get_rag_chat_prompt()


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
    返回某个类别向量库的目录。
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


def _load_vector_stores(
    root_path: str, categories: Iterable[str]
) -> Dict[str, FAISS]:
    stores: Dict[str, "FAISS"] = {}
    for category in categories:
        category_path = _resolve_category_path(root_path, category)
        print(f"Loading vector store for '{category}' from {category_path} ...")
        stores[category] = load_knowledge_base(category_path)
    return stores


def _format_documents(docs: List[Document]) -> str:
    """
    将检索到的文档块整理为可读字符串，保留来源信息。
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


def answer_question(
    db_path: str,
    question: str,
    *,
    category_top_k: Mapping[str, int] | None = None,
) -> Dict[str, object]:
    """
    基于本地多类别 FAISS 向量库执行检索增强问答。
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
    简单的命令行交互模式，方便快速验证 RAG 问答效果。
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
    stores: Mapping[str, FAISS],
    question: str,
    *,
    category_top_k: Mapping[str, int],
) -> Dict[str, object]:
    _ensure_openai_key()

    docs: List[Document] = []
    for category, store in stores.items():
        k = max(int(category_top_k.get(category, 0)), 0)
        if k <= 0:
            continue
        retrieved = store.similarity_search(question, k=k)
        for doc in retrieved:
            doc.metadata = {**doc.metadata, "kb_category": category}
        docs.extend(retrieved)

    context = _format_documents(docs)
    if not context:
        print("Warning: No relevant context retrieved. The answer may be limited.")

    llm = ChatOpenAI(model=DEFAULT_MODEL, temperature=0.1)

    print("Calling AI model to generate answer...")
    chain = RAG_PROMPT | llm
    response = chain.invoke({"context": context or "无检索结果。", "question": question})
    answer_text = getattr(response, "content", response)

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

