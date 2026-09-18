from fastapi import APIRouter, HTTPException, Request

from src.storage.supabase_client import SupabaseClient, SupabaseStorageError
from src.utils.logger import setup_logger

logger = setup_logger("api.repos")
router = APIRouter()


@router.post("/dashboard/repos")
async def list_dashboard_repositories_api(request: Request):
    """
    工作台仓库卡片：以 `repositories` 表中已索引的仓库为准（含 R2 结构与内容 URL），
    仅展示当前用户曾成功生成/命中缓存的 repo；返回 task_id 供前端进入 /wiki/:taskId。
    """
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")

    supabase_client = SupabaseClient()
    try:
        repos = supabase_client.get_user_dashboard_repositories(user_id)
    except Exception as e:
        logger.exception("列出工作台仓库失败")
        raise HTTPException(status_code=503, detail=f"Failed to list dashboard repositories: {e}") from e

    return {"repos": repos}


@router.get("/repos/github-metadata")
async def repos_github_metadata_api():
    """
    返回 repositories 表中所有仓库的 stargazers_count 与 github_short_description。
    """
    supabase_client = SupabaseClient()
    if not supabase_client.client:
        raise HTTPException(status_code=503, detail="Database not configured")

    rows = supabase_client.get_all_repositories_metadata()
    metadata = {
        row["repo_url"]: {
            "repo_url": row["repo_url"],
            "stars": row.get("stargazers_count"),
            "github_short_description": row.get("github_short_description"),
        }
        for row in rows
        if row.get("repo_url")
    }
    return {"metadata": metadata}


@router.get("/chat/repos")
async def list_available_repos_api():
    """
    列出所有可用于聊天的仓库
    """
    logger.info("列出所有可用于聊天的仓库")
    supabase_client = SupabaseClient()
    try:
        available_repos = supabase_client.get_all_indexed_repos()
    except SupabaseStorageError as e:
        logger.error("列出可用仓库时 Supabase 失败: %s", e)
        raise HTTPException(status_code=503, detail=f"Failed to list repositories: {e}")

    return {"repos": available_repos}

## TODO: What the differents between list_dashboard_repositories_api() and list_available_repos_api()