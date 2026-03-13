import sys
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

from src.storage.supabase_client import SupabaseClient

# 初始化日志
from src.utils.logger import setup_logger
logger = setup_logger("api")

# 导入业务逻辑和管理模块
from src.core.wiki_pipeline import execute_generation_task, VECTOR_STORE_ROOT
from src.core.chat import answer_question

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


# ============ API Endpoints ============

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
    logger.info(f"查询任务信息: {task_id}")

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

    logger.info(f"列出所有任务: {user_id}")
    supabase_client = SupabaseClient()
    all_tasks = supabase_client.get_all_tasks(user_id)
    return {"tasks": all_tasks}


@app.delete("/task/{task_id}")
async def delete_task_api(task_id: str):
    """
    删除已完成或失败的任务记录
    """
    logger.info(f"删除任务: {task_id}")
    supabase_client = SupabaseClient()
    success = supabase_client.delete_task(task_id)
    if not success:
        # Check if task exists first
        task = supabase_client.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        else:
            raise HTTPException(status_code=400, detail="不能删除正在处理中的任务")

    return {"message": f"任务 {task_id} 已删除"}


@app.get("/health")
async def health_check():
    return {"status": "ok"}


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
            chat_session = supabase_client.create_chat_history(user_id, repo_url)
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
        
        vector_store_path = repo_info["vector_store_path"]
        
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
