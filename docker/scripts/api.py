import sys
import os
import uuid
import asyncio
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

# 获取项目根目录并添加到 sys.path（必须在导入 src 模块之前）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv("../.env")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import json

from src.storage.supabase_client import SupabaseClient, SupabaseStorageError
from src.agent import run_agent, AgentRunner
from src.config import CONFIG
from src.clients.ai_client_factory import get_ai_client, get_model_config

# 初始化日志
from src.utils.logger import setup_logger
from src.utils.repo_utils import get_repo_name
logger = setup_logger("api")

# 导入业务逻辑和管理模块
from src.core.wiki_pipeline import execute_generation_task, VECTOR_STORE_ROOT, REPO_STORE_ROOT
from src.core.chat import answer_question, answer_question_stream


def _normalize_vector_store_path(raw_path: Optional[str], repo_url: str) -> str:
    """
    规范化向量库路径，确保从持久化卷正确加载。
    兼容 Supabase 中可能存在的旧路径（如 /app/vector_stores/xxx）。
    """
    if not raw_path or not raw_path.strip():
        raise ValueError("vector_store_path is empty")
    path_str = raw_path.strip()
    # 旧版本地 Docker 可能存储了 /app/vector_stores/xxx，映射到当前 VECTOR_STORE_ROOT
    if path_str.startswith("/app/vector_stores/"):
        suffix = path_str[len("/app/vector_stores/"):].lstrip("/")
        path_str = str(VECTOR_STORE_ROOT / suffix) if suffix else str(VECTOR_STORE_ROOT / get_repo_name(repo_url))
    elif path_str == "/app/vector_stores":
        path_str = str(VECTOR_STORE_ROOT / get_repo_name(repo_url))
    return path_str


def _resolve_path_under_root(root: Path, input_path: str) -> Optional[Path]:
    root_resolved = root.expanduser().resolve()
    raw = Path(input_path).expanduser()

    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (root_resolved / raw).resolve()

    try:
        target.relative_to(root_resolved)
    except ValueError:
        return None
    return target

app = FastAPI(
    title="Project Wiki Generation API",
    description="Async API: turn a repository URL into wiki structure and generated content.",
)

# 配置 CORS，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境下允许所有来源，生产环境应指定具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from enum import Enum
class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CACHED = "cached"
    FAILED = "failed"


def _to_jsonable_builtin(value: Any) -> Any:
    """
    将 numpy/自定义对象中的标量递归转换为 Python 原生可 JSON 序列化类型。
    """
    try:
        import numpy as np  # 延迟导入，避免无依赖环境报错
        numpy_scalar_types = (np.generic,)
    except Exception:
        numpy_scalar_types = tuple()

    if isinstance(value, numpy_scalar_types):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _to_jsonable_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable_builtin(v) for v in value]
    if isinstance(value, Enum):
        return value.value
    return value


# ============ API Endpoints ============

# ============ Global State ============
# 存储正在运行的异步任务，以便可以被强制终止
running_tasks: Dict[str, asyncio.Task] = {}


def _generate_chat_preview_sync(question: str) -> str:
    """
    Generate a short preview text (title) using simple truncation rules.
    This is fast and non-blocking - no LLM call.
    """
    question = question.strip()
    
    # Remove common prefixes
    prefixes_to_remove = [
        "[Current page context:",
        "User question:",
        "Question:",
    ]
    for prefix in prefixes_to_remove:
        if question.startswith(prefix):
            question = question[len(prefix):].strip()
    
    # Extract first meaningful sentence or phrase
    for delimiter in ["？", "?", "。", "\n", "，", ","]:
        if delimiter in question:
            question = question.split(delimiter)[0].strip()
            break
    
    # Truncate to reasonable length
    max_len = 40
    if len(question) <= max_len:
        return question if question else "New chat"
    
    # Try to cut at word boundary
    truncated = question[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > max_len // 2:
        truncated = truncated[:last_space]
    
    return truncated.strip() + "..." if truncated else "New chat"


async def _generate_chat_preview_async(question: str) -> str:
    """
    Async wrapper for generating chat preview using LLM.
    Can be used for background title enhancement if needed.
    """
    try:
        provider, model = get_model_config(CONFIG, "chat_title")
        client = get_ai_client(provider, model=model)
        
        prompt = f"""Summarize in 3-5 words as a chat title (no quotes):
{question[:200]}

Title:"""
        messages = [{"role": "user", "content": prompt}]
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.chat(messages, temperature=0.3, max_tokens=20)
        )
        title = response.strip().strip('"').strip("'")
        return title if title else _generate_chat_preview_sync(question)
    except Exception as e:
        logger.debug(f"LLM title generation failed, using fallback: {e}")
        return _generate_chat_preview_sync(question)


