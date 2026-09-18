import sys
import shutil
from pathlib import Path
from git import Repo, GitCommandError

# 保证 `python docker/scripts/setup_repository.py` 等方式下可导入 src.*
_SCRIPT_DIR = Path(__file__).resolve().parent  # /Users/xxx/FAN-Tianrui-FYP/docker/scripts
_DOCKER_ROOT = _SCRIPT_DIR.parent  # /Users/xxx/FAN-Tianrui-FYP/docker
if str(_DOCKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_DOCKER_ROOT))  # 把 docker/ 加入 sys.path，使 `import src.xxx` 可用

from src.paths import REPO_STORE_ROOT, repo_disk_dirname
# REPO_STORE_ROOT 始终是绝对路径（相对路径的 REPO_STORE_PATH 会被锚定到仓库根），
# setup_repository() 的返回值也因此始终是绝对路径


def setup_repository(repo_url_or_path: str) -> str:
    """
    将远程地址克隆到本地，并返回本地路径。
    目录名为仓库名（与 URL 最后一级一致），位于 REPO_STORE_ROOT 下。
    """
    print(f"Setting up repository for: {repo_url_or_path}")

    try:

        REPO_STORE_ROOT.mkdir(parents=True, exist_ok=True)
        repo_dir_name = repo_disk_dirname(repo_url_or_path)  # "https://github.com/foo/bar.git" -> "bar"
        repo_dir = (REPO_STORE_ROOT / repo_dir_name).resolve()

        # 每次拉取前清理旧目录，避免脏状态
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
