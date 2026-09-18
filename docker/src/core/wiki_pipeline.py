import os
import json
import asyncio
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple

# 导入必要的模块
from scripts.setup_repository import setup_repository
from src.config import CONFIG_PATH
from src.ingestion.file_processor import generate_file_tree, get_files_to_process, split_code_and_text_files
from src.ingestion.docu_splitter import load_and_split_docs
from src.ingestion.vector_store import upsert_vector_store
from src.wiki.struct_gen import generate_wiki_structure
from src.wiki.content_gen import WikiContentGenerator
from src.storage.r2_client import upload_wiki_to_r2
from src.storage.supabase_client import update_repo_vector_path, SupabaseClient, SupabaseStorageError
from scripts.setup_repository import get_repo_disk_directory_name
from src.utils.wiki_cache_policy import wiki_generation_cache_is_stale, WIKI_GENERATION_CACHE_MAX_AGE_DAYS

# 任务状态定义 (保持与 api.py 一致)
class TaskStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CACHED = "cached"
    FAILED = "failed"

logger = logging.getLogger("api")

# 项目根目录获取
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# 任务级工作目录根路径
TASK_WORK_ROOT: Path = Path(os.getenv("TASK_WORK_PATH", str(PROJECT_ROOT / "task_workdirs")))


def _task_output_dir(task_id: str) -> Path:
    """返回 task_id 专属的工作目录，确保不同任务的输出互不干扰"""
    d = TASK_WORK_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _persist_graphrag_communities_to_vector_store(repo_url: str, source_json: Path) -> None:
    """
    在 Wiki 流水线早期把 GraphRAG 元数据复制到向量库根目录。

    避免 run_rag_indexing 中途失败或 finally 清理任务目录时，仅存于 task_dir 的 JSON 丢失；
    后台 RAG 重试时也能在向量库路径下找到该文件（无需再传 communities_json_path）。
    """
    if not source_json.is_file():
        return
    # TODO repo name 应该在pipeline中一直传递的，和url一起，而不是每次都要处理一遍
    repo_dir = get_repo_disk_directory_name(repo_url)
    dest_root = VECTOR_STORE_ROOT / repo_dir
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / "graphrag_communities.json"
        shutil.copy2(source_json, dest)
        logger.info("[GraphRAG] 元数据已写入向量库目录（结构阶段）: %s", dest)
    except OSError as exc:
        logger.warning("[GraphRAG] 结构阶段写入向量库失败: %s", exc)


def _persist_code_graph_to_vector_store(repo_url: str, source_json: Path) -> Optional[str]:
    """
    在 Wiki 流水线早期把 code_graph.json 复制到向量库根目录。

    与 _persist_graphrag_communities_to_vector_store 策略相同：RAG 失败或任务目录被清理时，
    向量库目录下已有备份可供 Agent 的 CodeGraphTool 直接加载。

    Returns:
        本地目标路径字符串（供 Supabase graph_path 写入），失败时返回 None。
    """
    if not source_json.is_file():
        return None
    repo_dir = get_repo_disk_directory_name(repo_url)
    dest_root = VECTOR_STORE_ROOT / repo_dir
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / "code_graph.json"
        if source_json.resolve() != dest.resolve():
            shutil.copy2(source_json, dest)
        logger.info("[CodeGraph] code_graph.json 已写入向量库目录（结构阶段）: %s", dest)
        return str(dest)
    except OSError as exc:
        logger.warning("[CodeGraph] 结构阶段写入向量库失败: %s", exc)
        return None

# 持久化数据默认在仓库根下 data/（与 docker/ 同级）；Fly/Compose 挂载 /data 时由环境变量覆盖
_DATA_ROOT_DEFAULT = PROJECT_ROOT.parent / "data"
VECTOR_STORE_ROOT: Path = Path(
    os.getenv("VECTOR_STORE_PATH", str(_DATA_ROOT_DEFAULT / "vector_stores"))
)
REPO_STORE_ROOT: Path = Path(
    os.getenv("REPO_STORE_PATH", str(_DATA_ROOT_DEFAULT / "repos"))
).expanduser()