@app.post("/generate")
async def generate_wiki(request: Request):
    """
    创建 Wiki 生成任务（异步）
    """
    try:
        data = await request.json()
        url_link = data.get("url_link")
        user_id = data.get("user_id")

        if not url_link or not user_id:
            raise HTTPException(status_code=400, detail="Missing url_link or user_id")

        supabase_client = SupabaseClient()
        cached_result = supabase_client.build_cached_task_result(url_link)
        logger.info(
            "缓存检查: input_url=%s normalized_url=%s hit=%s",
            url_link,
            supabase_client._normalize_repo_url(url_link),
            bool(cached_result),
        )

        # 生成唯一任务 ID
        task_id = str(uuid.uuid4())

        # 使用supabase 来控制任务记录
        success = supabase_client.create_task(user_id, task_id, url_link)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create task record")

        if cached_result:
            supabase_client.update_task_progress(task_id, 100.0, "Cache hit — loaded existing docs")
            supabase_client.update_task_status(task_id, TaskStatus.CACHED.value, result=cached_result)
            logger.info(f"缓存命中，直接返回任务结果: {task_id}")
            return {
                "task_id": task_id,
                "message": "Cache hit — existing documentation loaded."
            }

        logger.info(f"创建任务成功: {task_id}")

        # 启动后台任务
        task = asyncio.create_task(execute_generation_task(task_id, url_link))
        running_tasks[task_id] = task
        
        # 任务完成后自动从字典中移除
        task.add_done_callback(lambda t: running_tasks.pop(task_id, None))

        return {
            "task_id": task_id,
            "message": "Task created and processing in the background. Poll /task/{task_id} for progress."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建任务失败:")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/task/{task_id}")
async def get_task_information_api(task_id: str):
    """
    查询任务信息
    """
    logger.debug(f"查询任务信息: {task_id}")

    supabase_client = SupabaseClient()

    try:
        task_information = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        logger.error("查询任务时 Supabase 不可用: %s", e)
        raise HTTPException(status_code=503, detail=f"Database temporarily unavailable: {e}")

    if not task_information:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return {"task": task_information}


@app.post("/tasks")
async def list_tasks_api(request: Request):
    """
    列出所有任务
    """
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")

    logger.debug(f"列出所有任务: {user_id}")
    supabase_client = SupabaseClient()
    all_tasks = supabase_client.get_all_tasks(user_id)
    return {"tasks": all_tasks}


@app.post("/task/{task_id}/cancel")
async def cancel_task_api(task_id: str):
    """
    强制终止处于 processing 状态的任务
    """
    logger.info(f"请求强制终止任务: {task_id}")
    supabase_client = SupabaseClient()

    def _persist_cancelled_status() -> bool:
        return supabase_client.update_task_status(
            task_id, TaskStatus.FAILED.value, error="Cancelled by user"
        )

    if task_id in running_tasks:
        task = running_tasks[task_id]
        task.cancel()
        running_tasks.pop(task_id, None)

        if not _persist_cancelled_status():
            logger.error(f"取消任务后写入 Supabase 失败: {task_id}")
            raise HTTPException(
                status_code=503,
                detail="Task stopped locally but failed to persist cancelled status; try again.",
            )
        logger.info(f"任务已强制终止: {task_id}")
        return {"success": True, "message": "Task cancelled"}

    try:
        task_info = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        logger.error("取消任务时无法查询 Supabase: %s", e)
        raise HTTPException(status_code=503, detail=f"Could not verify task status: {e}")

    if task_info and task_info.get("status") == TaskStatus.PROCESSING.value:
        if not _persist_cancelled_status():
            logger.error(f"仅 DB 标记取消时写入失败: {task_id}")
            raise HTTPException(status_code=503, detail="Failed to update task status in database")
        logger.info(f"内存中未找到任务，但已在数据库中将其标记为终止: {task_id}")
        return {"success": True, "message": "Task marked as cancelled"}

    if task_info and task_info.get("status") == TaskStatus.PENDING.value:
        # 尚未进入 execute_generation_task 的 processing，无内存任务可杀；删除流程可继续
        return {"success": True, "message": "Task not yet running; nothing to cancel"}

    logger.warning(f"无法终止任务（未运行或不存在）: {task_id}")
    raise HTTPException(
        status_code=404,
        detail="No running task found or task is not processing",
    )


