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
from src.config import CONFIG_PATH, load_config, CONFIG
from src.ingestion.file_processor import generate_file_tree, get_files_to_process, split_code_and_text_files
from src.ingestion.docu_splitter import load_and_split_docs
from src.ingestion.vector_store import create_and_save_vector_store
from src.wiki.struct_gen import generate_wiki_structure
from src.wiki.content_gen import WikiContentGenerator
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.storage.r2_client import upload_wiki_to_r2
from src.core.chat import invalidate_vector_store_cache
from src.storage.supabase_client import update_repo_vector_path, SupabaseClient, SupabaseStorageError
from src.utils.github_repo_metadata import refresh_github_metadata_for_repo_url
from src.utils.repo_utils import get_repo_disk_directory_name

# 任务状态定义 (保持与 api.py 一致)
class TaskStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

logger = logging.getLogger("api")

# 项目根目录获取
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# 默认路径配置（保留为单任务回退，实际并发场景应使用 _task_output_dir 生成的任务级路径）
DEFAULT_OUTPUT_PATH: Path = PROJECT_ROOT / "wiki_structure.json"
DEFAULT_WIKI_SECTION_JSON_OUTPUT: Path = PROJECT_ROOT / "wiki_section_json"

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
    repo_dir = get_repo_disk_directory_name(repo_url)
    dest_root = VECTOR_STORE_ROOT / repo_dir
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
        dest = dest_root / "graphrag_communities.json"
        shutil.copy2(source_json, dest)
        logger.info("[GraphRAG] 元数据已写入向量库目录（结构阶段）: %s", dest)
    except OSError as exc:
        logger.warning("[GraphRAG] 结构阶段写入向量库失败: %s", exc)

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
    if row.get("status") != TaskStatus.FAILED:
        return False
    err = (row.get("error") or "").lower()
    return "cancel" in err


