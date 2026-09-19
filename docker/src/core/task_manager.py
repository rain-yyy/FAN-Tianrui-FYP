"""
Wiki 生成任务的进程内生命周期管理：跟踪正在运行的 asyncio.Task，
支持创建和强制取消。任务的持久状态（pending/processing/completed/...）
仍然由 Supabase 的 tasks 表保存，这里只管理本进程内的可取消句柄。
"""
import asyncio
from enum import Enum
from typing import Dict


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

