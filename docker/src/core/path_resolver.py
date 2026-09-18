"""
仓库相关路径解析。

无论 Supabase 中保存的 vector_store_path / repo_root 为何值（可能来自其它机器的绝对路径），
均在此根据当前环境的 VECTOR_STORE_ROOT / REPO_STORE_ROOT 重新计算，而不是直接信任数据库中的值。
"""
from pathlib import Path
from typing import Optional, Tuple

from src.paths import VECTOR_STORE_ROOT, REPO_STORE_ROOT, repo_disk_dirname


def normalize_vector_store_path(raw_path: Optional[str], repo_url: str) -> str:
    """
    规范化向量库路径，确保从持久化卷正确加载。
    无论数据库中保存的是什么路径（如 macOS 本地绝对路径、/app/vector_stores 等旧路径），
    在此环境中均统一重新计算为 VECTOR_STORE_ROOT 下的对应目录。
    """
    if not raw_path or not raw_path.strip():
        raise ValueError("vector_store_path is empty")

    return str(VECTOR_STORE_ROOT / repo_disk_dirname(repo_url))


def resolve_agent_paths(
    repo_url: str,
    vector_store_path: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    从当前环境变量和路径规则动态推断 graph_path 和 repo_root：
    - graph_path = vector_store_path/code_graph.json（若文件存在）
    - repo_root  = REPO_STORE_ROOT/<repo_name>（若目录存在）
    """
    graph_path: Optional[str] = None
    repo_root: Optional[str] = None

    if vector_store_path:
        code_graph_file = Path(vector_store_path) / "code_graph.json"
        if code_graph_file.is_file():
            graph_path = str(code_graph_file)

    repo_dir = REPO_STORE_ROOT / repo_disk_dirname(repo_url)
    if repo_dir.is_dir():
        repo_root = str(repo_dir)

    return graph_path, repo_root
