import os
from typing import Optional, List
from urllib.parse import urlsplit
from supabase import create_client, Client
import dotenv

dotenv.load_dotenv()

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
                "current_step": "等待执行",
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
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping get task.")
            return None

        try:
            response = self.client.table("tasks").select("*").eq("task_id", task_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"[Supabase] Error getting task: {e}")
            return None

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

    def get_all_available_repos(self):
        """
        Get all available repos from Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping get all available repos.")
            return None

        try:
            response = self.client.table("repositories").select("*").execute()
            return response.data
        except Exception as e:
            print(f"[Supabase] Error getting all available repos: {e}")
            return None

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

    def build_cached_task_result(self, repo_url: str):
        """
        Build task result payload from repositories table for cache hit.
        Returns None when required wiki artifacts are missing.
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

        return {
            "r2_structure_url": r2_structure_url,
            "r2_content_urls": r2_content_urls,
            "json_wiki": None,
            "json_content": None,
            "vector_store_path": repo_info.get("vector_store_path"),
            "repo_url": self._normalize_repo_url(repo_url),
        }

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

    def update_repository_information(self, repo_url: str, r2_structure_url: Optional[str], r2_content_urls: Optional[List[str]], vector_store_path: Optional[str], description: Optional[str] = None):
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


def update_repo_vector_path(repo_url: str, vector_store_path: str):
    """
    Helper function to update repository vector path in Supabase.
    """
    client = SupabaseClient()
    return client.update_repository_vector_path(repo_url, vector_store_path)


