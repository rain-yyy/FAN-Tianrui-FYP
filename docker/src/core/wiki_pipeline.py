import os
import json
import asyncio
import shutil
import logging
from pathlib import Path
from datetime import datetime
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
from src.storage.supabase_client import update_repo_vector_path, SupabaseClient
from src.utils.repo_utils import get_repo_name, get_repo_hash

# 任务状态定义 (保持与 api.py 一致)
class TaskStatus:
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

logger = logging.getLogger("api")

# 项目根目录获取
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# 默认路径配置
DEFAULT_OUTPUT_PATH: Path = PROJECT_ROOT / "wiki_structure.json"
DEFAULT_WIKI_SECTION_JSON_OUTPUT: Path = PROJECT_ROOT / "wiki_section_json"

# Vector store 根目录 (Fly.io 持久化卷挂载点或本地开发目录)
VECTOR_STORE_ROOT: Path = Path(os.getenv("VECTOR_STORE_PATH", str(PROJECT_ROOT / "vector_stores")))
# 默认使用项目内 data/repos，本地开发可写；Docker 通过 REPO_STORE_PATH=/data/repos 覆盖
REPO_STORE_ROOT: Path = Path(os.getenv("REPO_STORE_PATH", str(PROJECT_ROOT / "data" / "repos"))).expanduser()


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
    _update_progress(task_id, 5, "正在克隆/读取仓库...")

    if repo_url_or_path.startswith(("http://", "https://", "git@")):
        repo_path = setup_repository(repo_url_or_path)
    else:
        repo_path = repo_url_or_path

    _update_progress(task_id, 15, "仓库准备完成，正在加载配置...")

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")

    # 加载配置（用于验证配置文件有效性）
    _ = load_config(str(config_path))

    _update_progress(task_id, 20, "正在生成文件树...")

    file_tree = generate_file_tree(repo_path, str(config_path))

    _update_progress(task_id, 30, "正在生成 Wiki 目录结构（包含 GraphRAG 社区分析）...")

    wiki_structure = generate_wiki_structure(repo_path, file_tree)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(wiki_structure, f, indent=2, ensure_ascii=False)

    _update_progress(task_id, 40, "Wiki 目录结构生成完成")

    return repo_path, wiki_structure


def run_wiki_content_generation(
    repo_path: str,
    wiki_structure: Dict[str, Any],
    json_output_dir: Path,
    task_id: Optional[str] = None
) -> List[Path]:
    """
    调用 AI 客户端，根据 wiki 目录逐条生成内容与 Mermaid 图，并写入 JSON。
    """
    _update_progress(task_id, 45, "正在初始化 AI 客户端...")

    provider, model = get_model_config(CONFIG, "wiki_content")
    client = get_ai_client(provider, model=model)
    generator = WikiContentGenerator(
        repo_root=repo_path,
        json_output_dir=json_output_dir,
        client=client,
    )

    _update_progress(task_id, 50, "正在生成 Wiki 内容（此步骤可能耗时较长）...")

    result = generator.generate(wiki_structure)

    _update_progress(task_id, 85, "Wiki 内容生成完成")

    return result


def run_rag_indexing(
    repo_path: str,
    repo_url: str,
    config_path: Path,
    task_id: Optional[str] = None
) -> str:
    """
    为仓库创建 RAG 向量索引（代码和文本分类）
    """
    _update_progress(task_id, 86, "正在构建 RAG 向量索引...")
    
    # 获取仓库名称作为目录名
    repo_name = get_repo_name(repo_url)
    vector_store_path = VECTOR_STORE_ROOT / repo_name
    
    # 确保目录存在
    vector_store_path.mkdir(parents=True, exist_ok=True)
    
    # 获取需要处理的文件
    config = load_config(str(config_path))
    all_files = get_files_to_process(repo_path, str(config_path))
    
    if not all_files:
        logger.warning("[RAG] 没有找到需要索引的文件")
        return str(vector_store_path)
    
    # 分离代码和文本文件
    code_files, text_files = split_code_and_text_files(all_files, config)
    
    logger.info(f"[RAG] 找到 {len(code_files)} 个代码文件, {len(text_files)} 个文本文件")
    
    # 处理代码文件
    if code_files:
        _update_progress(task_id, 87, f"正在索引 {len(code_files)} 个代码文件...")
        
        code_docs = load_and_split_docs(code_files)
        if code_docs:
            code_store_path = str(vector_store_path / "code")
            create_and_save_vector_store(code_docs, code_store_path)
            logger.info(f"[RAG] 代码向量库已保存: {code_store_path}")
    
    # 处理文本文件
    if text_files:
        _update_progress(task_id, 88, f"正在索引 {len(text_files)} 个文本文件...")
        
        text_docs = load_and_split_docs(text_files)
        if text_docs:
            text_store_path = str(vector_store_path / "text")
            create_and_save_vector_store(text_docs, text_store_path)
            logger.info(f"[RAG] 文本向量库已保存: {text_store_path}")
    
    # 同步到 Supabase
    try:
        update_repo_vector_path(repo_url, str(vector_store_path))
    except Exception as e:
        logger.error(f"[Supabase] 同步向量路径失败: {e}")
    
    _update_progress(task_id, 89, "RAG 向量索引构建完成")
    
    logger.info(f"[RAG] 向量库构建完成: {vector_store_path}")
    return str(vector_store_path)


def cleanup_local_files(repo_path: Optional[str], output_path: Path, json_output_dir: Path):
    """
    清理本地生成的临时文件，释放存储空间
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

        # 获取默认路径
        config_path = CONFIG_PATH.expanduser().resolve()
        output_path = DEFAULT_OUTPUT_PATH.expanduser().resolve()
        json_output_dir = DEFAULT_WIKI_SECTION_JSON_OUTPUT.expanduser().resolve()

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

        _update_progress(task_id, 90, "正在上传到 R2 存储...")

        # 4. 上传到 R2 存储
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
        result = {
            "r2_structure_url": r2_structure_url,
            "r2_content_urls": r2_content_urls,
            "json_wiki": str(output_path) if not r2_structure_url else None,
            "json_content": str(json_output_dir) if not r2_content_urls else None,
            "vector_store_path": vector_store_path,  # RAG 向量库路径
            "repo_url": url_link,  # 仓库 URL（用于聊天接口）
        }
        supabase_client.update_task_status(task_id, TaskStatus.COMPLETED, result=result)

        logger.info(f"任务 {task_id} 执行完成")
        
        # 5. 生成仓库简短描述
        description = await loop.run_in_executor(
            None,
            lambda: _generate_repo_description(repo_path, url_link)
        )
        logger.info(f"生成仓库描述: {description}")

        # 同步supabase repository表
        success = supabase_client.update_repository_information(url_link, r2_structure_url, r2_content_urls, vector_store_path, description)
        if not success:
            logger.error(f"同步supabase repository表失败: {url_link}")

    except InterruptedError:
        logger.info(f"任务 {task_id} 被用户中断（删除），停止后台处理")
        # 任务记录已删除，无需更新状态

    except Exception as e:
        logger.exception(f"任务 {task_id} 执行过程中发生异常:")
        # 更新任务为失败状态
        supabase_client.update_task_status(task_id, TaskStatus.FAILED, error=str(e))

    finally:
        # 无论成功还是失败，都清理本地文件以释放存储空间
        if output_path and json_output_dir:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: cleanup_local_files(repo_path, output_path, json_output_dir)
            )
        logger.info(f"[任务 {task_id}] 本地临时文件清理完成")
