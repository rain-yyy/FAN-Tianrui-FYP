"""
Unified chat endpoint: a single agent-first entry point replacing the old
`/chat` (plain RAG) + `/agent/chat` (tool-using agent) split. Simple questions
take a fast path with no tool calls automatically — see
`docker/src/chat/graph.py` — so there is no separate "RAG mode" any more.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from src.chat.models import ChatTurnRequest, ChatTurnResponse
from src.chat.service import ChatTurnService
from src.storage.supabase_client import get_supabase_client
from src.utils.logger import setup_logger

logger = setup_logger("api.chat")
router = APIRouter()


@router.post("/chat", response_model=ChatTurnResponse)
async def chat_api(request: ChatTurnRequest) -> ChatTurnResponse:
    """Non-streaming chat turn: runs the agent to completion and returns the final answer."""
    try:
        return await ChatTurnService().run(request)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Chat turn failed")
        raise HTTPException(status_code=500, detail=f"Chat failed: {e}")


@router.post("/chat/stream")
async def chat_stream_api(request: ChatTurnRequest) -> StreamingResponse:
    """
    Streaming chat turn (SSE). Event vocabulary: turn_start, iteration_start,
    tool_call_start, tool_call_result, answer_token, answer_done, complete, error.
    """
    return StreamingResponse(
        ChatTurnService().run_streaming(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/history")
async def list_chat_history_api(user_id: str):
    """Return a user's chat sessions."""
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    supabase_client = get_supabase_client()
    return {"history": supabase_client.get_user_chat_sessions(user_id)}


@router.get("/chat/messages/{chat_id}")
async def get_chat_messages_api(chat_id: str):
    """Return all messages for a chat session."""
    if not chat_id:
        raise HTTPException(status_code=400, detail="Missing chat_id")
    supabase_client = get_supabase_client()
    return {"messages": supabase_client.get_chat_messages(chat_id)}


@router.delete("/chat/history/{chat_id}")
async def delete_chat_session_api(chat_id: str, user_id: str):
    """Delete a chat session and its messages. Only the owning user_id may delete it."""
    if not chat_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing chat_id or user_id")
    supabase_client = get_supabase_client()
    if not supabase_client.delete_chat_session(chat_id, user_id):
        raise HTTPException(status_code=404, detail="Chat not found or unauthorized")
    return {"success": True}
