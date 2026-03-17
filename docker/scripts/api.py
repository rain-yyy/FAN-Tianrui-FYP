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

from src.storage.supabase_client import SupabaseClient
from src.agent import run_agent, AgentRunner
from src.config import CONFIG
from src.clients.ai_client_factory import get_ai_client, get_model_config

# 初始化日志
from src.utils.logger import setup_logger
from src.utils.repo_utils import get_repo_hash, get_repo_name
logger = setup_logger("api")

# 导入业务逻辑和管理模块
from src.core.wiki_pipeline import execute_generation_task, VECTOR_STORE_ROOT, REPO_STORE_ROOT
from src.core.chat import answer_question


def _normalize_vector_store_path(raw_path: Optional[str], repo_url: str) -> str:
    """
    规范化向量库路径，确保从持久化卷正确加载。
    兼容 Supabase 中可能存在的旧路径（如 /app/vector_stores/xxx）。
    """
    if not raw_path or not raw_path.strip():
        raise ValueError("vector_store_path 为空")
    path_str = raw_path.strip()
    # 旧版本地 Docker 可能存储了 /app/vector_stores/xxx，映射到当前 VECTOR_STORE_ROOT
    if path_str.startswith("/app/vector_stores/"):
        suffix = path_str[len("/app/vector_stores/"):].lstrip("/")
        path_str = str(VECTOR_STORE_ROOT / suffix) if suffix else str(VECTOR_STORE_ROOT / get_repo_name(repo_url))
    elif path_str == "/app/vector_stores":
        path_str = str(VECTOR_STORE_ROOT / get_repo_name(repo_url))
    return path_str


def _resolve_repo_roots(repo_info: Dict[str, Any], repo_url: str) -> List[Path]:
    roots: List[Path] = []

    repo_root = repo_info.get("repo_root")
    if repo_root:
        roots.append(Path(str(repo_root)).expanduser())

    # 当前推荐路径：/data/repos/<repo_hash>
    roots.append(REPO_STORE_ROOT / get_repo_hash(repo_url))
    # 历史兼容：/data/repos/<repo_name>
    roots.append(REPO_STORE_ROOT / get_repo_name(repo_url))

    deduped: List[Path] = []
    seen = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


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
    description="将项目仓库 URL 转换为 Wiki 结构和内容的 API（异步任务模式）"
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


def _generate_chat_preview(question: str) -> str:
    """
    Generate a short preview text (title) for the chat session using LLM.
    """
    try:
        provider, model = get_model_config(CONFIG, "chat")
        client = get_ai_client(provider, model=model)
        
        prompt = f"""
Summarize the following user question into a very short phrase (3-5 words) to be used as a chat title.
Do not include "User asks" or "Question about" or similar phrases. Just the topic.
Do not use quotes.

Question: {question}

Title:
"""
        messages = [{"role": "user", "content": prompt}]
        response = client.chat(messages, temperature=0.3, max_tokens=20)
        title = response.strip().strip('"').strip("'")
        return title
    except Exception as e:
        logger.warning(f"Failed to generate chat preview: {e}")
        return f"Chat: {question[:20]}..."


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
            raise HTTPException(status_code=500, detail="创建任务记录失败")

        if cached_result:
            supabase_client.update_task_progress(task_id, 100.0, "命中缓存，已直接加载")
            supabase_client.update_task_status(task_id, TaskStatus.CACHED.value, result=cached_result)
            logger.info(f"缓存命中，直接返回任务结果: {task_id}")
            return {
                "task_id": task_id,
                "message": "命中缓存，已直接加载已有文档。"
            }

        logger.info(f"创建任务成功: {task_id}")

        # 启动后台任务
        asyncio.create_task(execute_generation_task(task_id, url_link))

        return {
            "task_id": task_id,
            "message": "任务已创建，正在后台处理。请使用 /task/{task_id} 查询进度。"
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

    # 去supabase中查找task_id对应的任务信息
    task_information = supabase_client.get_task(task_id)
    if not task_information:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

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
        task = supabase_client.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        if task.get("user_id") != user_id:
            raise HTTPException(status_code=404, detail="Task not found or unauthorized")
        raise HTTPException(status_code=500, detail="删除任务失败")

    return {"success": True}


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.post("/file/content")
async def get_file_content_api(request: Request):
    """
    读取指定仓库的文件内容
    """
    try:
        data = await request.json()
        repo_url = data.get("repo_url")
        file_path = data.get("file_path")

        if not repo_url or not file_path:
            raise HTTPException(status_code=400, detail="Missing repo_url or file_path")

        supabase_client = SupabaseClient()
        repo_info = supabase_client.get_repo_information(repo_url) or {}
        candidate_roots = _resolve_repo_roots(repo_info, repo_url)

        for root in candidate_roots:
            if not root.exists() or not root.is_dir():
                continue

            target_path = _resolve_path_under_root(root, file_path)
            if not target_path:
                continue
            if not target_path.exists() or not target_path.is_file():
                continue

            try:
                content = target_path.read_text(encoding="utf-8", errors="replace")
                return {"content": content}
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Failed to read file: {str(e)}")

        raise HTTPException(
            status_code=404,
            detail=(
                "File not found under repository root. "
                "Please ensure repo is generated and stored under REPO_STORE_PATH."
            ),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("获取文件内容失败:")
        raise HTTPException(status_code=500, detail=str(e))


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
            # Generate preview text
            loop = asyncio.get_event_loop()
            preview_text = await loop.run_in_executor(
                None,
                lambda: _generate_chat_preview(question)
            )
            
            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
            if not chat_session:
                raise HTTPException(status_code=500, detail="创建会话失败")
            chat_id = chat_session["id"]
        
        # 2. 保存用户问题到数据库
        supabase_client.add_chat_message(chat_id, "user", question)

        # 3. 从 Supabase 获取向量库路径
        repo_info = supabase_client.get_repo_information(repo_url)
        if not repo_info or not repo_info.get("vector_store_path"):
            raise HTTPException(
                status_code=404,
                detail="未找到该仓库的向量索引。请先通过 /generate 接口生成文档。"
            )
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )

        # 4. 构建增强问题（包含上下文信息）
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[当前页面上下文: {current_page_context}]\n\n用户问题: {question}"
        
        # 5. 执行 RAG 问答
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: answer_question(
                db_path=vector_store_path,
                question=enhanced_question,
                conversation_history=conversation_history
            )
        )
        
        answer = str(result.get("answer", ""))
        sources = list(result.get("sources", []))

        # 6. 保存助手回答到数据库
        supabase_client.add_chat_message(
            chat_id, 
            "assistant", 
            answer, 
            {"sources": sources}
        )
        
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
        raise HTTPException(status_code=500, detail=f"RAG 问答失败: {str(e)}")


