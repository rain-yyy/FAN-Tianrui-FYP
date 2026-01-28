from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import sys
from pathlib import Path
import json
import uuid
import asyncio
import shutil
import hashlib
import os
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum

# 获取项目根目录并添加到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入必要的模块
from scripts.setup_repository import setup_repository
from src.config import CONFIG_PATH, load_config
from src.ingestion.file_processor import generate_file_tree, get_files_to_process, split_code_and_text_files
from src.ingestion.docu_splitter import load_and_split_docs
from src.ingestion.vector_store import create_and_save_vector_store
from src.wiki.struct_gen import generate_wiki_structure
from src.wiki.content_gen import WikiContentGenerator
from src.clients.ai_client_factory import get_ai_client
from src.storage.r2_client import upload_wiki_to_r2
from src.core.chat import answer_question

app = FastAPI(
    title="Project Wiki Generation API",
    description="将项目仓库 URL 转换为 Wiki 结构和内容的 API（异步任务模式）"
)


# ============ 任务状态管理 ============

class TaskStatus(str, Enum):
    PENDING = "pending"       # 任务已创建，等待执行
    PROCESSING = "processing" # 任务正在执行
    COMPLETED = "completed"   # 任务已完成
    FAILED = "failed"         # 任务执行失败


class TaskInfo(BaseModel):
    task_id: str
    status: TaskStatus
    progress: float = 0.0           # 进度百分比 (0-100)
    current_step: str = ""          # 当前步骤描述
    created_at: datetime
    updated_at: datetime
    result: Optional[Dict[str, Any]] = None  # 任务完成后的结果
    error: Optional[str] = None              # 错误信息


# 内存中的任务存储（生产环境建议使用 Redis 等持久化存储）
tasks_store: Dict[str, TaskInfo] = {}

# 配置 CORS，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境下允许所有来源，生产环境应指定具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 默认路径配置
DEFAULT_OUTPUT_PATH: Path = PROJECT_ROOT / "wiki_structure.json"
DEFAULT_WIKI_SECTION_JSON_OUTPUT: Path = PROJECT_ROOT / "wiki_section_json"

# Vector store 根目录 (Fly.io 持久化卷挂载点或本地开发目录)
VECTOR_STORE_ROOT: Path = Path(os.getenv("VECTOR_STORE_PATH", str(PROJECT_ROOT / "vector_stores")))

# 存储 repo_url -> vector_store_path 的映射 (生产环境建议持久化到数据库)
repo_vector_store_mapping: Dict[str, str] = {}


def _get_repo_hash(repo_url: str) -> str:
    """根据仓库 URL 生成唯一的短哈希标识"""
    # 清理 URL
    clean_url = repo_url.rstrip('/').replace('.git', '').lower()
    # 提取仓库名称
    repo_name = clean_url.split('/')[-1] if '/' in clean_url else clean_url
    # 生成短哈希
    url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:8]
    return f"{repo_name}_{url_hash}"


class GenRequest(BaseModel):
    url_link: str


class ChatRequest(BaseModel):
    """RAG 聊天请求"""
    question: str = Field(..., min_length=1, description="用户问题")
    repo_url: str = Field(..., description="仓库 URL，用于定位对应的向量库")
    conversation_history: Optional[List[Dict[str, str]]] = Field(
        default=None, 
        description="对话历史 [{'role': 'user'/'assistant', 'content': '...'}]"
    )
    current_page_context: Optional[str] = Field(
        default=None, 
        description="当前浏览页面的上下文信息"
    )


class ChatResponse(BaseModel):
    """RAG 聊天响应"""
    answer: str
    sources: List[str]
    repo_url: str


class GenResponse(BaseModel):
    """任务完成后的结果"""
    r2_structure_url: str | None = None  # R2 中 wiki_structure.json 的 URL
    r2_content_urls: list[str] | None = None  # R2 中 content 目录的所有文件 URL
    json_wiki: str | None = None  # 保留旧字段以兼容
    json_content: str | None = None  # 保留旧字段以兼容
    vector_store_path: str | None = None  # RAG 向量库路径
    repo_url: str | None = None  # 仓库 URL（用于聊天接口）


