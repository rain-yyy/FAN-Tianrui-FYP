import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.agent import AgentRunner, run_agent
from src.core.chat_session import prepare_chat_turn
from src.core.path_resolver import resolve_agent_paths
from src.storage.supabase_client import SupabaseClient
from src.utils.json_utils import to_jsonable
from src.utils.logger import setup_logger

logger = setup_logger("api.agent")
router = APIRouter()


@router.post("/agent/chat")
async def agent_chat_api(request: Request):
    """
    Agent 模式问答接口

    与 RAG 模式不同，Agent 模式会：
    1. 分析问题意图并制定探索计划
    2. 迭代式收集上下文（使用 RAG、代码图谱、文件读取等工具）
    3. 自我反思评估信息充分性
    4. 生成带有 Mermaid 图表和精确溯源的答案

    Request Body:
        question: 用户问题
        repo_url: 仓库 URL
        user_id: 用户 ID
        chat_id: 会话 ID（可选，不传则创建新会话）
        conversation_history: 对话历史（可选）
        current_page_context: 当前页面上下文（可选）

    Response:
        answer: 最终答案
        mermaid: Mermaid 图表代码（可选）
        sources: 引用来源列表
        trajectory: Agent 推理轨迹
        confidence: 置信度分数
        iterations: 迭代次数
        chat_id: 会话 ID
        repo_url: 仓库 URL
    """
    try:
        data = await request.json()
        question = data.get("question")
        repo_url = data.get("repo_url")
        user_id = data.get("user_id")
        chat_id = data.get("chat_id")
        conversation_history = data.get("conversation_history")
        current_page_context = data.get("current_page_context")

        supabase_client = SupabaseClient()
        turn = prepare_chat_turn(
            supabase_client,
            question=question,
            repo_url=repo_url,
            user_id=user_id,
            chat_id=chat_id,
            current_page_context=current_page_context,
        )
        chat_id = turn.chat_id
        graph_path, repo_root = resolve_agent_paths(repo_url, turn.vector_store_path)

        logger.info(
            f"Agent 模式问答开始: question={question[:50]}... repo_url={repo_url} "
            f"graph_path={graph_path} repo_root={repo_root}"
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_agent(
                question=turn.enhanced_question,
                repo_url=repo_url,
                vector_store_path=turn.vector_store_path,
                graph_path=graph_path,
                repo_root=repo_root,
                conversation_history=conversation_history,
                max_iterations=5,
            )
        )

        answer = str(result.get("answer", ""))
        sources = list(result.get("sources", []))
        mermaid = result.get("mermaid")
        trajectory = result.get("trajectory", [])
        confidence = float(result.get("confidence", 0.0))
        iterations = int(result.get("iterations", 0))

        metadata = {
            "sources": sources,
            "mermaid": mermaid,
            "trajectory": trajectory,
            "confidence": confidence,
            "iterations": iterations,
            "mode": "agent"
        }
        metadata = to_jsonable(metadata)
        if supabase_client.add_chat_message(chat_id, "assistant", answer, metadata) is None:
            raise HTTPException(status_code=500, detail="Failed to save assistant message")

        logger.info(f"Agent 模式问答完成: iterations={iterations}, confidence={confidence:.2f}")

        return to_jsonable({
            "answer": answer,
            "mermaid": mermaid,
            "sources": sources,
            "trajectory": trajectory,
            "confidence": confidence,
            "iterations": iterations,
            "chat_id": chat_id,
            "repo_url": repo_url
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Agent 问答过程中发生异常:")
        raise HTTPException(status_code=500, detail=f"Agent chat failed: {str(e)}")


@router.post("/agent/chat/stream")
async def agent_chat_stream_api(request: Request):
    """
    Agent 模式流式问答接口 (Server-Sent Events)

    实时返回 Agent 的思考过程和工具调用轨迹。

    Request Body:
        与 /agent/chat 相同

    Response (SSE):
        event: planning | tool_call | evaluation | synthesis | complete | error
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

        supabase_client = SupabaseClient()
        turn = prepare_chat_turn(
            supabase_client,
            question=question,
            repo_url=repo_url,
            user_id=user_id,
            chat_id=chat_id,
            current_page_context=current_page_context,
        )
        chat_id = turn.chat_id
        graph_path, repo_root = resolve_agent_paths(repo_url, turn.vector_store_path)

        async def event_generator():
            runner = AgentRunner(
                vector_store_path=turn.vector_store_path,
                graph_path=graph_path,
                repo_root=repo_root,
                max_iterations=5,
            )

            async for event in runner.run_streaming(
                question=turn.enhanced_question,
                repo_url=repo_url,
                conversation_history=conversation_history
            ):
                if event.event_type == "final_result":
                    payload = to_jsonable(event.data)
                    payload["chat_id"] = chat_id
                    payload["repo_url"] = repo_url

                    answer = str(payload.get("answer", ""))
                    sources = list(payload.get("sources", []))
                    metadata = to_jsonable({
                        "sources": sources,
                        "mermaid": payload.get("mermaid"),
                        "trajectory": payload.get("trajectory", []),
                        "confidence": float(payload.get("confidence", 0.0)),
                        "iterations": int(payload.get("iterations", 0)),
                        "mode": "agent",
                    })
                    if supabase_client.add_chat_message(chat_id, "assistant", answer, metadata) is None:
                        err_pl = {"detail": "Failed to save assistant message"}
                        yield f"event: error\ndata: {json.dumps(err_pl, ensure_ascii=False)}\n\n"
                        return

                    yield f"event: complete\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                elif event.event_type == "error":
                    payload = to_jsonable(event.data)
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    payload = to_jsonable(event.data)
                    yield f"event: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

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
        logger.exception("Agent 流式问答过程中发生异常:")
        raise HTTPException(status_code=500, detail=f"Agent streaming chat failed: {str(e)}")
