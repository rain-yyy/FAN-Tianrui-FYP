import logging
import os
from enum import Enum
from typing import Dict, List, Optional
from urllib.parse import urlsplit
from supabase import create_client, Client
import dotenv

from src.storage.models import TaskRecord, coerce_str_list

logger = logging.getLogger(__name__)

dotenv.load_dotenv()


class SupabaseStorageError(Exception):
    """Supabase 网络/查询失败，与「无记录」区分（无记录时 get_task 返回 None）。"""


class DeleteTaskResult(Enum):
    """Outcome of `SupabaseClient.delete_task`, so callers don't need to
    re-query just to tell "no such task for this user" apart from "delete failed"."""

    DELETED = "deleted"
    NOT_FOUND = "not_found"
    ERROR = "error"

class SupabaseClient:
    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
    ):
        self.url = supabase_url or os.getenv("SUPABASE_URL")
        self.key = supabase_key or os.getenv("SUPABASE_KEY")

        if not self.url or not self.key:
            print("[Supabase] Warning: SUPABASE_URL or SUPABASE_KEY not set.")
            self.client = None
        else:
            self.client = create_client(self.url, self.key)

    def _normalize_repo_url(self, repo_url: str) -> str:
        """Normalize a GitHub repo URL to https://host/owner/repo (lowercase)."""
        if not repo_url or not repo_url.strip():
            return ""

        parsed = urlsplit(repo_url.strip())
        path = parsed.path.rstrip("/").removesuffix(".git")
        owner, repo, *_ = [p.lower() for p in path.split("/") if p] + ["", ""]

        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}/{owner}/{repo}"

    def _repo_owner_slug(self,repo_url: str) -> str:
        return ("/").join(repo_url.split("/")[-2:])

    def create_task(self, user_id: str, task_id: str, repo_url: str):
        """
        Create a new task in Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping create task.")
            return False

        repo_url = self._normalize_repo_url(repo_url)
        repo_name = self._repo_owner_slug(repo_url)
        try:
            self.client.table("tasks").insert({
                "user_id": user_id,
                "task_id": task_id,
                "repo_name": repo_name,
                "repo_url": repo_url,
                "status": "pending",
                "progress": 0.0,
                "current_step": "Waiting for execution",
                "created_at": "now()",
                "last_updated": "now()"
            }).execute()
            print(f"[Supabase] Created new task record for {task_id}")
            return True
        except Exception as e:
            print(f"[Supabase] Error creating task: {e}")
            return False

    def update_task_progress(self, task_id: str, progress: float, current_step: str) -> bool:
        """
        Update task progress in Supabase.
        """
        if not self.client:
            return False
        try:
            response = self.client.table("tasks").update({
                "progress": progress,
                "current_step": current_step,
                "last_updated": "now()"
            }).eq("task_id", task_id).execute()
            
            # If no data returned, it means no rows were updated (task likely deleted)
            if not response.data:
                return False
                
            return True
        except Exception as e:
            print(f"[Supabase] Error updating task progress: {e}")
            return False

    def update_task_status(self, task_id: str, status: str, result: Optional[dict] = None, error: Optional[str] = None) -> bool:
        """
        Update task status and result/error in Supabase.
        """
        if not self.client:
            return False
        try:
            update_data = {
                "status": status,
                "last_updated": "now()"
            }
            if result is not None:
                update_data["result"] = result
                update_data["progress"] = 100.0
            if error is not None:
                update_data["error"] = error
            
            response = self.client.table("tasks").update(update_data).eq("task_id", task_id).execute()
            
            # If no data returned, it means no rows were updated (task likely deleted)
            if not response.data:
                return False

            return True
        except Exception as e:
            print(f"[Supabase] Error updating task status: {e}")
            return False

    def delete_task(self, task_id: str, user_id: str) -> DeleteTaskResult:
        """
        Delete a task from Supabase. Only deletes if task belongs to the given user.
        Returns NOT_FOUND when no matching task exists for this user (whether absent
        entirely or owned by someone else), ERROR on a query/delete failure, DELETED
        on success.
        """
        if not self.client:
            return DeleteTaskResult.ERROR
        try:
            task = (
                self.client.table("tasks")
                .select("task_id, user_id, status")
                .eq("task_id", task_id)
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if not task.data or len(task.data) == 0:
                return DeleteTaskResult.NOT_FOUND

            self.client.table("tasks").delete().eq("task_id", task_id).eq("user_id", user_id).execute()
            return DeleteTaskResult.DELETED
        except Exception as e:
            print(f"[Supabase] Error deleting task: {e}")
            return DeleteTaskResult.ERROR

    def get_task(self, task_id: str) -> Optional[TaskRecord]:
        """
        Get a task from Supabase.
        成功且无行时返回 None；客户端未配置、查询异常或行数据不符合 TaskRecord 时抛出 SupabaseStorageError。
        """
        if not self.client:
            raise SupabaseStorageError("Supabase client is not configured")

        try:
            response = self.client.table("tasks").select("*").eq("task_id", task_id).execute()
            if response.data:
                return TaskRecord.model_validate(response.data[0])
            return None
        except Exception as e:
            raise SupabaseStorageError(f"Failed to fetch task: {e}") from e

    def get_all_tasks(self, user_id: str) -> Optional[List[TaskRecord]]:
        """
        Get all users' tasks from Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping get all tasks.")
            return None

        try:
            response = self.client.table("tasks").select("*").eq("user_id", user_id).execute()
            return [TaskRecord.model_validate(row) for row in (response.data or [])]
        except Exception as e:
            print(f"[Supabase] Error getting all tasks: {e}")
            return None

    def get_all_indexed_repos(self) -> List[dict]:
        """
        返回 `repositories` 表的全部行（含所有列），不做用户/完整性过滤。
        供 `/chat/repos` 使用：聊天页的仓库选择器需要展示每一个曾经被处理过的仓库，
        不区分是否属于当前用户、wiki 是否生成完整。与 `get_user_dashboard_repositories`
        （按用户 + wiki 完整性过滤）和 `get_all_repositories_metadata`（仅取 3 个展示列）
        是三个不同用途的读法，并非重复实现。
        Always returns a list (empty on no data); raises SupabaseStorageError on failure.
        """
        if not self.client:
            raise SupabaseStorageError("Supabase client is not configured")

        try:
            response = self.client.table("repositories").select("*").execute()
            data = getattr(response, "data", None)
            return data if isinstance(data, list) else []

        except Exception as e:
            raise SupabaseStorageError(f"Failed to fetch repositories: {e}") from e

    def get_all_repositories_metadata(self) -> List[dict]:
        """
        返回 repositories 表中所有行的 repo_url / stargazers_count / github_short_description。
        供 `/repos/github-metadata` 使用：仅为展示 GitHub 元数据（star 数、简介）而取的窄列查询，
        比 `get_all_indexed_repos` 的 `select("*")` 更省带宽，两者服务不同端点，不是重复代码。
        """
        if not self.client:
            return []
        try:
            resp = (
                self.client.table("repositories")
                .select("repo_url, stargazers_count, github_short_description")
                .execute()
            )
            return resp.data or []
        except Exception as e:
            print(f"[Supabase] Error fetching all repositories metadata: {e}")
            return []

    ## TODO: change the search key from repo_url to repo_name
    def get_repo_information(self, repo_url: str):
        """
        Get a repo information from Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping get repo information.")
            return None
        
        repo_url = self._normalize_repo_url(repo_url)
        try:
            response = self.client.table("repositories").select("*").eq("repo_url", repo_url).execute()
            if response.data:
                return response.data[0]

            # Fallback: match by owner/repo suffix in case historical data used non-canonical URL format
            repo_name = self._repo_owner_slug(repo_url)
            if repo_name:
                fuzzy_response = (
                    self.client
                    .table("repositories")
                    .select("*")
                    .ilike("repo_url", f"%{repo_name}%")
                    .limit(1)
                    .execute()
                )
                if fuzzy_response.data:
                    return fuzzy_response.data[0]

            return None
        except Exception as e:
            print(f"[Supabase] Error getting repo information: {e}")
            return None
        
    def get_repo_wiki_artifacts(self, repo_url: str):
        """
        Check whether a repository has complete wiki artifacts (r2_structure_url + r2_content_urls).
        Returns the artifact payload dict if complete, or None if any artifact is missing.
        TTL/staleness check is handled separately in wiki_pipeline.execute_generation_task.

        Used both as the wiki-generation cache-hit check (single repo, called with the
        requested repo_url) and, via `get_user_dashboard_repositories`, as the per-repo
        completeness filter when building a user's dashboard card list.
        """
        repo_info = self.get_repo_information(repo_url)
        if not repo_info:
            return None

        r2_structure_url = repo_info.get("r2_structure_url")
        r2_content_urls = coerce_str_list(repo_info.get("r2_content_urls"))
        if not r2_structure_url or not r2_content_urls:
            return None

        return {
            "r2_structure_url": r2_structure_url,
            "r2_content_urls": r2_content_urls,
            "vector_store_path": repo_info.get("vector_store_path"),
            "repo_url": self._normalize_repo_url(repo_url),
        }

    def get_user_dashboard_repositories(self, user_id: str) -> List[dict]:
        """
        工作台展示用：仅包含 `repositories` 表中已具备完整 wiki 产物的仓库（与缓存命中条件一致），
        且该用户存在已完成/缓存任务。卡片数据以 repositories 行为准；task_id 用于跳转 Wiki。

        与 `get_all_indexed_repos` 的区别：后者不按用户或完整性过滤，返回 `repositories` 表
        全部行，服务的是聊天页仓库选择器（`/chat/repos`）而非用户工作台，两者输入/输出/用途
        均不同，不能合并为一个方法。
        """
        if not self.client:
            return []

        tasks = self.get_all_tasks(user_id)
        if not tasks:
            return []

        per_repo: Dict[str, dict] = {}
        for t in tasks:
            if t.status not in ("completed", "cached"):
                continue
            if not t.repo_url:
                continue
            norm = self._normalize_repo_url(t.repo_url)
            if not norm:
                continue
            created = t.created_at.isoformat() if t.created_at else ""
            task_id = t.task_id
            prev = per_repo.get(norm)
            if not prev or (created and created > (prev.get("created_at") or "")):
                per_repo[norm] = {"task_id": task_id, "created_at": created}

        result: List[dict] = []
        for norm, meta in per_repo.items():
            if not self.get_repo_wiki_artifacts(norm):
                continue
            row = self.get_repo_information(norm) or {}
            result.append({
                "repo_url": norm,
                "task_id": meta["task_id"],
                "github_short_description": row.get("github_short_description"),
                "description": row.get("description"),
                "stargazers_count": row.get("stargazers_count"),
                "vector_store_path": row.get("vector_store_path"),
                "last_updated": row.get("last_updated"),
            })

        result.sort(key=lambda item: item.get("last_updated") or "", reverse=True)
        return result

    # ============ Chat Related Methods ============

    def create_chat_session(self, user_id: str, repo_url: str, title: Optional[str] = None, preview_text: Optional[str] = None):
        """
        Create a new chat session record in chat_history table.
        """
        if not self.client:
            return None
        
        repo_url = self._normalize_repo_url(repo_url)
        try:
            data = {
                "user_id": user_id,
                "repo_url": repo_url,
                "title": title or f"Chat about {repo_url.split('/')[-1]}",
                "preview_text": preview_text or "New Chat",
            }
            response = self.client.table("chat_history").insert(data).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"[Supabase] Error creating chat history: {e}")
            return None

    def get_user_chat_sessions(self, user_id: str):
        """
        Return all chat sessions for a user, ordered by most recently updated.
        Each row includes a chat_id alias (chat_history.id) for frontend compatibility.
        """
        if not self.client:
            return []
        try:
            response = self.client.table("chat_history")\
                .select("*")\
                .eq("user_id", user_id)\
                .order("updated_at", desc=True)\
                .execute()
            # Ensure chat_id is present for frontend compatibility (id === chat_id)
            return [
                {**row, "chat_id": row.get("id")} if "chat_id" not in row else row
                for row in (response.data or [])
            ]
        except Exception as e:
            print(f"[Supabase] Error getting user chat history: {e}")
            return []

    def get_chat_messages(self, chat_id: str):
        """
        Get all messages for a specific chat session.
        """
        if not self.client:
            return []
        try:
            response = self.client.table("chat_messages")\
                .select("*")\
                .eq("chat_id", chat_id)\
                .order("created_at", desc=False)\
                .execute()
            return response.data
        except Exception as e:
            print(f"[Supabase] Error getting chat messages: {e}")
            return []

    def add_chat_message(self, chat_id: str, role: str, content: str, metadata: Optional[dict] = None):
        """
        Add a message to a chat session.
        """
        if not self.client:
            return None
        try:
            data = {
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "created_at": "now()"
            }
            response = self.client.table("chat_messages").insert(data).execute()
            
            # Update chat_history updated_at
            self.client.table("chat_history").update({
                "updated_at": "now()"
            }).eq("id", chat_id).execute()
            
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"[Supabase] Error adding chat message: {e}")
            return None

    def delete_chat_session(self, chat_id: str, user_id: str) -> bool:
        """
        Delete a chat session and all its messages.
        Only deletes if the session belongs to the given user.
        Deletes chat_messages (child) before chat_history (parent) to respect FK constraints.
        """
        if not self.client:
            return False
        try:
            # Verify chat belongs to user
            chat_row = (
                self.client.table("chat_history")
                .select("id, user_id")
                .eq("id", chat_id)
                .eq("user_id", user_id)
                .execute()
            )
            if not chat_row.data or len(chat_row.data) == 0:
                return False

            # Delete child records first (chat_messages)
            self.client.table("chat_messages").delete().eq("chat_id", chat_id).execute()
            # Delete parent record (chat_history)
            self.client.table("chat_history").delete().eq("id", chat_id).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error deleting chat history: {e}")
            return False

    def upsert_repo_wiki_data(
        self,
        repo_url: str,
        r2_structure_url: Optional[str],
        r2_content_urls: Optional[List[str]],
        vector_store_path: Optional[str],
        description: Optional[str] = None,
        graph_path: Optional[str] = None,
    ):
        """
        Upsert wiki artifact data for a repository (r2 URLs, vector store path, description).
        Only non-None fields are written; repo_url and last_updated are always set.

        Note: graph_path is accepted for API compatibility but NOT written to the DB —
        it is derived dynamically at runtime from vector_store_pgraph_path is intentionally NOTath/code_graph.json.
        """
        if not self.client:
            return False
        
        repo_url = self._normalize_repo_url(repo_url)
        try:
            data = {
                "repo_url": repo_url,
                "last_updated": "now()"
            }
            
            # Only update fields if they are not None
            if r2_structure_url is not None:
                data["r2_structure_url"] = r2_structure_url
            if r2_content_urls is not None:
                data["r2_content_urls"] = r2_content_urls
            if vector_store_path is not None:
                data["vector_store_path"] = vector_store_path
            if description is not None:
                data["description"] = description
            if graph_path is not None:
                data["graph_path"] = graph_path
            # graph_path is intentionally NOT persisted — it is always computed
            # at runtime as vector_store_path/code_graph.json by _resolve_agent_paths()

            self.client.table("repositories").upsert(data).execute()
            print(f"[Supabase] Upserted repository information for {repo_url}")
            return True
        except Exception as e:
            print(f"[Supabase] Error updating repository information (upsert): {e}")
            return False


