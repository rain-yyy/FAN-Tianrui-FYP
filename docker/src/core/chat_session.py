"""
RAG 与 Agent 两种聊天模式共用的会话建立流程：校验请求字段、按需创建会话、
持久化用户消息、解析向量库路径、拼接页面上下文。四个路由
(`/chat`, `/chat/stream`, `/agent/chat`, `/agent/chat/stream`) 在真正调用各自的
检索/推理逻辑之前都执行这套完全相同的 Supabase 编排步骤，此前各自复制了一份。
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException

from src.core.chat_titles import generate_chat_preview_sync
from src.core.path_resolver import normalize_vector_store_path
from src.storage.supabase_client import SupabaseClient


@dataclass
class ChatTurnContext:
    chat_id: str
    vector_store_path: str
    enhanced_question: str
    repo_info: dict


def prepare_chat_turn(
    supabase_client: SupabaseClient,
    *,
    question: Optional[str],
    repo_url: Optional[str],
    user_id: Optional[str],
    chat_id: Optional[str],
    current_page_context: Optional[str],
) -> ChatTurnContext:
    """
    执行会话建立所需的全部 Supabase 读写，失败时抛出 HTTPException：
    1. 校验必填字段
    2. 若无 chat_id，创建新会话（标题用快速本地摘要，不调用 LLM）
    3. 保存用户消息
    4. 读取仓库信息并解析出向量库路径（不存在则 404）
    5. 拼接页面上下文，得到最终喂给检索/Agent 的问题文本
    """
    if not question or not repo_url or not user_id:
        raise HTTPException(status_code=400, detail="Missing question, repo_url or user_id")

    if not chat_id:
        preview_text = generate_chat_preview_sync(question)
        chat_session = supabase_client.create_chat_session(
            user_id, repo_url, title=preview_text, preview_text=preview_text
        )
        if not chat_session:
            raise HTTPException(status_code=500, detail="Failed to create chat session")
        chat_id = chat_session["id"]

    if supabase_client.add_chat_message(chat_id, "user", question) is None:
        raise HTTPException(status_code=500, detail="Failed to save user message")

    repo_info = supabase_client.get_repo_information(repo_url)
    if not repo_info or not repo_info.get("vector_store_path"):
        raise HTTPException(
            status_code=404,
            detail="No vector index for this repository. Generate documentation via /generate first.",
        )

    vector_store_path = normalize_vector_store_path(repo_info["vector_store_path"], repo_url)

    enhanced_question = question
    if current_page_context:
        enhanced_question = f"[Current page context: {current_page_context}]\n\nUser question: {question}"

    return ChatTurnContext(
        chat_id=chat_id,
        vector_store_path=vector_store_path,
        enhanced_question=enhanced_question,
        repo_info=repo_info,
    )


__all__ = ["ChatTurnContext", "prepare_chat_turn"]
