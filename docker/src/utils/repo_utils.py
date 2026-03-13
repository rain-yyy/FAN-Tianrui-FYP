import hashlib

def get_repo_name(repo_url: str) -> str:
    """从仓库 URL 中提取仓库名称"""
    clean_url = repo_url.rstrip('/').replace('.git', '')
    repo_name = clean_url.split('/')[-1] if '/' in clean_url else clean_url
    return repo_name


def get_repo_hash(repo_url: str) -> str:
    """根据仓库 URL 生成唯一的短哈希标识"""
    # 清理 URL
    clean_url = repo_url.rstrip('/').replace('.git', '').lower()
    # 提取仓库名称
    repo_name = get_repo_name(clean_url)
    # 生成短哈希
    url_hash = hashlib.md5(clean_url.encode()).hexdigest()[:8]
    return f"{repo_name}_{url_hash}"
