from __future__ import annotations

import os
from typing import Dict, List

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from knowledge_base_loader import load_knowledge_base

load_dotenv()

openai_key = os.getenv("OPENAI_API_KEY")

DEFAULT_MODEL = "gpt-4o-mini-2024-07-18"
DEFAULT_TOP_K = 4
VECTOR_STORE_PATH: str = os.getenv("VECTOR_STORE_PATH", "").strip()


def _ensure_openai_key() -> None:
    if not openai_key:
        raise ValueError(
            "OPENAI_API_KEY environment variable not set. Please set it before running."
        )


def _format_documents(docs: List[Document]) -> str:
    """
    将检索到的文档块整理为可读字符串，保留来源信息。
    """
    if not docs:
        return ""

    formatted_chunks: List[str] = []
    for idx, doc in enumerate(docs, start=1):
        source = doc.metadata.get("source") or doc.metadata.get("file_path") or ""
        header = f"[片段 {idx}] 来源：{source}".strip()
        formatted_chunks.append(f"{header}\n{doc.page_content.strip()}")
    return "\n\n".join(formatted_chunks)


def answer_question(
    db_path: str, question: str, *, top_k: int = DEFAULT_TOP_K
) -> Dict[str, object]:
    """
    基于本地 FAISS 向量库执行检索增强问答。
    """
    if not question or not question.strip():
        raise ValueError("问题不能为空。")

    _ensure_openai_key()

    print("Loading vector store...")
    vector_store = load_knowledge_base(db_path)

    print(f"Retrieving top {top_k} relevant chunks...")
    docs = vector_store.similarity_search(question, k=top_k)

    context = _format_documents(docs)
    if not context:
        print("Warning: No relevant context retrieved. The answer may be limited.")

    llm = ChatOpenAI(model=DEFAULT_MODEL, temperature=0.1)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是一名熟悉软件项目的智能助手。请仅依据提供的上下文回答用户问题，无法确定时请明确说明。",
            ),
            (
                "human",
                "以下是检索到的仓库上下文（可能为空）：\n{context}\n\n"
                "问题：{question}\n\n"
                "请给出简洁、准确的中文回答。",
            ),
        ]
    )

    print("Calling AI model to generate answer...")
    chain = prompt | llm
    response = chain.invoke({"context": context or "无检索结果。", "question": question})
    answer_text = getattr(response, "content", response)

    if not isinstance(answer_text, str):
        answer_text = str(answer_text)

    sources: List[str] = []
    for doc in docs:
        source = doc.metadata.get("source") or doc.metadata.get("file_path")
        if source:
            sources.append(source)

    return {
        "answer": answer_text.strip(),
        "sources": sources,
    }


def interactive_chat(db_path: str, *, top_k: int = DEFAULT_TOP_K) -> None:
    """
    简单的命令行交互模式，方便快速验证 RAG 问答效果。
    """
    print("加载本地向量库成功，输入问题开始对话（输入 exit/quit 退出）。")

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
            result = answer_question(db_path, question, top_k=top_k)
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


def main() -> None:
    """
    直接启动命令行对话，无需传入额外参数。
    """
    db_path = VECTOR_STORE_PATH
    if not db_path:
        raise ValueError(
            "请先在 rag_qa.py 中设置 VECTOR_STORE_PATH 常量，或通过环境变量 VECTOR_STORE_PATH 指定向量库目录。"
        )

    if not os.path.exists(db_path):
        raise FileNotFoundError(f"指定的向量库目录不存在：{db_path}")

    print(f"使用向量库：{db_path}")
    interactive_chat(db_path, top_k=DEFAULT_TOP_K)


if __name__ == "__main__":
    main()