def _task_marked_cancelled_by_user(supabase_client: SupabaseClient, task_id: str) -> bool:
    """
    用户已通过 /cancel 将任务标为 failed（含 Cancelled）时返回 True，
    避免后台在长时间 run_in_executor 结束后把状态写回 completed 覆盖取消结果。
    """
    try:
        row = supabase_client.get_task(task_id)
    except SupabaseStorageError:
        return False
    if not row:
        return False
    if row.status != TaskStatus.FAILED:
        return False
    err = (row.error or "").lower()
    return "cancel" in err


def _update_progress(task_id: Optional[str], progress: float, step: str) -> None:
    """同步更新任务进度到 Supabase"""
    if task_id:
        try:
            success = SupabaseClient().update_task_progress(task_id, progress, step)
            if not success:
                logger.warning(f"Task {task_id} not found (likely deleted), aborting...")
                raise InterruptedError(f"Task {task_id} was deleted.")
        except Exception as e:
            if isinstance(e, InterruptedError):
                raise
            logger.warning(f"更新任务进度失败: {e}")


def run_structure_generation(
    repo_url: str, config_path: Path, output_path: Path,
    task_id: Optional[str] = None,
    code_graph_persist_path: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    根据仓库地址生成 wiki 目录结构，并保存到指定文件。

    code_graph_persist_path: 在构建代码图时同步将图保存为 NetworkX node-link JSON。
    """
    _update_progress(task_id, 5, "Cloning/reading repository...")

    repo_path = setup_repository(repo_url)

    _update_progress(task_id, 20, "Generating file tree...")

    file_tree = generate_file_tree(repo_path)

    _update_progress(task_id, 30, "Generating Wiki structure (including GraphRAG + code graph build)...")

    communities_path = str((output_path.parent / "graphrag_communities.json").resolve())
    wiki_structure = generate_wiki_structure(
        repo_path,
        file_tree,
        communities_persist_path=communities_path,
        code_graph_persist_path=code_graph_persist_path,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(wiki_structure, f, indent=2, ensure_ascii=False)

    _update_progress(task_id, 40, "Wiki structure generation completed")

    return repo_path, wiki_structure


def run_wiki_content_generation(
    repo_path: str,
    wiki_structure: Dict[str, Any],
    json_output_dir: Path,
    task_id: Optional[str] = None
) -> List[Path]:
    """
    调用 AI 客户端，根据 wiki 目录并发生成内容与 Mermaid 图，并写入 JSON。
    """
    _update_progress(task_id, 45, "Initializing AI chain...")

    def _progress_cb(progress: float, step: str):
        _update_progress(task_id, progress, step)

    generator = WikiContentGenerator(
        repo_root=repo_path,
        json_output_dir=json_output_dir,
        progress_callback=_progress_cb if task_id else None,
        task_id=task_id,
    )

    _update_progress(task_id, 50, "Generating Wiki content concurrently...")

    result = generator.generate(wiki_structure)

    _update_progress(task_id, 85, "Wiki content generation completed")

    return result


def run_rag_indexing(
    repo_path: str,
    repo_url: str,
    config_path: Path,
    task_id: Optional[str] = None,
    communities_json_path: Optional[str] = None,
    code_graph_json_path: Optional[str] = None,
) -> str:
    """
    为仓库创建 RAG 向量索引（代码和文本分类）
    """
    _update_progress(task_id, 88, "Building RAG vector index...")
    
    repo_dir = get_repo_disk_directory_name(repo_url)
    vector_store_path = VECTOR_STORE_ROOT / repo_dir
    
    # 确保目录存在
    vector_store_path.mkdir(parents=True, exist_ok=True)
    
    # 获取需要处理的文件
    all_files = get_files_to_process(repo_path)

    if not all_files:
        logger.warning("[RAG] No files found to index")
        return str(vector_store_path)

    # 分离代码和文本文件
    code_files, text_files = split_code_and_text_files(all_files)
    
    logger.info(f"[RAG] Found {len(code_files)} code files, {len(text_files)} text files")
    
    # debug 输出目录放在向量库路径下，保证不同任务互相隔离
    chunk_debug_dir = vector_store_path / "chunk_debug"

    # 处理代码文件
    if code_files:  
        _update_progress(task_id, 89, f"Indexing {len(code_files)} code files...")
        
        code_docs = load_and_split_docs(
            code_files,
            debug_output_path=str(chunk_debug_dir / "code_chunks.jsonl"),
        )
        if code_docs:
            upsert_vector_store(code_docs, repo_id=repo_dir, category="code")
            logger.info(f"[RAG] Code chunks upserted to Qdrant: repo_id={repo_dir}, count={len(code_docs)}")
    
    # 处理文本文件
    if text_files:
        _update_progress(task_id, 90, f"Indexing {len(text_files)} text files...")
        
        text_docs = load_and_split_docs(
            text_files,
            debug_output_path=str(chunk_debug_dir / "text_chunks.jsonl"),
        )
        if text_docs:
            upsert_vector_store(text_docs, repo_id=repo_dir, category="text")
            logger.info(f"[RAG] Text chunks upserted to Qdrant: repo_id={repo_dir}, count={len(text_docs)}")
    
    if communities_json_path:
        src = Path(communities_json_path).expanduser().resolve()
        if src.is_file():
            dest = (vector_store_path / "graphrag_communities.json").resolve()
            try:
                if src == dest:
                    logger.info("[RAG] GraphRAG metadata already in vector store directory, skipping copy: %s", dest)
                else:
                    shutil.copy2(src, dest)
                    logger.info("[RAG] GraphRAG metadata copied to %s", dest)
            except OSError as copy_exc:
                logger.warning("[RAG] Failed to copy GraphRAG metadata: %s", copy_exc)

    if code_graph_json_path:
        src_cg = Path(code_graph_json_path).expanduser().resolve()
        if src_cg.is_file():
            dest_cg = (vector_store_path / "code_graph.json").resolve()
            try:
                if src_cg == dest_cg:
                    logger.info("[CodeGraph] Already in vector store directory, skipping copy: %s", dest_cg)
                else:
                    shutil.copy2(src_cg, dest_cg)
                    logger.info("[CodeGraph] code_graph.json copied to %s", dest_cg)
            except OSError as copy_exc:
                logger.warning("[CodeGraph] Failed to copy code_graph.json: %s", copy_exc)

    # 同步到 Supabase
    try:
        update_repo_vector_path(repo_url, str(vector_store_path))
    except Exception as e:
        logger.error(f"[Supabase] Failed to sync vector path: {e}")
    
    _update_progress(task_id, 91, "RAG vector index construction completed")
    
    logger.info(f"[RAG] Vector store construction completed: {vector_store_path}")
    return str(vector_store_path)


def cleanup_local_files(repo_path: Optional[str], output_path: Path, json_output_dir: Path):
    """
    清理本地生成的临时文件，释放存储空间。
    如果 output_path 和 json_output_dir 位于同一个 task 工作目录，则直接清理整个工作目录。
    """
    if repo_path and Path(repo_path).exists():
        try:
            repo_path_obj = Path(repo_path).expanduser().resolve()
            repo_store_root = REPO_STORE_ROOT.resolve()
            is_persistent_repo = False
            try:
                repo_path_obj.relative_to(repo_store_root)
                is_persistent_repo = True
            except ValueError:
                is_persistent_repo = False

            if is_persistent_repo:
                logger.info(f"[清理] 跳过删除持久化仓库目录: {repo_path_obj}")
            else:
                shutil.rmtree(repo_path_obj)
                logger.info(f"[清理] 已删除克隆的仓库目录: {repo_path_obj}")
        except Exception as e:
            logger.warning(f"[清理警告] 删除仓库目录失败: {repo_path}, 错误: {e}")

    # 尝试整体清理 task 工作目录（如果两者同属一个 task_dir）
    task_work_root = TASK_WORK_ROOT.resolve()
    try:
        task_dir = output_path.resolve().parent
        task_dir.relative_to(task_work_root)
        # output_path 位于 task 工作目录内，直接清理整个目录
        if task_dir.exists():
            shutil.rmtree(task_dir)
            logger.info(f"[清理] 已删除任务工作目录: {task_dir}")
        return
    except ValueError:
        pass

    # 回退：分别清理单个文件和目录
    if output_path.exists():
        try:
            output_path.unlink()
            logger.info(f"[清理] 已删除 wiki_structure.json: {output_path}")
        except Exception as e:
            logger.warning(f"[清理警告] 删除 wiki_structure.json 失败: {output_path}, 错误: {e}")

    if json_output_dir.exists():
        try:
            shutil.rmtree(json_output_dir)
            logger.info(f"[清理] 已删除 wiki_section_json 目录: {json_output_dir}")
        except Exception as e:
            logger.warning(f"[清理警告] 删除 wiki_section_json 目录失败: {json_output_dir}, 错误: {e}")


async def _background_retry_rag_indexing(task_id: str, url_link: str, config_path: Path) -> None:
    """
    Wiki 已成功上传后，若 RAG 失败则在后台多次重试索引；成功后合并写回 tasks.result 与 repositories。
    每次重试单独克隆到持久目录，不依赖已清理的任务临时目录。
    """
    supabase_client = SupabaseClient()
    delays_before_attempt_sec = [30, 120, 300]
    loop = asyncio.get_event_loop()
    last_error: Optional[str] = None

    for attempt in range(1, len(delays_before_attempt_sec) + 1):
        if attempt > 1:
            await asyncio.sleep(delays_before_attempt_sec[attempt - 1])
        else:
            await asyncio.sleep(delays_before_attempt_sec[0])

        try:
            task_row = supabase_client.get_task(task_id)
        except SupabaseStorageError as e:
            logger.warning(f"[RAG 重试] Supabase 查询失败，终止后台重试: {e}")
            return
        if not task_row:
            logger.info(f"[RAG 重试] 任务 {task_id} 已不存在，终止后台重试")
            return

        try:

            def _clone_and_index() -> str:
                rp = setup_repository(url_link)
                repo_dir = get_repo_disk_directory_name(url_link)
                vs_dir = VECTOR_STORE_ROOT / repo_dir
                comm = (vs_dir / "graphrag_communities.json").resolve()
                cg = (vs_dir / "code_graph.json").resolve()
                return run_rag_indexing(
                    rp,
                    url_link,
                    config_path,
                    task_id=None,
                    communities_json_path=str(comm) if comm.is_file() else None,
                    code_graph_json_path=str(cg) if cg.is_file() else None,
                )

            vector_store_path = await loop.run_in_executor(None, _clone_and_index)
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[RAG 重试] task={task_id} 第 {attempt} 次失败: {e}")
            continue

        try:
            task_row = supabase_client.get_task(task_id)
        except SupabaseStorageError as e:
            logger.warning(f"[RAG 重试] 写回前 Supabase 查询失败，跳过: {e}")
            return
        if not task_row:
            logger.info(f"[RAG 重试] 任务 {task_id} 在索引成功后已被删除，跳过写回")
            return

        # Derive graph_path from vector_store_path
        _retry_graph_path: Optional[str] = None
        if vector_store_path:
            _cg = Path(vector_store_path) / "code_graph.json"
            if _cg.is_file():
                _retry_graph_path = str(_cg)

        prev = dict(task_row.result)
        prev["vector_store_path"] = vector_store_path
        if _retry_graph_path:
            prev["graph_path"] = _retry_graph_path
        emb = prev.get("embedding") if isinstance(prev.get("embedding"), dict) else {}
        emb = dict(emb)
        emb["status"] = "ready"
        emb["last_error"] = None
        emb["retry_attempts"] = attempt
        emb["ready_at"] = datetime.now(timezone.utc).isoformat()
        prev["embedding"] = emb

        supabase_client.update_task_status(task_id, TaskStatus.COMPLETED, result=prev)

        supabase_client.upsert_repo_wiki_data(
            url_link,
            None,
            None,
            vector_store_path,
            graph_path=_retry_graph_path,
        )
        logger.info(f"[RAG 重试] 成功 task={task_id} vector_store_path={vector_store_path} graph_path={_retry_graph_path}")
        return

    try:
        task_row = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        logger.warning(f"[RAG 重试] 最终状态写回前 Supabase 失败: {e}")
        return
    if not task_row:
        return
    prev = dict(task_row.result)
    emb = prev.get("embedding") if isinstance(prev.get("embedding"), dict) else {}
    emb = dict(emb)
    emb["status"] = "failed"
    emb["last_error"] = last_error
    emb["retry_attempts"] = len(delays_before_attempt_sec)
    emb["failed_at"] = datetime.now(timezone.utc).isoformat()
    prev["embedding"] = emb
    supabase_client.update_task_status(task_id, TaskStatus.COMPLETED, result=prev)
    logger.error(f"[RAG 重试] task={task_id} 已达最大重试次数，embedding 仍为失败")


async def execute_generation_task(task_id: str, url_link: str):
    """
    后台异步执行 Wiki 生成任务
    """
    repo_path: Optional[str] = None
    output_path: Optional[Path] = None
    json_output_dir: Optional[Path] = None
    supabase_client = SupabaseClient()

    try:
        # 更新状态为处理中
        if not supabase_client.update_task_status(task_id, TaskStatus.PROCESSING):
            logger.info(f"Task {task_id} not found (deleted) at start, aborting.")
            return

        # 缓存判断：查询 repositories 表的 last_updated 字段
        # 若记录存在且距今不超过 WIKI_GENERATION_CACHE_MAX_AGE_DAYS 天，则直接返回已有数据
        # TODO 等下检查这里的逻辑，感觉有问题
        repo_info = supabase_client.get_repo_information(url_link)
        if repo_info and not wiki_generation_cache_is_stale(repo_info, WIKI_GENERATION_CACHE_MAX_AGE_DAYS):
            r2_structure_url = repo_info.get("r2_structure_url")
            r2_content_urls = repo_info.get("r2_content_urls")
            if isinstance(r2_content_urls, str):
                r2_content_urls = [r2_content_urls]
            if r2_structure_url and r2_content_urls:
                cached_result = {
                    "r2_structure_url": r2_structure_url,
                    "r2_content_urls": r2_content_urls,
                    "vector_store_path": repo_info.get("vector_store_path"),
                    "repo_url": supabase_client._normalize_repo_url(url_link),
                }
                supabase_client.update_task_progress(task_id, 100.0, "Cache hit — loaded existing docs")
                supabase_client.update_task_status(task_id, TaskStatus.CACHED, result=cached_result)
                logger.info(
                    "缓存命中 (last_updated 在 %d 天内)，跳过重新生成: task=%s repo=%s",
                    WIKI_GENERATION_CACHE_MAX_AGE_DAYS, task_id, url_link,
                )
                return

        # 构建任务级隔离路径
        config_path = CONFIG_PATH.expanduser().resolve()
        task_dir = _task_output_dir(task_id) #临时工作目录
        output_path = (task_dir / "wiki_structure.json").resolve()
        json_output_dir = (task_dir / "wiki_section_json").resolve()
        code_graph_path = (task_dir / "code_graph.json").resolve()

        # 1. 生成项目结构 (wiki_structure.json) + 构建代码图 (code_graph.json)
        loop = asyncio.get_event_loop()
        repo_path, wiki_structure = await loop.run_in_executor(
            None,
            lambda: run_structure_generation(
                repo_url=url_link,
                config_path=config_path,
                output_path=output_path,
                task_id=task_id,
                code_graph_persist_path=str(code_graph_path),
            )
        )
        await asyncio.sleep(0)

        graphrag_json_path = (output_path.parent / "graphrag_communities.json").resolve()

        # 尽早将 graphrag_communities.json 和 code_graph.json 写入向量库目录，
        # 保证即使后续 RAG 或上传失败，本地副本依然存在供 Agent 使用。
        await loop.run_in_executor(
            None,
            lambda: _persist_graphrag_communities_to_vector_store(url_link, graphrag_json_path),
        )
        graph_path: Optional[str] = await loop.run_in_executor(
            None,
            lambda: _persist_code_graph_to_vector_store(url_link, code_graph_path),
        )
        await asyncio.sleep(0)

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
        await asyncio.sleep(0)
        
        # 3. 先上传 R2，避免仅因 RAG/embedding 失败导致 Wiki 成果未持久化
        _update_progress(task_id, 86, "Uploading to R2 storage...")
        r2_structure_url, r2_content_urls, r2_graphrag_url, r2_code_graph_url = await loop.run_in_executor(
            None,
            lambda gp=graphrag_json_path, cgp=code_graph_path: upload_wiki_to_r2(
                repo_url=url_link,
                wiki_structure=wiki_structure,
                structure_local_path=output_path,
                content_dir=json_output_dir,
                task_id=task_id,
                graphrag_local_path=gp if gp.is_file() else None,
                code_graph_local_path=cgp if cgp.is_file() else None,
            )
        )
        await asyncio.sleep(0)
        
        # 4. 构建 RAG 向量索引（失败不推翻已完成的上传与任务）
        vector_store_path: Optional[str] = None
        embedding_error: Optional[str] = None
        try:
            vector_store_path = await loop.run_in_executor(
                None,
                lambda: run_rag_indexing(
                    repo_path=repo_path,
                    repo_url=url_link,
                    config_path=config_path,
                    task_id=task_id,
                    communities_json_path=str(graphrag_json_path),
                    code_graph_json_path=str(code_graph_path) if code_graph_path.is_file() else None,
                )
            )
            # RAG 成功后从向量库目录确认 graph_path（优先以向量库副本为准）
            if vector_store_path:
                _vsp_cg = Path(vector_store_path) / "code_graph.json"
                if _vsp_cg.is_file():
                    graph_path = str(_vsp_cg)
        except Exception as rag_exc:
            embedding_error = str(rag_exc)
            logger.exception(
                "Task %s Wiki uploaded to R2, but RAG vector indexing failed. Marking as completed and scheduling background retry: %s",
                task_id,
                rag_exc,
            )

        await asyncio.sleep(0)

        result: Dict[str, Any] = {
            "r2_structure_url": r2_structure_url,
            "r2_content_urls": r2_content_urls,
            "r2_graphrag_url": r2_graphrag_url,
            "r2_code_graph_url": r2_code_graph_url,
            "json_wiki": str(output_path) if not r2_structure_url else None,
            "json_content": str(json_output_dir) if not r2_content_urls else None,
            "vector_store_path": vector_store_path,
            "graph_path": graph_path,
            "repo_url": url_link,
            "embedding": {
                "status": "ready" if embedding_error is None else "retry_scheduled",
                "last_error": None if embedding_error is None else embedding_error,
            },
        }
        if embedding_error is not None:
            result["embedding"]["message"] = (
                "Wiki generated and uploaded; vector indexing failed, system will retry in background."
            )

        if _task_marked_cancelled_by_user(supabase_client, task_id):
            logger.info(f"任务 {task_id} 已被用户取消，跳过写入完成状态")
            return

        supabase_client.update_task_status(task_id, TaskStatus.COMPLETED, result=result)

        logger.info(
            "任务 %s Wiki 流程结束（embedding 成功=%s, graph_path=%s）",
            task_id,
            embedding_error is None,
            graph_path,
        )

        success = supabase_client.upsert_repo_wiki_data(
            url_link,
            r2_structure_url,
            r2_content_urls,
            vector_store_path if embedding_error is None else None,
            graph_path=graph_path,
        )
        if not success:
            logger.error(f"同步supabase repository表失败: {url_link}")

        if embedding_error is not None:
            asyncio.create_task(
                _background_retry_rag_indexing(task_id, url_link, config_path)
            )

    except InterruptedError:
        logger.info(f"任务 {task_id} 被用户中断（删除），停止后台处理")
        # 任务记录已删除，无需更新状态

    except Exception as e:
        logger.exception(f"任务 {task_id} 执行过程中发生异常:")
        if not _task_marked_cancelled_by_user(supabase_client, task_id):
            supabase_client.update_task_status(task_id, TaskStatus.FAILED, error=str(e))

    finally:
        # 无论成功还是失败，都清理本地文件以释放存储空间
        if output_path and json_output_dir:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: cleanup_local_files(repo_path, output_path, json_output_dir)
            )
        logger.info(f"[任务 {task_id}] 本地临时文件清理完成")