class TaskCreateResponse(BaseModel):
    """创建任务后返回的响应"""
    task_id: str
    message: str = "任务已创建，正在后台处理"


class TaskStatusResponse(BaseModel):
    """任务状态查询响应"""
    task_id: str
    status: TaskStatus
    progress: float
    current_step: str
    created_at: datetime
    updated_at: datetime
    result: Optional[GenResponse] = None
    error: Optional[str] = None

def update_task_progress(task_id: str, progress: float, current_step: str):
    """更新任务进度"""
    if task_id in tasks_store:
        tasks_store[task_id].progress = progress
        tasks_store[task_id].current_step = current_step
        tasks_store[task_id].updated_at = datetime.now()


def run_structure_generation(
    repo_url_or_path: str, config_path: Path, output_path: Path,
    task_id: Optional[str] = None
) -> tuple[str, Dict[str, Any]]:
    """
    根据仓库地址（Git URL 或本地路径）生成 wiki 目录结构，并保存到指定文件。
    
    流程包括：
    1. 克隆/读取仓库
    2. 生成文件树
    3. 构建代码知识图谱 (CKG) 并运行 Leiden 社区发现算法
    4. 生成社区摘要
    5. 生成 Wiki 目录结构
    """
    if task_id:
        update_task_progress(task_id, 5, "正在克隆/读取仓库...")

    if repo_url_or_path.startswith(("http://", "https://", "git@")):
        repo_path = setup_repository(repo_url_or_path)
    else:
        repo_path = repo_url_or_path

    if task_id:
        update_task_progress(task_id, 15, "仓库准备完成，正在加载配置...")

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")

    # 加载配置（用于验证配置文件有效性）
    _ = load_config(str(config_path))

    if task_id:
        update_task_progress(task_id, 20, "正在生成文件树...")

    file_tree = generate_file_tree(repo_path, str(config_path))

    if task_id:
        update_task_progress(task_id, 25, "正在构建代码知识图谱与社区分析...")

    # generate_wiki_structure 内部会执行以下步骤：
    # - 构建 RepoMap 上下文
    # - 使用 Tree-sitter 构建代码知识图谱 (CodeGraphBuilder)
    # - 运行 Leiden 社区发现算法 (CommunityEngine)
    # - 生成社区摘要
    # - 调用 LLM 生成 Wiki 目录结构
    
    if task_id:
        update_task_progress(task_id, 30, "正在生成 Wiki 目录结构（包含 GraphRAG 社区分析）...")

    wiki_structure = generate_wiki_structure(repo_path, file_tree)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(wiki_structure, f, indent=2, ensure_ascii=False)

    if task_id:
        update_task_progress(task_id, 40, "Wiki 目录结构生成完成")

    return repo_path, wiki_structure


def run_wiki_content_generation(
    repo_path: str,
    wiki_structure: Dict[str, Any],
    json_output_dir: Path,
    task_id: Optional[str] = None
) -> list[Path]:
    """
    调用 AI 客户端，根据 wiki 目录逐条生成内容与 Mermaid 图，并写入 JSON。
    """
    if task_id:
        update_task_progress(task_id, 45, "正在初始化 AI 客户端...")

    client = get_ai_client("qwen")
    generator = WikiContentGenerator(
        repo_root=repo_path,
        json_output_dir=json_output_dir,
        client=client,
    )

    if task_id:
        update_task_progress(task_id, 50, "正在生成 Wiki 内容（此步骤可能耗时较长）...")

    result = generator.generate(wiki_structure)

    if task_id:
        update_task_progress(task_id, 85, "Wiki 内容生成完成")

    return result


