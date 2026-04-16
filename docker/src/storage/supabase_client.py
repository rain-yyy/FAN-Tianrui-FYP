import os
from typing import Dict, List, Optional
from urllib.parse import urlsplit
from supabase import create_client, Client
import dotenv

from src.utils.wiki_cache_policy import (
    WIKI_GENERATION_CACHE_MAX_AGE_DAYS,
    wiki_generation_cache_is_stale,
)

dotenv.load_dotenv()


class SupabaseStorageError(Exception):
    """Supabase 网络/查询失败，与「无记录」区分（无记录时 get_task 返回 None）。"""


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
        """
        Normalize repository URL to stable canonical format.
        """
        if not repo_url:
            return ""
        raw = repo_url.strip()
        if not raw:
            return ""

        # Support ssh format: git@github.com:owner/repo(.git)
        if raw.startswith("git@") and ":" in raw:
            host_and_path = raw.split("@", 1)[1]
            host, path = host_and_path.split(":", 1)
            path = path.strip().rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                return f"https://{host.lower()}/{parts[0].lower()}/{parts[1].lower()}"

        parsed = urlsplit(raw)
        if parsed.netloc:
            host = parsed.netloc.lower()
            path = parsed.path.strip().rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 2:
                scheme = parsed.scheme.lower() if parsed.scheme else "https"
                return f"{scheme}://{host}/{parts[0].lower()}/{parts[1].lower()}"

            # No owner/repo shape, fallback to cleaned URL
            scheme = parsed.scheme.lower() if parsed.scheme else "https"
            return f"{scheme}://{host}{path}".rstrip("/")

        # Fallback for plain strings
        normalized = raw.rstrip("/")
        if normalized.endswith(".git"):
            normalized = normalized[:-4]
        return normalized.lower()

    def _extract_repo_key(self, normalized_repo_url: str) -> str:
        """
        Extract 'owner/repo' key from normalized URL.
        """
        parsed = urlsplit(normalized_repo_url)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0].lower()}/{parts[1].lower()}"
        return ""

    def update_repository_vector_path(self, repo_url: str, vector_store_path: str):
        """
        Update or insert the vector_store_path for a repository in Supabase using upsert.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping update.")
            return False

        repo_url = self._normalize_repo_url(repo_url)
        try:
            # Use upsert to either update an existing record or insert a new one
            # The 'repo_url' is the primary key, so upsert will use it to match
            self.client.table("repositories").upsert({
                "repo_url": repo_url,
                "vector_store_path": vector_store_path,
                "last_updated": "now()"
            }).execute()
            
            print(f"[Supabase] Upserted repository record (vector path) for {repo_url}")
            return True
        except Exception as e:
            print(f"[Supabase] Error updating repository (upsert): {e}")
            return False

    def create_task(self, user_id: str, task_id: str, repo_url: str):
        """
        Create a new task in Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping create task.")
            return False

        repo_url = self._normalize_repo_url(repo_url)
        try:
            self.client.table("tasks").insert({
                "user_id": user_id,
                "task_id": task_id,
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

    def update_task_progress(self, task_id: str, progress: float, current_step: str):
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

    def update_task_status(self, task_id: str, status: str, result: Optional[dict] = None, error: Optional[str] = None):
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

    def delete_task(self, task_id: str, user_id: str):
        """
        Delete a task from Supabase.
        Only deletes if task belongs to the given user.
        """
        if not self.client:
            return False
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
                return False

            self.client.table("tasks").delete().eq("task_id", task_id).eq("user_id", user_id).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error deleting task: {e}")
            return False

    def get_task(self, task_id: str):
        """
        Get a task from Supabase.
        成功且无行时返回 None；客户端未配置或查询异常时抛出 SupabaseStorageError。
        """
        if not self.client:
            raise SupabaseStorageError("Supabase client is not configured")

        try:
            response = self.client.table("tasks").select("*").eq("task_id", task_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            raise SupabaseStorageError(f"Failed to fetch task: {e}") from e

    def get_all_tasks(self, user_id: str):
        """
        Get all tasks from Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping get all tasks.")
            return None

        try:
            response = self.client.table("tasks").select("*").eq("user_id", user_id).execute()
            return response.data
        except Exception as e:
            print(f"[Supabase] Error getting all tasks: {e}")
            return None

    def get_all_available_repos(self) -> List[dict]:
        """
        Get all available repos from Supabase.
        成功时始终返回 list（可为空）；未配置或查询失败时抛出 SupabaseStorageError。
        """
        if not self.client:
            raise SupabaseStorageError("Supabase client is not configured")

        try:
            response = self.client.table("repositories").select("*").execute()
            return list(response.data or [])
        except Exception as e:
            raise SupabaseStorageError(f"Failed to fetch repositories: {e}") from e

    def get_repositories_for_urls(self, repo_urls: List[str]) -> Dict[str, Optional[dict]]:
        """
        批量拉取 repositories 行，key 为调用方传入 URL 经 normalize 后的字符串。
        缺失时回退到 get_repo_information（含模糊匹配），避免 N+1 全走模糊查询。
        """
        if not self.client:
            return {}
        ordered_unique: List[str] = []
        for u in repo_urls:
            n = self._normalize_repo_url(u or "")
            if not n or n in ordered_unique:
                continue
            ordered_unique.append(n)

        if not ordered_unique:
            return {}

        by_key: Dict[str, dict] = {}
        try:
            resp = (
                self.client.table("repositories")
                .select("*")
                .in_("repo_url", ordered_unique)
                .execute()
            )
            for row in resp.data or []:
                rk = self._normalize_repo_url(row.get("repo_url") or "")
                if rk:
                    by_key[rk] = row
        except Exception as e:
            print(f"[Supabase] Error batch-fetch repositories: {e}")

        for k in ordered_unique:
            if k in by_key:
                continue
            row = self.get_repo_information(k)
            if row:
                by_key[k] = row

        return {k: by_key.get(k) for k in ordered_unique}

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
            repo_key = self._extract_repo_key(repo_url)
            if repo_key:
                fuzzy_response = (
                    self.client
                    .table("repositories")
                    .select("*")
                    .ilike("repo_url", f"%{repo_key}%")
                    .limit(1)
                    .execute()
                )
                if fuzzy_response.data:
                    return fuzzy_response.data[0]

            return None
        except Exception as e:
            print(f"[Supabase] Error getting repo information: {e}")
            return None

    def _repository_wiki_cache_ttl_fresh_db(
        self, db_repo_url: str, max_age_days: int, repo_info: dict
    ) -> bool:
        """
        True = 仍可短路 /generate。优先 rpc：用 Postgres `timezone('utc', now())` 与行内 `last_updated` 比较。
        RPC 未部署或失败时，仅用行内 `last_updated` + 应用 UTC 回退（日志会提示）。
        """
        if max_age_days < 0:
            return True
        if not self.client:
            return not wiki_generation_cache_is_stale(repo_info, max_age_days)

        try:
            resp = (
                self.client.rpc(
                    "repository_wiki_cache_ttl_fresh",
                    {"p_repo_url": db_repo_url, "p_max_age_days": max_age_days},
                ).execute()
            )
            data = resp.data
            if isinstance(data, bool):
                return data
            if isinstance(data, list):
                if not data:
                    return False
                row0 = data[0]
                if isinstance(row0, bool):
                    return row0
                return bool(row0)
            return bool(data)
        except Exception as e:
            print(
                "[Supabase] repository_wiki_cache_ttl_fresh RPC 不可用或未执行 "
                f"supabase_migrations 中的 SQL，回退为仅基于表字段 last_updated + 应用 UTC：{e}"
            )
            return not wiki_generation_cache_is_stale(repo_info, max_age_days)

    def build_cached_task_result(self, repo_url: str, max_cache_age_days: Optional[int] = None):
        """
        Build task result payload from repositories table for cache hit.
        Returns None when required wiki artifacts are missing.

        max_cache_age_days: 传入时（如 WIKI_GENERATION_CACHE_MAX_AGE_DAYS），按 `repositories.last_updated`
        判断；优先在数据库内与 `now() UTC` 比较（rpc），未部署则回退应用时钟。
        None 表示不校验时效（如工作台列表仍展示「有产物但偏旧」的仓库）。
        """
        repo_info = self.get_repo_information(repo_url)
        if not repo_info:
            return None

        r2_structure_url = repo_info.get("r2_structure_url")
        r2_content_urls = repo_info.get("r2_content_urls")
        if isinstance(r2_content_urls, str):
            r2_content_urls = [r2_content_urls]
        if not r2_structure_url or not r2_content_urls:
            return None

        db_key = (repo_info.get("repo_url") or "").strip() or self._normalize_repo_url(repo_url)
        if max_cache_age_days is not None and not self._repository_wiki_cache_ttl_fresh_db(
            db_key, max_cache_age_days, repo_info
        ):
            print(
                f"[Supabase] Wiki cache stale for {repo_url!r} "
                f"(repositories.last_updated vs DB now, TTL {max_cache_age_days}d), "
                "skip short-circuit — full regeneration."
            )
            return None

        return {
            "r2_structure_url": r2_structure_url,
            "r2_content_urls": r2_content_urls,
            "json_wiki": None,
            "json_content": None,
            "vector_store_path": repo_info.get("vector_store_path"),
            "repo_url": self._normalize_repo_url(repo_url),
        }

    def get_user_dashboard_repositories(self, user_id: str) -> List[dict]:
        """
        工作台展示用：仅包含 `repositories` 表中已具备完整 wiki 产物的仓库（与缓存命中条件一致），
        且该用户存在已完成/缓存任务。卡片数据以 repositories 行为准；task_id 用于跳转 Wiki。
        """
        if not self.client:
            return []

        tasks = self.get_all_tasks(user_id)
        if not tasks:
            return []

        per_repo: Dict[str, dict] = {}
        for t in tasks:
            if t.get("status") not in ("completed", "cached"):
                continue
            ru = t.get("repo_url")
            if not ru:
                continue
            norm = self._normalize_repo_url(ru)
            if not norm:
                continue
            created = t.get("created_at") or ""
            task_id = t.get("task_id")
            prev = per_repo.get(norm)
            if not prev or (created and created > (prev.get("created_at") or "")):
                per_repo[norm] = {"task_id": task_id, "created_at": created}

        result: List[dict] = []
        for norm, meta in per_repo.items():
            if not self.build_cached_task_result(norm):
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

    def create_chat_history(self, user_id: str, repo_url: str, title: Optional[str] = None, preview_text: Optional[str] = None):
        """
        Create a new chat history session.
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

    def get_user_chat_history(self, user_id: str):
        """
        Get all chat history sessions for a user.
        Returns list with chat_id alias (chat_history.id is the chat identifier).
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

    def delete_chat_history(self, chat_id: str, user_id: str) -> bool:
        """
        Delete a chat history session and its messages.
        Only deletes if the chat belongs to the given user.
        Must delete chat_messages first (child) then chat_history (parent).
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

    def update_repository_information(
        self,
        repo_url: str,
        r2_structure_url: Optional[str],
        r2_content_urls: Optional[List[str]],
        vector_store_path: Optional[str],
        description: Optional[str] = None,
    ):
        """
        Update repository information in Supabase using upsert.
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

            self.client.table("repositories").upsert(data).execute()
            print(f"[Supabase] Upserted repository information for {repo_url}")
            return True
        except Exception as e:
            print(f"[Supabase] Error updating repository information (upsert): {e}")
            return False

    def update_github_public_metadata(
        self,
        repo_url: str,
        *,
        github_short_description: Optional[str] = None,
        stargazers_count: Optional[int] = None,
    ) -> bool:
        """
        写入 GitHub 公开元数据（stars、GitHub 简介）与刷新时间；不触碰 LLM description。
        使用 upsert：在 ON CONFLICT 时只更新本 payload 中的列，避免纯 update 匹配 0 行时无写入；
        新行仅含元数据列时其余字段为 NULL，后续 Wiki upsert 会补全。
        """
        if not self.client:
            return False
        repo_url = self._normalize_repo_url(repo_url)
        try:
            data: dict = {
                "repo_url": repo_url,
                "github_metadata_updated_at": "now()",
                "last_updated": "now()",
            }
            if github_short_description is not None:
                data["github_short_description"] = github_short_description
            if stargazers_count is not None:
                data["stargazers_count"] = stargazers_count
            self.client.table("repositories").upsert(data).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error updating GitHub public metadata: {e}")
            return False

    # ============ Profile Related Methods ============

    def get_profile(self, user_id: str) -> Optional[dict]:
        """
        Get a user's profile from Supabase.
        """
        if not self.client:
            return None
        try:
            response = self.client.table("profiles").select("*").eq("id", user_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"[Supabase] Error getting profile: {e}")
            return None

    def upsert_profile_preferences(self, user_id: str, theme: Optional[str] = None) -> bool:
        """
        Update a user's theme preference.
        """
        if not self.client:
            return False
        try:
            data: dict = {"id": user_id, "updated_at": "now()"}
            if theme is not None:
                if theme not in ("light", "dark"):
                    return False
                data["theme"] = theme
            else:
                return False

            self.client.table("profiles").upsert(data).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error upserting profile preferences: {e}")
            return False


def update_repo_vector_path(repo_url: str, vector_store_path: str):
    """
    Helper function to update repository vector path in Supabase.
    """
    client = SupabaseClient()
    return client.update_repository_vector_path(repo_url, vector_store_path)