@app.delete("/task/{task_id}")
async def delete_task_api(task_id: str, user_id: str):
    """
    删除任务记录（包括进行中、已完成或失败的任务）
    """
    if not task_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing task_id or user_id")
    logger.info(f"删除任务: {task_id}")
    supabase_client = SupabaseClient()
    success = supabase_client.delete_task(task_id, user_id)
    if not success:
        try:
            task = supabase_client.get_task(task_id)
        except SupabaseStorageError as e:
            raise HTTPException(status_code=503, detail=f"Database error while verifying task: {e}")
        if not task:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        if task.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Task not found or unauthorized")
        raise HTTPException(status_code=500, detail="Failed to delete task")

    return {"success": True}


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/profile")
async def get_profile_api(user_id: str):
    """
    Get user profile (theme preference).
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    supabase_client = SupabaseClient()
    profile = supabase_client.get_profile(user_id)
    if not profile:
        return {"profile": {"id": user_id, "theme": "dark"}}
    return {"profile": profile}


@app.patch("/profile")
async def update_profile_api(request: Request):
    """
    Update user theme preference.
    """
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    theme = data.get("theme")
    if theme is None:
        raise HTTPException(status_code=400, detail="Nothing to update")
    supabase_client = SupabaseClient()
    ok = supabase_client.upsert_profile_preferences(user_id, theme=theme)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update profile")
    return {"success": True}


@app.post("/file/content")
async def get_file_content_api(request: Request):
    """
    读取指定仓库的文件内容
    """
    data = await request.json()
    repo_url = data.get("repo_url")
    file_path = data.get("file_path")

    if not repo_url or not file_path:
        raise HTTPException(status_code=400, detail="Missing repo_url or file_path")

    supabase_client = SupabaseClient()
    repo_info = supabase_client.get_repo_information(repo_url)
    if not repo_info:
        raise HTTPException(status_code=404, detail="Repository not found")

    folder_raw = repo_info.get("local_path")
    if folder_raw is None or not str(folder_raw).strip():
        raise HTTPException(
            status_code=400,
            detail="Repository local_path is missing; expected folder name under REPO_STORE_PATH",
        )
    folder = str(folder_raw).strip()
    folder_path = Path(folder)
    if folder_path.is_absolute() or len(folder_path.parts) != 1 or folder_path.name in (".", ".."):
        raise HTTPException(status_code=400, detail="local_path must be a single directory name")

    repo_root = REPO_STORE_ROOT.expanduser() / folder_path.name
    target_path = _resolve_path_under_root(repo_root, file_path)
    if target_path is None or not target_path.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    content = target_path.read_text(encoding="utf-8", errors="replace")
    return {"content": content}
    

@app.post("/chat")
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

        if not question or not repo_url or not user_id:
            raise HTTPException(status_code=400, detail="Missing question, repo_url or user_id")

        supabase_client = SupabaseClient()
        
        # 1. 如果没有 chat_id，创建一个新的会话
        if not chat_id:
            # Use fast sync title generation (no LLM call)
            preview_text = _generate_chat_preview_sync(question)
            
            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
            if not chat_session:
                raise HTTPException(status_code=500, detail="Failed to create chat session")
            chat_id = chat_session["id"]
        
        # 2. 保存用户问题到数据库
        if supabase_client.add_chat_message(chat_id, "user", question) is None:
            raise HTTPException(status_code=500, detail="Failed to save user message")

        # 3. 从 Supabase 获取向量库路径
        repo_info = supabase_client.get_repo_information(repo_url)
        if not repo_info or not repo_info.get("vector_store_path"):
            raise HTTPException(
                status_code=404,
                detail="No vector index for this repository. Generate documentation via /generate first.",
            )
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )

        # 4. Build enhanced question (page context)
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[Current page context: {current_page_context}]\n\nUser question: {question}"
        
        # 5. Run RAG Q&A (answers are always in English)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: answer_question(
                db_path=vector_store_path,
                question=enhanced_question,
                conversation_history=conversation_history,
            )
        )
        
        answer = str(result.get("answer", ""))
        sources = list(result.get("sources", []))

        # 6. 保存助手回答到数据库
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


@app.post("/chat/stream")
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

        if not question or not repo_url or not user_id:
            raise HTTPException(status_code=400, detail="Missing question, repo_url or user_id")

        supabase_client = SupabaseClient()
        
        # 创建会话
        if not chat_id:
            preview_text = _generate_chat_preview_sync(question)
            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
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
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )
        
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[Current page context: {current_page_context}]\n\nUser question: {question}"

        async def event_generator():
            loop = asyncio.get_event_loop()
            full_answer = ""
            sources = []
            
            # 在线程池中执行流式生成器
            def run_stream():
                return list(answer_question_stream(
                    db_path=vector_store_path,
                    question=enhanced_question,
                    conversation_history=conversation_history,
                    use_hyde=True,
                ))
            
            events = await loop.run_in_executor(None, run_stream)
            
            for event_type, event_data in events:
                if event_type == "answer_delta":
                    full_answer += event_data.get("delta", "")
                elif event_type == "answer_done":
                    sources = event_data.get("sources", [])
                
                payload = _to_jsonable_builtin(event_data)
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


@app.get("/chat/repos")
async def list_available_repos_api():
    """
    列出所有可用于聊天的仓库
    """
    logger.info("列出所有可用于聊天的仓库")
    supabase_client = SupabaseClient()
    try:
        available_repos = supabase_client.get_all_available_repos()
    except SupabaseStorageError as e:
        logger.error("列出可用仓库时 Supabase 失败: %s", e)
        raise HTTPException(status_code=503, detail=f"Failed to list repositories: {e}")

    return {"repos": available_repos}


@app.get("/chat/history")
async def list_chat_history_api(user_id: str):
    """
    获取用户的聊天会话列表
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    
    logger.info(f"获取用户聊天记录: {user_id}")
    supabase_client = SupabaseClient()
    history = supabase_client.get_user_chat_history(user_id)
    return {"history": history}