def run_rag_indexing(
    repo_path: str,
    repo_url: str,
    config_path: Path,
    task_id: Optional[str] = None
) -> str:
    """
    为仓库创建 RAG 向量索引（代码和文本分类）
    
    Args:
        repo_path: 本地仓库路径
        repo_url: 仓库 URL（用于生成唯一标识）
        config_path: 配置文件路径
        task_id: 任务 ID（用于进度更新）
    
    Returns:
        向量库根目录路径
    """
    if task_id:
        update_task_progress(task_id, 86, "正在构建 RAG 向量索引...")
    
    # 生成仓库唯一标识
    repo_hash = _get_repo_hash(repo_url)
    vector_store_path = VECTOR_STORE_ROOT / repo_hash
    
    # 确保目录存在
    vector_store_path.mkdir(parents=True, exist_ok=True)
    
    # 获取需要处理的文件
    config = load_config(str(config_path))
    all_files = get_files_to_process(repo_path, str(config_path))
    
    if not all_files:
        print("[RAG] 没有找到需要索引的文件")
        return str(vector_store_path)
    
    # 分离代码和文本文件
    code_files, text_files = split_code_and_text_files(all_files, config)
    
    print(f"[RAG] 找到 {len(code_files)} 个代码文件, {len(text_files)} 个文本文件")
    
    # 处理代码文件
    if code_files:
        if task_id:
            update_task_progress(task_id, 87, f"正在索引 {len(code_files)} 个代码文件...")
        
        code_docs = load_and_split_docs(code_files)
        if code_docs:
            code_store_path = str(vector_store_path / "code")
            create_and_save_vector_store(code_docs, code_store_path)
            print(f"[RAG] 代码向量库已保存: {code_store_path}")
    
    # 处理文本文件
    if text_files:
        if task_id:
            update_task_progress(task_id, 88, f"正在索引 {len(text_files)} 个文本文件...")
        
        text_docs = load_and_split_docs(text_files)
        if text_docs:
            text_store_path = str(vector_store_path / "text")
            create_and_save_vector_store(text_docs, text_store_path)
            print(f"[RAG] 文本向量库已保存: {text_store_path}")
    
    # 更新映射
    repo_vector_store_mapping[repo_url] = str(vector_store_path)
    
    if task_id:
        update_task_progress(task_id, 89, "RAG 向量索引构建完成")
    
    print(f"[RAG] 向量库构建完成: {vector_store_path}")
    return str(vector_store_path)


def cleanup_local_files(repo_path: Optional[str], output_path: Path, json_output_dir: Path):
    """
    清理本地生成的临时文件，释放存储空间
    
    - repo_path: 克隆的仓库目录
    - output_path: wiki_structure.json 文件路径
    - json_output_dir: wiki_section_json 目录路径
    
    注意：不清理向量库目录，因为它需要被 /chat 接口使用
    """
    # 清理克隆的仓库目录
    if repo_path and Path(repo_path).exists():
        try:
            shutil.rmtree(repo_path)
            print(f"[清理] 已删除克隆的仓库目录: {repo_path}")
        except Exception as e:
            print(f"[清理警告] 删除仓库目录失败: {repo_path}, 错误: {e}")

    # 清理生成的 wiki_structure.json
    if output_path.exists():
        try:
            output_path.unlink()
            print(f"[清理] 已删除 wiki_structure.json: {output_path}")
        except Exception as e:
            print(f"[清理警告] 删除 wiki_structure.json 失败: {output_path}, 错误: {e}")

    # 清理生成的 wiki_section_json 目录
    if json_output_dir.exists():
        try:
            shutil.rmtree(json_output_dir)
            print(f"[清理] 已删除 wiki_section_json 目录: {json_output_dir}")
        except Exception as e:
            print(f"[清理警告] 删除 wiki_section_json 目录失败: {json_output_dir}, 错误: {e}")


