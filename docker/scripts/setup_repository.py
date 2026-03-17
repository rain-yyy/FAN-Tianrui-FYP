import os
import shutil
import hashlib
from pathlib import Path
from git import Repo, GitCommandError

# 默认使用项目内 data/repos，本地开发可写；Docker 通过 REPO_STORE_PATH=/data/repos 覆盖
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_REPO_STORE = _SCRIPT_DIR.parent / "data" / "repos"
REPO_STORE_ROOT = Path(os.getenv("REPO_STORE_PATH", str(_DEFAULT_REPO_STORE))).expanduser()


def _is_remote_repo(repo_url_or_path: str) -> bool:
    return repo_url_or_path.startswith(("http://", "https://", "git@"))


def _repo_hash(repo_url_or_path: str) -> str:
    clean_url = repo_url_or_path.rstrip("/").replace(".git", "").lower()
    repo_name = clean_url.split("/")[-1] if "/" in clean_url else clean_url
    url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:8]
    return f"{repo_name}_{url_hash}"


def setup_repository(repo_url_or_path: str) -> str:
    """
    将远程地址克隆到本地，并返回本地路径
    """

    print(f"Setting up repository for: {repo_url_or_path}")

    try:
        if not _is_remote_repo(repo_url_or_path):
            local_path = Path(repo_url_or_path).expanduser().resolve()
            if not local_path.exists():
                raise ValueError(f"Local repository path not found: {local_path}")
            return str(local_path)

        REPO_STORE_ROOT.mkdir(parents=True, exist_ok=True)
        repo_dir_name = _repo_hash(repo_url_or_path)
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
