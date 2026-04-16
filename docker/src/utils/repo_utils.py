import hashlib

def get_repo_name(repo_url: str) -> str:
    """从仓库 URL 中提取仓库名称"""
    clean_url = repo_url.rstrip('/').replace('.git', '')
    repo_name = clean_url.split('/')[-1] if '/' in clean_url else clean_url
    return repo_name


def sanitize_repo_dir_name(name: str) -> str:
    """将仓库名规范为可在 REPO_STORE_ROOT 下使用的单段目录名。"""
    n = (name or "").strip() or "repo"
    for bad in ("/", "\\", ":", "\0"):
        n = n.replace(bad, "_")
    n = n.strip(".") or "repo"
    return n[:200]


def get_repo_disk_directory_name(repo_url: str) -> str:
    """
    VECTOR_STORE_ROOT 与 REPO_STORE_ROOT 下的同一套目录 basename。
    与既有向量库目录命名一致（URL 最后一级仓库名）。
    """
    return get_repo_name(repo_url)


def get_repo_hash(repo_url: str) -> str:
    """根据仓库 URL 生成唯一的短哈希标识"""
    # 清理 URL
    clean_url = repo_url.rstrip('/').replace('.git', '').lower()
    # 提取仓库名称
    repo_name = get_repo_name(clean_url)
    # 生成短哈希
    url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:8]
    return f"{repo_name}_{url_hash}"