async def execute_generation_task(task_id: str, url_link: str):
    """
    后台异步执行 Wiki 生成任务
    """
    repo_path: Optional[str] = None
    output_path: Optional[Path] = None
    json_output_dir: Optional[Path] = None

    try:
        # 更新状态为处理中
        tasks_store[task_id].status = TaskStatus.PROCESSING
        tasks_store[task_id].updated_at = datetime.now()

        # 获取默认路径
        config_path = CONFIG_PATH.expanduser().resolve()
        output_path = DEFAULT_OUTPUT_PATH.expanduser().resolve()
        json_output_dir = DEFAULT_WIKI_SECTION_JSON_OUTPUT.expanduser().resolve()

        # 1. 生成项目结构 (wiki_structure.json)
        # 使用 run_in_executor 让同步代码在线程池中执行，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        repo_path, wiki_structure = await loop.run_in_executor(
            None,
            lambda: run_structure_generation(
                repo_url_or_path=url_link,
                config_path=config_path,
                output_path=output_path,
                task_id=task_id
            )
        )

        print(wiki_structure)

        # TODO: 测试用截断,用于测试文件路径错误的问题（已修复）

        
        # 2. 生成 Wiki 内容和对应的 JSON 详情
        await loop.run_in_executor(
            None,
            lambda: run_wiki_content_generation(
                repo_path=repo_path,
                wiki_structure=wiki_structure,
                json_output_dir=json_output_dir,
                task_id=task_id
            )
        )

        # 3. 构建 RAG 向量索引（用于聊天问答）
        vector_store_path = await loop.run_in_executor(
            None,
            lambda: run_rag_indexing(
                repo_path=repo_path,
                repo_url=url_link,
                config_path=config_path,
                task_id=task_id
            )
        )

        update_task_progress(task_id, 90, "正在上传到 R2 存储...")

        # 3. 上传到 R2 存储
        r2_structure_url, r2_content_urls = await loop.run_in_executor(
            None,
            lambda: upload_wiki_to_r2(
                repo_url=url_link,
                wiki_structure=wiki_structure,
                structure_local_path=output_path,
                content_dir=json_output_dir,
            )
        )

        # 更新任务为完成状态
        tasks_store[task_id].status = TaskStatus.COMPLETED
        tasks_store[task_id].progress = 100
        tasks_store[task_id].current_step = "任务完成"
        tasks_store[task_id].result = {
            "r2_structure_url": r2_structure_url,
            "r2_content_urls": r2_content_urls,
            "json_wiki": str(output_path) if not r2_structure_url else None,
            "json_content": str(json_output_dir) if not r2_content_urls else None,
            "vector_store_path": vector_store_path,  # RAG 向量库路径
            "repo_url": url_link,  # 仓库 URL（用于聊天接口）
        }
        tasks_store[task_id].updated_at = datetime.now()

    except Exception as e:
        import traceback
        traceback.print_exc()
        # 更新任务为失败状态
        tasks_store[task_id].status = TaskStatus.FAILED
        tasks_store[task_id].error = str(e)
        tasks_store[task_id].current_step = "任务失败"
        tasks_store[task_id].updated_at = datetime.now()

    finally:
        # 无论成功还是失败，都清理本地文件以释放存储空间
        # 不再更新状态给用户，后台静默清理
        if output_path and json_output_dir:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: cleanup_local_files(repo_path, output_path, json_output_dir)
            )
        print(f"[任务 {task_id}] 本地文件清理完成")

