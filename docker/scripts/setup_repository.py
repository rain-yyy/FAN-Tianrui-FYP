import os
import sys
import shutil
from pathlib import Path
from git import Repo, GitCommandError

# 保证 `python docker/scripts/setup_repository.py` 等方式下可导入 src.*
_SCRIPT_DIR = Path(__file__).resolve().parent
_DOCKER_ROOT = _SCRIPT_DIR.parent
if str(_DOCKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_DOCKER_ROOT))

from src.utils.repo_utils import get_repo_disk_directory_name

# 默认：<仓库根>/data/repos（data 与 docker/ 同级）；容器/Fly 通过 REPO_STORE_PATH=/data/repos 覆盖
_REPO_ROOT = _SCRIPT_DIR.parent.parent  # scripts -> docker -> 仓库根
_DEFAULT_REPO_STORE = _REPO_ROOT / "data" / "repos"
REPO_STORE_ROOT = Path(os.getenv("REPO_STORE_PATH", str(_DEFAULT_REPO_STORE))).expanduser()


def _is_remote_repo(repo_url_or_path: str) -> bool:
    return repo_url_or_path.startswith(("http://", "https://", "git@"))


def setup_repository(repo_url_or_path: str) -> str:
    """
    将远程地址克隆到本地，并返回本地路径。
    目录名为仓库名（与 URL 最后一级一致），位于 REPO_STORE_ROOT 下。
    """

    print(f"Setting up repository for: {repo_url_or_path}")

    try:
        if not _is_remote_repo(repo_url_or_path):
            local_path = Path(repo_url_or_path).expanduser().resolve()
            if not local_path.exists():
                raise ValueError(f"Local repository path not found: {local_path}")
            return str(local_path)

        REPO_STORE_ROOT.mkdir(parents=True, exist_ok=True)
        repo_dir_name = get_repo_disk_directory_name(repo_url_or_path)
        repo_dir = (REPO_STORE_ROOT / repo_dir_name).resolve()

        # 保持目录可重复使用：每次拉取前清理旧目录，避免脏状态
        if repo_dir.exists():
            shutil.rmtree(repo_dir)

        print(f"Cloning repository {repo_url_or_path} to persistent directory: {repo_dir}")
        Repo.clone_from(repo_url_or_path, str(repo_dir))
        print(f"Repository cloned successfully to: {repo_dir}")
        return str(repo_dir)
    except GitCommandError as e:
        print(f"Error cloning repository: {e}")
        raise ValueError(f"Failed to clone repository: {e}, Please check if the repository is valid and accessible.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise 
