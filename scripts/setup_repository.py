import tempfile
from git import Repo, GitCommandError


def setup_repository(repo_url_or_path: str) -> str:
    """
    将远程地址克隆到本地，并返回本地路径
    """

    print(f"Setting up repository for: {repo_url_or_path}")

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