def _update_progress(task_id: Optional[str], progress: float, step: str):
    """内部辅助函数，同步更新任务进度到 Supabase"""
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
    repo_url_or_path: str, config_path: Path, output_path: Path,
    task_id: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    """
    根据仓库地址（Git URL 或本地路径）生成 wiki 目录结构，并保存到指定文件。
    """
    _update_progress(task_id, 5, "Cloning/reading repository...")

    if repo_url_or_path.startswith(("http://", "https://", "git@")):
        repo_path = setup_repository(repo_url_or_path)
    else:
        repo_path = repo_url_or_path

    _update_progress(task_id, 15, "Repository ready, loading configuration...")

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    # 加载配置（用于验证配置文件有效性）
    _ = load_config(str(config_path))

    _update_progress(task_id, 20, "Generating file tree...")

    file_tree = generate_file_tree(repo_path, str(config_path))

    _update_progress(task_id, 30, "Generating Wiki structure (including GraphRAG community analysis)...")

    communities_path = str((output_path.parent / "graphrag_communities.json").resolve())
    wiki_structure = generate_wiki_structure(
        repo_path,
        file_tree,
        communities_persist_path=communities_path,
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
    _update_progress(task_id, 45, "Initializing AI client...")

    provider, model = get_model_config(CONFIG, "wiki_content")
    # 构建客户端工厂，让每个并发 worker 独立获取实例
    client_factory = lambda: get_ai_client(provider, model=model)

    # 进度回调：将章节级进度同步到 Supabase
    def _progress_cb(progress: float, step: str):
        _update_progress(task_id, progress, step)

    generator = WikiContentGenerator(
        repo_root=repo_path,
        json_output_dir=json_output_dir,
        client_factory=client_factory,
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
    config = load_config(str(config_path))
    all_files = get_files_to_process(repo_path, str(config_path))
    
    if not all_files:
        logger.warning("[RAG] No files found to index")
        return str(vector_store_path)
    
    # 分离代码和文本文件
    code_files, text_files = split_code_and_text_files(all_files, config)
    
    logger.info(f"[RAG] Found {len(code_files)} code files, {len(text_files)} text files")
    
    # 处理代码文件
    if code_files:
        _update_progress(task_id, 89, f"Indexing {len(code_files)} code files...")
        
        code_docs = load_and_split_docs(code_files, debug_output_path="chunk_debug/code_chunks.jsonl")
        if code_docs:
            code_store_path = str(vector_store_path / "code")
            create_and_save_vector_store(code_docs, code_store_path)
            logger.info(f"[RAG] Code vector store saved: {code_store_path}")
    
    # 处理文本文件
    if text_files:
        _update_progress(task_id, 90, f"Indexing {len(text_files)} text files...")
        
        text_docs = load_and_split_docs(text_files, debug_output_path="chunk_debug/text_chunks.jsonl")
        if text_docs:
            text_store_path = str(vector_store_path / "text")
            create_and_save_vector_store(text_docs, text_store_path)
            logger.info(f"[RAG] Text vector store saved: {text_store_path}")
    
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


def _parse_task_result_blob(raw: Any) -> Dict[str, Any]:
    """将 Supabase tasks.result（dict 或 JSON 字符串）规范为 dict。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


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
                comm = (VECTOR_STORE_ROOT / repo_dir / "graphrag_communities.json").resolve()
                comm_arg = str(comm) if comm.is_file() else None
                return run_rag_indexing(
                    rp,
                    url_link,
                    config_path,
                    task_id=None,
                    communities_json_path=comm_arg,
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

        prev = _parse_task_result_blob(task_row.get("result"))
        prev["vector_store_path"] = vector_store_path
        emb = prev.get("embedding") if isinstance(prev.get("embedding"), dict) else {}
        emb = dict(emb)
        emb["status"] = "ready"
        emb["last_error"] = None
        emb["retry_attempts"] = attempt
        emb["ready_at"] = datetime.now(timezone.utc).isoformat()
        prev["embedding"] = emb

        supabase_client.update_task_status(task_id, TaskStatus.COMPLETED, result=prev)

        repo_row = supabase_client.get_repo_information(url_link)
        desc = (repo_row or {}).get("description")
        supabase_client.update_repository_information(
            url_link,
            None,
            None,
            vector_store_path,
            desc,
        )
        logger.info(f"[RAG 重试] 成功 task={task_id} vector_store_path={vector_store_path}")
        return

    try:
        task_row = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        logger.warning(f"[RAG 重试] 最终状态写回前 Supabase 失败: {e}")
        return
    if not task_row:
        return
    prev = _parse_task_result_blob(task_row.get("result"))
    emb = prev.get("embedding") if isinstance(prev.get("embedding"), dict) else {}
    emb = dict(emb)
    emb["status"] = "failed"
    emb["last_error"] = last_error
    emb["retry_attempts"] = len(delays_before_attempt_sec)
    emb["failed_at"] = datetime.now(timezone.utc).isoformat()
    prev["embedding"] = emb
    supabase_client.update_task_status(task_id, TaskStatus.COMPLETED, result=prev)
    logger.error(f"[RAG 重试] task={task_id} 已达最大重试次数，embedding 仍为失败")


def _generate_repo_description(repo_path: str, repo_url: str) -> str:
    """
    Generate a short description for the repository using LLM.
    """
    try:
        # Try to read README
        readme_content = ""
        repo_dir = Path(repo_path)
        for name in ["README.md", "readme.md", "README.txt", "readme.txt"]:
            readme_path = repo_dir / name
            if readme_path.exists():
                try:
                    readme_content = readme_path.read_text(encoding="utf-8", errors="replace")[:2000]
                    break
                except Exception:
                    continue
        
        provider, model = get_model_config(CONFIG, "chat") # Use chat model for description
        client = get_ai_client(provider, model=model)
        
        prompt = f"""
Based on the following repository information, generate a very concise description (max 20 words) for this project.
Focus on its main purpose and functionality. Do not include phrases like "This repository contains" or "This project is". Just the description.

Repository URL: {repo_url}
README Excerpt:
{readme_content}

Description:
"""
        messages = [{"role": "user", "content": prompt}]
        response = client.chat(messages, temperature=0.3, max_tokens=100)
        description = response.strip().strip('"').strip("'")
        return description
    except Exception as e:
        logger.warning(f"Failed to generate repo description: {e}")
        return f"Repository: {repo_url.split('/')[-1]}"


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

        # 全量重跑前清掉进程内 FAISS 缓存，避免同一路径上磁盘已重建但仍命中旧向量
        try:
            vs_dir = VECTOR_STORE_ROOT / get_repo_disk_directory_name(url_link)
            invalidate_vector_store_cache(str(vs_dir.resolve()))
        except Exception:
            logger.debug("invalidate_vector_store_cache 跳过或失败", exc_info=True)

        # 构建任务级隔离路径
        config_path = CONFIG_PATH.expanduser().resolve()
        task_dir = _task_output_dir(task_id) #临时工作目录
        output_path = (task_dir / "wiki_structure.json").resolve()
        json_output_dir = (task_dir / "wiki_section_json").resolve()

        
        # 1. 生成项目结构 (wiki_structure.json)
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
        await asyncio.sleep(0)

        graphrag_json_path = (output_path.parent / "graphrag_communities.json").resolve()
        await loop.run_in_executor(
            None,
            lambda: _persist_graphrag_communities_to_vector_store(url_link, graphrag_json_path),
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
        r2_structure_url, r2_content_urls, r2_graphrag_url = await loop.run_in_executor(
            None,
            lambda gp=graphrag_json_path: upload_wiki_to_r2(
                repo_url=url_link,
                wiki_structure=wiki_structure,
                structure_local_path=output_path,
                content_dir=json_output_dir,
                task_id=task_id,
                graphrag_local_path=gp if gp.is_file() else None,
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
                    communities_json_path=str(
                        (output_path.parent / "graphrag_communities.json").resolve()
                    ),
                )
            )
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
            "json_wiki": str(output_path) if not r2_structure_url else None,
            "json_content": str(json_output_dir) if not r2_content_urls else None,
            "vector_store_path": vector_store_path,
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
            "任务 %s Wiki 流程结束（embedding 成功=%s）",
            task_id,
            embedding_error is None,
        )

        description = await loop.run_in_executor(
            None,
            lambda: _generate_repo_description(repo_path, url_link)
        )
        await asyncio.sleep(0)
        logger.info(f"生成仓库描述: {description}")

        success = supabase_client.update_repository_information(
            url_link,
            r2_structure_url,
            r2_content_urls,
            vector_store_path if embedding_error is None else None,
            description,
        )
        if not success:
            logger.error(f"同步supabase repository表失败: {url_link}")
        else:
            try:
                refresh_github_metadata_for_repo_url(
                    supabase_client, url_link, force_if_missing=True
                )
            except Exception as gh_exc:
                logger.warning("同步 GitHub 公开元数据失败（可稍后由接口后台刷新）: %s", gh_exc)

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
