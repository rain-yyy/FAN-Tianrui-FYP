import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.core.chat import answer_question, answer_question_stream
from src.core.chat_session import prepare_chat_turn
from src.storage.supabase_client import get_supabase_client
from src.utils.json_utils import to_jsonable
from src.utils.logger import setup_logger

logger = setup_logger("api.chat")
router = APIRouter()


@router.post("/chat")
async def chat_with_repo_api(request: Request):
    """
    RAG 问答接口，支持会话持久化
    """
    try:
        data = await request.json()
        question = data.get("question")
        repo_url = data.get("repo_url")
        user_id = data.get("user_id")
        chat_id = data.get("chat_id")
        conversation_history = data.get("conversation_history")
        current_page_context = data.get("current_page_context")

        supabase_client = get_supabase_client()
        turn = prepare_chat_turn(
            supabase_client,
            question=question,
            repo_url=repo_url,
            user_id=user_id,
            chat_id=chat_id,
            current_page_context=current_page_context,
        )
        chat_id = turn.chat_id

        # Run RAG Q&A (answers are always in English)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: answer_question(
                db_path=turn.vector_store_path,
                question=turn.enhanced_question,
                conversation_history=conversation_history,
            )
        )

        answer = str(result.get("answer", ""))
        sources = list(result.get("sources", []))

        # 保存助手回答到数据库
        if (
            supabase_client.add_chat_message(
                chat_id,
                "assistant",
                answer,
                {"sources": sources},
            )
            is None
        ):
            raise HTTPException(status_code=500, detail="Failed to save assistant message")

        return {
            "answer": answer,
            "sources": sources,
            "chat_id": chat_id,
            "repo_url": repo_url
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("RAG 问答过程中发生异常:")
        raise HTTPException(status_code=500, detail=f"RAG chat failed: {str(e)}")


@router.post("/chat/stream")
async def chat_stream_api(request: Request):
    """
    RAG 流式问答接口 (Server-Sent Events)

    实时返回检索阶段和答案生成过程。

    Request Body:
        question: 用户问题
        repo_url: 仓库 URL
        user_id: 用户 ID
        chat_id: 会话 ID（可选）
        conversation_history: 对话历史（可选）
        current_page_context: 当前页面上下文（可选）

    Response (SSE):
        event: retrieval_start | hyde_generated | retrieval_done | answer_delta | answer_done | error
        data: JSON 格式的事件数据
    """
    try:
        data = await request.json()
        question = data.get("question")
        repo_url = data.get("repo_url")
        user_id = data.get("user_id")
        chat_id = data.get("chat_id")
        conversation_history = data.get("conversation_history")
        current_page_context = data.get("current_page_context")

        supabase_client = get_supabase_client()
        turn = prepare_chat_turn(
            supabase_client,
            question=question,
            repo_url=repo_url,
            user_id=user_id,
            chat_id=chat_id,
            current_page_context=current_page_context,
        )
        chat_id = turn.chat_id

        async def event_generator():
            loop = asyncio.get_event_loop()
            full_answer = ""
            sources = []

            # 在线程池中执行流式生成器
            def run_stream():
                return list(answer_question_stream(
                    db_path=turn.vector_store_path,
                    question=turn.enhanced_question,
                    conversation_history=conversation_history,
                    use_hyde=True,
                ))

            events = await loop.run_in_executor(None, run_stream)

            for event_type, event_data in events:
                if event_type == "answer_delta":
                    full_answer += event_data.get("delta", "")
                elif event_type == "answer_done":
                    sources = event_data.get("sources", [])

                payload = to_jsonable(event_data)
                yield f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

            # 保存消息
            if full_answer:
                saved = supabase_client.add_chat_message(
                    chat_id,
                    "assistant",
                    full_answer.strip(),
                    {"sources": sources},
                )
                if saved is None:
                    err_payload = {"detail": "Failed to save assistant message"}
                    yield f"event: error\ndata: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
                    return

            # 发送完成事件
            complete_payload = {
                "chat_id": chat_id,
                "repo_url": repo_url,
                "answer": full_answer.strip(),
                "sources": sources,
            }
            yield f"event: complete\ndata: {json.dumps(complete_payload, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("RAG 流式问答过程中发生异常:")
        raise HTTPException(status_code=500, detail=f"RAG streaming chat failed: {str(e)}")


@router.get("/chat/history")
async def list_chat_history_api(user_id: str):
    """
    获取用户的聊天会话列表
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")

    logger.info(f"获取用户聊天记录: {user_id}")
    supabase_client = get_supabase_client()
    history = supabase_client.get_user_chat_sessions(user_id)
    return {"history": history}


@router.get("/chat/messages/{chat_id}")
async def get_chat_messages_api(chat_id: str):
    """
    获取特定会话的消息记录
    """
    if not chat_id:
        raise HTTPException(status_code=400, detail="Missing chat_id")

    logger.info(f"获取会话消息: {chat_id}")
    supabase_client = get_supabase_client()
    messages = supabase_client.get_chat_messages(chat_id)
    return {"messages": messages}


@router.delete("/chat/history/{chat_id}")
async def delete_chat_session_api(chat_id: str, user_id: str):
    """
    删除指定会话及其消息。
    仅当 chat 属于该 user_id 时才可删除。
    """
    if not chat_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing chat_id or user_id")
    supabase_client = get_supabase_client()
    ok = supabase_client.delete_chat_session(chat_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found or unauthorized")
    return {"success": True}
