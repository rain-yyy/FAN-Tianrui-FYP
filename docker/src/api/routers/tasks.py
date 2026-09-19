import uuid

from fastapi import APIRouter, HTTPException, Request
from typing import List, Optional

from src.core.task_manager import TaskStatus, cancel_running_task, start_generation_task
from src.storage.supabase_client import SupabaseStorageError, DeleteTaskResult, get_supabase_client
from src.utils.logger import setup_logger

from src.storage.models import TaskRecord

logger = setup_logger("api.tasks")
router = APIRouter()


@router.post("/generate")
async def generate_wiki(request: Request):
    """
    创建 Wiki 生成任务（异步）
    """
    try:
        data = await request.json()
        url_link = data.get("url_link")
        user_id = data.get("user_id")

        if not url_link or not user_id:
            raise HTTPException(status_code=400, detail="Missing url_link or user_id")

        supabase_client = get_supabase_client()
        task_id = str(uuid.uuid4())

        # 创建任务记录；缓存判断在后台 execute_generation_task 中进行
        success = supabase_client.create_task(user_id, task_id, url_link)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to create task record")
        logger.info(f"创建任务成功: {task_id}")

        # 启动后台任务（进程内登记，支持后续强制取消）
        start_generation_task(task_id, url_link)

        return {
            "task_id": task_id,
            "message": "Task created and processing in the background. Poll /task/{task_id} for progress."
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("创建任务失败:")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/task/{task_id}")
async def get_task_information_api(task_id: str) -> Optional[TaskRecord]:
    """
    查询任务信息
    """
    logger.debug(f"查询任务信息: {task_id}")

    supabase_client = get_supabase_client()

    try:
        task_information = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        logger.error("查询任务时 Supabase 不可用: %s", e)
        raise HTTPException(status_code=503, detail=f"Supabase temporarily unavailable: {e}")

    if not task_information:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

    return task_information


@router.post("/tasks")
async def list_tasks_api(request: Request) -> Optional[List[TaskRecord]]:
    """
    List all the tasks associated with the userid
    """
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        logger.debug(f"Userid doesn't exist: {user_id}")
        raise HTTPException(status_code=400, detail="Missing user_id")

    logger.debug(f"List all associated tasks for: {user_id}")
    supabase_client = get_supabase_client()
    all_tasks = supabase_client.get_all_tasks(user_id)
    return all_tasks


@router.post("/task/{task_id}/cancel")
async def cancel_task_api(task_id: str) -> dict:
    """
    Termination task with task_id
    """
    logger.info(f"Terminate: {task_id}")
    supabase_client = get_supabase_client()

    def _persist_cancelled_status() -> bool:
        return supabase_client.update_task_status(
            task_id, TaskStatus.FAILED.value, error="Cancelled by user"
        )

    if cancel_running_task(task_id):
        if not _persist_cancelled_status():
            logger.error(f"取消任务后写入 Supabase 失败: {task_id}")
            raise HTTPException(
                status_code=503,
                detail="Task stopped locally but failed to persist cancelled status; try again.",
            )
        logger.info(f"Task has been terminated: {task_id}")
        return {"success": True, "message": "Task cancelled"}

    try:
        task_info = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        logger.error("取消任务时无法查询 Supabase: %s", e)
        raise HTTPException(status_code=503, detail=f"Could not verify task status: {e}")

    # The service has been restarted
    if task_info and task_info.status == TaskStatus.PROCESSING.value:
        if not _persist_cancelled_status():
            logger.error(f"仅 DB 标记取消时写入失败: {task_id}")
            raise HTTPException(status_code=503, detail="Failed to update task status in database")
        logger.info(f"The task was not found in the memory, but it has been marked as cancelled in the datbase: {task_id}")
        return {"success": True, "message": "Task marked as cancelled"}

    # 尚未进入 execute_generation_task 的 processing，无内存任务可杀；删除流程可继续
    if task_info and task_info.status == TaskStatus.PENDING.value:
        return {"success": True, "message": "Task not yet running; nothing to cancel"}

    logger.warning(f"Task is not running or does not exist: {task_id}")
    raise HTTPException(
        status_code=404,
        detail="No running task found or task is not processing",
    )


@router.delete("/task/{task_id}")
async def delete_task_api(task_id: str, user_id: str) -> dict[str,bool]:
    """
    Delete task record in supabase（包括进行中、已完成或失败的任务）
    """
    if not task_id or not user_id:
        raise HTTPException(status_code=400, detail="Missing task_id or user_id")
    logger.info(f"Delete task record: {task_id}")
    supabase_client = get_supabase_client()
    result = supabase_client.delete_task(task_id, user_id)
    if result == DeleteTaskResult.NOT_FOUND:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    if result == DeleteTaskResult.ERROR:
        raise HTTPException(status_code=500, detail="Failed to delete task")

    return {"success": True}