@app.get("/chat/messages/{chat_id}")
async def get_chat_messages_api(chat_id: str):
    """
    获取特定会话的消息记录
    """
    if not chat_id:
        raise HTTPException(status_code=400, detail="Missing chat_id")
    
    logger.info(f"获取会话消息: {chat_id}")
    supabase_client = SupabaseClient()
    messages = supabase_client.get_chat_messages(chat_id)
    return {"messages": messages}


@app.delete("/chat/history/{chat_id}")
async def delete_chat_history_api(chat_id: str, user_id: str):
    """
    删除指定会话及其消息。
    仅当 chat 属于该 user_id 时才可删除。
    """
    if not chat_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing chat_id or user_id")
    supabase_client = SupabaseClient()
    ok = supabase_client.delete_chat_history(chat_id, user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Chat not found or unauthorized")
    return {"success": True}


# ============ Agent Mode Endpoints ============

@app.post("/agent/chat")
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

        if not question or not repo_url or not user_id:
            raise HTTPException(status_code=400, detail="Missing question, repo_url or user_id")

        supabase_client = SupabaseClient()
        
        if not chat_id:
            # Use fast sync title generation (no LLM call)
            preview_text = _generate_chat_preview_sync(question)

            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
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
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )
        graph_path = repo_info.get("graph_path")
        repo_root = repo_info.get("repo_root")
        
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[Current page context: {current_page_context}]\n\nUser question: {question}"
        
        logger.info(f"Agent 模式问答开始: question={question[:50]}... repo_url={repo_url}")
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_agent(
                question=enhanced_question,
                repo_url=repo_url,
                vector_store_path=vector_store_path,
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
        metadata = _to_jsonable_builtin(metadata)
        if supabase_client.add_chat_message(chat_id, "assistant", answer, metadata) is None:
            raise HTTPException(status_code=500, detail="Failed to save assistant message")

        logger.info(f"Agent 模式问答完成: iterations={iterations}, confidence={confidence:.2f}")
        
        return _to_jsonable_builtin({
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


@app.post("/agent/chat/stream")
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

        if not question or not repo_url or not user_id:
            raise HTTPException(status_code=400, detail="Missing question, repo_url or user_id")

        supabase_client = SupabaseClient()

        if not chat_id:
            # Use fast sync title generation (no LLM call)
            preview_text = _generate_chat_preview_sync(question)

            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
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
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )
        graph_path = repo_info.get("graph_path")
        repo_root = repo_info.get("repo_root")
        
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[Current page context: {current_page_context}]\n\nUser question: {question}"

        async def event_generator():
            runner = AgentRunner(
                vector_store_path=vector_store_path,
                graph_path=graph_path,
                repo_root=repo_root,
                max_iterations=5,
            )

            async for event in runner.run_streaming(
                question=enhanced_question,
                repo_url=repo_url,
                conversation_history=conversation_history
            ):
                if event.event_type == "final_result":
                    payload = _to_jsonable_builtin(event.data)
                    payload["chat_id"] = chat_id
                    payload["repo_url"] = repo_url

                    answer = str(payload.get("answer", ""))
                    sources = list(payload.get("sources", []))
                    metadata = _to_jsonable_builtin({
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
                    payload = _to_jsonable_builtin(event.data)
                    yield f"event: error\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                else:
                    payload = _to_jsonable_builtin(event.data)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
