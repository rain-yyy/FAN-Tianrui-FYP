from importlib.abc import ExecutionLoader
import os
import tempfile
from git import Repo, GitCommandError
import json
import fnmatch


def setup_repository(repo_url_or_path: str) -> str:
    """
    如果提供的是本地地址，则验证并返回该路径(绝对路径)
    如果提供的是远程地址，则克隆到本地，并返回本地路径
    """

    print(f"Setting up repository for: {repo_url_or_path}")

    if os.path.isdir(repo_url_or_path): 
        print(f"Repository already exists at: {repo_url_or_path}")
        return os.path.abspath(repo_url_or_path)

    try:
        # 创建一个临时安全的目录来存放克隆的仓库   
        temp_dir = tempfile.mkdtemp()
        print(f"Cloning repository {repo_url_or_path} to temporary directory: {temp_dir}")

        Repo.clone_from(repo_url_or_path, temp_dir)

        print(f"Repository cloned successfully to: {temp_dir}")
        return temp_dir
    except GitCommandError as e:
        print(f"Error cloning repository: {e}")
        raise ValueError(f"Failed to clone repository: {e}, Please check if the repository is valid and accessible.")
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise 
