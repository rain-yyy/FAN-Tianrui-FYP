from fastapi import APIRouter, HTTPException, Request

from src.paths import REPO_STORE_ROOT, repo_disk_dirname
from src.utils.path_safety import resolve_path_under_root

router = APIRouter()


@router.post("/file/content")
async def get_file_content_api(request: Request):
    """
    读取指定仓库的指定文件的内容
    """
    data = await request.json()
    repo_url = data.get("repo_url")
    file_path = data.get("file_path")

    if not repo_url or not file_path:
        raise HTTPException(status_code=400, detail="Missing repo_url or file_path")

    repo_dir = REPO_STORE_ROOT / repo_disk_dirname(repo_url)
    target = resolve_path_under_root(repo_dir, file_path)
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    content = target.read_text(encoding="utf-8", errors="replace")
    return {"content": content}