@app.post("/generate", response_model=TaskCreateResponse)
async def generate_wiki(request: GenRequest):
    """
    创建 Wiki 生成任务（异步）

    - 接收仓库 URL，创建后台任务
    - 立即返回任务 ID，无需等待任务完成
    - 使用 /task/{task_id} 接口查询任务进度和结果
    """
    try:
        # 生成唯一任务 ID
        task_id = str(uuid.uuid4())
        now = datetime.now()

        # 创建任务记录
        tasks_store[task_id] = TaskInfo(
            task_id=task_id,
            status=TaskStatus.PENDING,
            progress=0,
            current_step="任务已创建，等待处理",
            created_at=now,
            updated_at=now,
        )

        # 启动后台任务（不阻塞响应）
        asyncio.create_task(execute_generation_task(task_id, request.url_link))

        return TaskCreateResponse(
            task_id=task_id,
            message="任务已创建，正在后台处理。请使用 /task/{task_id} 查询进度。"
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    查询任务状态和进度

    - task_id: 任务 ID（由 /generate 接口返回）
    - 返回任务的当前状态、进度、结果或错误信息
    """
    if task_id not in tasks_store:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = tasks_store[task_id]

    # 构建响应
    response = TaskStatusResponse(
        task_id=task.task_id,
        status=task.status,
        progress=task.progress,
        current_step=task.current_step,
        created_at=task.created_at,
        updated_at=task.updated_at,
        error=task.error,
    )

    # 如果任务完成，包含结果
    if task.status == TaskStatus.COMPLETED and task.result:
        response.result = GenResponse(**task.result)

    return response


@app.get("/tasks", response_model=list[TaskStatusResponse])
async def list_tasks():
    """
    列出所有任务（用于调试和管理）
    """
    result = []
    for task in tasks_store.values():
        response = TaskStatusResponse(
            task_id=task.task_id,
            status=task.status,
            progress=task.progress,
            current_step=task.current_step,
            created_at=task.created_at,
            updated_at=task.updated_at,
            error=task.error,
        )
        if task.status == TaskStatus.COMPLETED and task.result:
            response.result = GenResponse(**task.result)
        result.append(response)
    return result


@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """
    删除已完成或失败的任务记录

    - 只能删除已完成（completed）或失败（failed）的任务
    - 正在处理中的任务不能删除
    """
    if task_id not in tasks_store:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    task = tasks_store[task_id]
    if task.status in [TaskStatus.PENDING, TaskStatus.PROCESSING]:
        raise HTTPException(
            status_code=400,
            detail="不能删除正在处理中或等待中的任务"
        )

    del tasks_store[task_id]
    return {"message": f"任务 {task_id} 已删除"}

@app.get("/health")
async def health_check():
    return {"status": "ok"}


# ============ RAG Chat 接口 ============

@app.post("/chat", response_model=ChatResponse)
async def chat_with_repo(request: ChatRequest):
    """
    RAG 问答接口
    
    基于已生成的向量库回答用户关于项目的问题。
    
    - question: 用户问题
    - repo_url: 仓库 URL，用于定位对应的向量库
    - conversation_history: 可选的对话历史（用于多轮对话）
    - current_page_context: 可选的当前页面上下文
    """
    try:
        # 1. 查找向量库路径
        vector_store_path = repo_vector_store_mapping.get(request.repo_url)
        
        # 如果映射中没有，尝试根据 URL 推断路径
        if not vector_store_path:
            repo_hash = _get_repo_hash(request.repo_url)
            inferred_path = VECTOR_STORE_ROOT / repo_hash
            if inferred_path.exists():
                vector_store_path = str(inferred_path)
                repo_vector_store_mapping[request.repo_url] = vector_store_path
        
        if not vector_store_path or not Path(vector_store_path).exists():
            raise HTTPException(
                status_code=404,
                detail=f"未找到该仓库的向量索引。请先通过 /generate 接口生成文档。"
            )
        
        # 2. 构建增强问题（包含上下文信息）
        enhanced_question = request.question
        if request.current_page_context:
            enhanced_question = f"[当前页面上下文: {request.current_page_context}]\n\n用户问题: {request.question}"
        
        # 3. 执行 RAG 问答
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: answer_question(
                db_path=vector_store_path,
                question=enhanced_question,
            )
        )
        
        return ChatResponse(
            answer=str(result.get("answer", "")),
            sources=list(result.get("sources", [])),
            repo_url=request.repo_url
        )
    
    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"RAG 问答失败: {str(e)}")


@app.get("/chat/repos")
async def list_available_repos():
    """
    列出所有可用于聊天的仓库
    
    返回已建立向量索引的仓库列表
    """
    available_repos = []
    
    # 从映射中获取
    for repo_url, path in repo_vector_store_mapping.items():
        if Path(path).exists():
            available_repos.append({
                "repo_url": repo_url,
                "vector_store_path": path,
                "has_code_index": (Path(path) / "code" / "index.faiss").exists(),
                "has_text_index": (Path(path) / "text" / "index.faiss").exists(),
            })
    
    # 扫描 vector_stores 目录查找未在映射中的索引
    if VECTOR_STORE_ROOT.exists():
        for subdir in VECTOR_STORE_ROOT.iterdir():
            if subdir.is_dir() and str(subdir) not in [r["vector_store_path"] for r in available_repos]:
                code_exists = (subdir / "code" / "index.faiss").exists()
                text_exists = (subdir / "text" / "index.faiss").exists()
                if code_exists or text_exists:
                    available_repos.append({
                        "repo_url": None,  # 未知原始 URL
                        "vector_store_path": str(subdir),
                        "repo_hash": subdir.name,
                        "has_code_index": code_exists,
                        "has_text_index": text_exists,
                    })
    
    return {"repos": available_repos, "count": len(available_repos)}


if __name__ == "__main__":
    import uvicorn
    # 建议使用 uvicorn scripts.api:app --reload 进行开发
    uvicorn.run(app, host="0.0.0.0", port=8000)
