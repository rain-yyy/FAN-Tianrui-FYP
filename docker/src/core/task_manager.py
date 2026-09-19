"""
Wiki 生成任务的进程内生命周期管理：跟踪正在运行的 asyncio.Task，
支持创建和强制取消。任务的持久状态（pending/processing/completed/...）
仍然由 Supabase 的 tasks 表保存，这里只管理本进程内的可取消句柄。
"""
import asyncio
from enum import Enum
from typing import Dict, Optional, Tuple

from src.storage.supabase_client import SupabaseClient, SupabaseStorageError


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    CACHED = "cached"
    FAILED = "failed"


_running_tasks: Dict[str, asyncio.Task] = {}


def start_generation_task(task_id: str, url_link: str) -> asyncio.Task:
    """创建后台 Wiki 生成任务并登记，任务结束（成功/失败/取消）后自动从登记表移除。"""
    # 延迟导入以避免与 wiki_pipeline（导入本模块的 TaskStatus）形成循环导入
    from src.core.wiki_pipeline import execute_generation_task

    task = asyncio.create_task(execute_generation_task(task_id, url_link))
    register_task(task_id, task)
    return task


def register_task(task_id: str, task: asyncio.Task) -> None:
    """
    登记一个已创建的 asyncio.Task，使 /task/{id}/cancel 能够找到并取消它。

    用于主生成任务已完成、登记已被移除之后才启动的后续任务（例如 Wiki 上传成功但
    RAG 索引失败时调度的后台重试），否则该任务会脱离取消登记表，无法被用户中断。
    """
    _running_tasks[task_id] = task
    task.add_done_callback(
        lambda t: _running_tasks.pop(task_id, None) if _running_tasks.get(task_id) is t else None
    )


def cancel_running_task(task_id: str) -> bool:
    """
    If the task is still running within this process, it will be cancelled
    Return whether the task was found and cancelled
    """
    task = _running_tasks.pop(task_id, None)
    if task is None:
        return False
    task.cancel()
    return True


class CancelOutcome(str, Enum):
    """`cancel_task` 的结果，供 /task/{id}/cancel 路由映射为 HTTP 响应，避免路由自己承载分支逻辑。"""

    CANCELLED = "cancelled"                          # 进程内仍在运行，已本地取消并写回 DB
    MARKED_CANCELLED_IN_DB = "marked_cancelled_in_db"  # 进程内已无登记（如服务重启），但 DB 显示 processing，仅写回 DB
    NOT_YET_RUNNING = "not_yet_running"               # DB 显示 pending，尚未进入执行，无需取消
    PERSIST_FAILED_AFTER_STOP = "persist_failed_after_stop"  # 本地已停止，但写回 DB 失败
    PERSIST_FAILED_DB_ONLY = "persist_failed_db_only"        # 仅 DB 标记取消时写入失败
    QUERY_FAILED = "query_failed"                     # 查询任务状态时 Supabase 失败
    NOT_FOUND = "not_found"                           # 既不在进程内运行，DB 状态也不是 pending/processing


def cancel_task(task_id: str, supabase_client: SupabaseClient) -> Tuple[CancelOutcome, Optional[str]]:
    """
    /task/{id}/cancel 的三分支状态机：
    1. 任务仍在进程内运行 —— 本地取消 + 写回 DB 为 failed/"Cancelled by user"
    2. 进程内找不到（例如服务重启过），但 DB 显示 processing —— 仅写回 DB 标记取消
    3. DB 显示 pending —— 尚未开始执行，无需取消
    否则视为没有可取消的任务。

    返回 (outcome, error_detail)；error_detail 仅在 QUERY_FAILED 时非 None，携带 Supabase 异常信息。
    """
    def _persist_cancelled_status() -> bool:
        return supabase_client.update_task_status(
            task_id, TaskStatus.FAILED.value, error="Cancelled by user"
        )

    if cancel_running_task(task_id):
        if not _persist_cancelled_status():
            return CancelOutcome.PERSIST_FAILED_AFTER_STOP, None
        return CancelOutcome.CANCELLED, None

    try:
        task_info = supabase_client.get_task(task_id)
    except SupabaseStorageError as e:
        return CancelOutcome.QUERY_FAILED, str(e)

    # 服务已重启：进程内登记表已丢失，但 DB 仍显示上一进程留下的 processing 状态
    if task_info and task_info.status == TaskStatus.PROCESSING.value:
        if not _persist_cancelled_status():
            return CancelOutcome.PERSIST_FAILED_DB_ONLY, None
        return CancelOutcome.MARKED_CANCELLED_IN_DB, None

    # 尚未进入 execute_generation_task 的 processing，无内存任务可杀；删除流程可继续
    if task_info and task_info.status == TaskStatus.PENDING.value:
        return CancelOutcome.NOT_YET_RUNNING, None

    return CancelOutcome.NOT_FOUND, None

