import os
import sys
import shutil
from pathlib import Path
from git import Repo, GitCommandError


# TODO: 很多地方都要这样处理路径，是否需要多一个专门用来处理路径的
# 保证 `python docker/scripts/setup_repository.py` 等方式下可导入 src.*
_SCRIPT_DIR = Path(__file__).resolve().parent  # /Users/xxx/FAN-Tianrui-FYP/docker/scripts
_DOCKER_ROOT = _SCRIPT_DIR.parent  # /Users/xxx/FAN-Tianrui-FYP/docker
if str(_DOCKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_DOCKER_ROOT))  # 把 docker/ 加入 sys.path，使 `import src.xxx` 可用

# 默认：<仓库根>/data/repos（data 与 docker/ 同级）；容器/Fly 通过 REPO_STORE_PATH=/data/repos 覆盖
_REPO_ROOT = _SCRIPT_DIR.parent.parent  # scripts -> docker -> 仓库根，例：/Users/xxx/FAN-Tianrui-FYP
_DEFAULT_REPO_STORE = _REPO_ROOT / "data" / "repos"  # 例：/Users/xxx/FAN-Tianrui-FYP/data/repos
_repo_store_env = os.getenv("REPO_STORE_PATH")
if _repo_store_env:
    REPO_STORE_ROOT = Path(_repo_store_env).expanduser()
    if not REPO_STORE_ROOT.is_absolute():
        # 相对路径统一锚定到本文件推导出的仓库根 _REPO_ROOT，
        # 不依赖调用者进程启动时的工作目录（CWD），避免不同调用位置得到不同结果
        REPO_STORE_ROOT = (_REPO_ROOT / REPO_STORE_ROOT).resolve()
else:
    REPO_STORE_ROOT = _DEFAULT_REPO_STORE
# 本地开发时例：/Users/xxx/FAN-Tianrui-FYP/data/repos；Docker/Fly 容器内例：/data/repos（由环境变量 REPO_STORE_PATH=/data/repos 覆盖，此时本身就是绝对路径）
# REPO_STORE_ROOT 现在始终是绝对路径，setup_repository() 的返回值也始终是绝对路径


def get_repo_name(repo_url: str) -> str:
    """从仓库 URL 中提取仓库名称"""
    clean_url = repo_url.rstrip('/').replace('.git', '')
    return clean_url.split('/')[-1] if '/' in clean_url else clean_url


# api.py / wiki_pipeline.py 通过此名称导入，保留作为别名
get_repo_disk_directory_name = get_repo_name


def setup_repository(repo_url_or_path: str) -> str:
    """
    将远程地址克隆到本地，并返回本地路径。
    目录名为仓库名（与 URL 最后一级一致），位于 REPO_STORE_ROOT 下。
    """
    print(f"Setting up repository for: {repo_url_or_path}")

    try:

        REPO_STORE_ROOT.mkdir(parents=True, exist_ok=True)  # 确保 /Users/xxx/FAN-Tianrui-FYP/data/repos 存在
        repo_dir_name = get_repo_name(repo_url_or_path)  # "https://github.com/foo/bar.git" -> "bar"
        repo_dir = (REPO_STORE_ROOT / repo_dir_name).resolve()  # 例：/Users/xxx/FAN-Tianrui-FYP/data/repos/bar

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