@app.get("/chat/repos")
async def list_available_repos_api():
    """
    列出所有可用于聊天的仓库
    """
    logger.info("列出所有可用于聊天的仓库")
    supabase_client = SupabaseClient()
    available_repos = supabase_client.get_all_available_repos()
    if not available_repos:
        return {"repos": []}
    
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
            # Generate preview text
            loop = asyncio.get_event_loop()
            preview_text = await loop.run_in_executor(
                None,
                lambda: _generate_chat_preview(question)
            )

            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
            if not chat_session:
                raise HTTPException(status_code=500, detail="创建会话失败")
            chat_id = chat_session["id"]
        
        supabase_client.add_chat_message(chat_id, "user", question)

        repo_info = supabase_client.get_repo_information(repo_url)
        if not repo_info or not repo_info.get("vector_store_path"):
            raise HTTPException(
                status_code=404,
                detail="未找到该仓库的向量索引。请先通过 /generate 接口生成文档。"
            )
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )
        graph_path = repo_info.get("graph_path")
        repo_root = repo_info.get("repo_root")
        
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[当前页面上下文: {current_page_context}]\n\n用户问题: {question}"
        
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
                max_iterations=5
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
        supabase_client.add_chat_message(
            chat_id, 
            "assistant", 
            answer, 
            metadata
        )
        
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
        raise HTTPException(status_code=500, detail=f"Agent 问答失败: {str(e)}")


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
            # Generate preview text
            loop = asyncio.get_event_loop()
            preview_text = await loop.run_in_executor(
                None,
                lambda: _generate_chat_preview(question)
            )

            chat_session = supabase_client.create_chat_history(user_id, repo_url, title=preview_text, preview_text=preview_text)
            if not chat_session:
                raise HTTPException(status_code=500, detail="创建会话失败")
            chat_id = chat_session["id"]

        supabase_client.add_chat_message(chat_id, "user", question)
        
        repo_info = supabase_client.get_repo_information(repo_url)
        if not repo_info or not repo_info.get("vector_store_path"):
            raise HTTPException(
                status_code=404,
                detail="未找到该仓库的向量索引。请先通过 /generate 接口生成文档。"
            )
        
        vector_store_path = _normalize_vector_store_path(
            repo_info["vector_store_path"], repo_url
        )
        graph_path = repo_info.get("graph_path")
        repo_root = repo_info.get("repo_root")
        
        enhanced_question = question
        if current_page_context:
            enhanced_question = f"[当前页面上下文: {current_page_context}]\n\n用户问题: {question}"

        async def event_generator():
            runner = AgentRunner(
                vector_store_path=vector_store_path,
                graph_path=graph_path,
                repo_root=repo_root,
                max_iterations=5
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
                    supabase_client.add_chat_message(chat_id, "assistant", answer, metadata)

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
        raise HTTPException(status_code=500, detail=f"Agent 流式问答失败: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
