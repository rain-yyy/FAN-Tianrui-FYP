import os
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
import dotenv
from datetime import datetime, timedelta
from dateutil import parser

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

    def check_client(self):
        """
        Check if the client is initialized.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping operation.")
            return False
        return True

    def update_repository_vector_path(self, repo_url: str, vector_store_path: str):
        """
        Update the vector_store_path for a repository in Supabase.
        """
        if not self.check_client():
            return False

        try:
            # First check if the repository exists
            response = self.client.table("repositories").select("*").eq("repo_url", repo_url).execute()
            
            if response.data:
                # Update existing record
                self.client.table("repositories").update({
                    "vector_store_path": vector_store_path,
                    "last_updated": "now()"
                }).eq("repo_url", repo_url).execute()
                print(f"[Supabase] Updated vector_store_path for {repo_url}")
            else:
                # Insert new record if it doesn't exist (though it should usually exist by now)
                self.client.table("repositories").insert({
                    "repo_url": repo_url,
                    "vector_store_path": vector_store_path,
                    "last_updated": "now()"
                }).execute()
                print(f"[Supabase] Created new repository record with vector_store_path for {repo_url}")
            return True
        except Exception as e:
            print(f"[Supabase] Error updating repository: {e}")
            return False

    def create_task(self, task_id: str, repo_url: str, user_id: str):
        """
        更新supabse tasks表,创建任务
        """
        if not self.check_client():
            return False
        try:
            self.client.table("tasks").insert({
            "task_id": task_id,
            "repo_url": repo_url,
            "user_id": user_id,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat()
            }).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error creating task: {e}")
            return False   


    def task_status_update(self, task_id: str, status: str, error: str = None):
        """
        Update the task status in Supabase.
        """
        if not self.check_client():
            return False
        try:
            if error:
                self.client.table("tasks").update({
                    "status": status,
                    "last_updated": datetime.now().isoformat(),
                    "error": error
                }).eq("task_id", task_id).execute()
            else:
                self.client.table("tasks").update({
                    "status": status,   
                    "last_updated": datetime.now().isoformat(),
                }).eq("task_id", task_id).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error updating task status: {e}")
            return False


    def task_finished(self, task_id: str, r2_structure_url: str, r2_content_urls: list[str], vector_store_path: str, repo_url: str):
        """
        更新任务状态为 finished（完成），并在 repositories 表中：
        - 如果存在相同 repo_url 的记录则更新；
        - 如果不存在则创建新的记录。
        """
        if not self.check_client():
            return False

        try:
            # 更新任务状态
            self.task_status_update(task_id, "completed")

            # 组装要更新/插入的数据
            data = {
                "repo_url": repo_url,
                "r2_structure_url": r2_structure_url,
                "r2_content_urls": r2_content_urls,
                "last_updated": datetime.now().isoformat(),
                "vector_store_path": vector_store_path,
            }

            # 使用 upsert，以 repo_url 作为唯一键（需在 Supabase 数据库中设置 repo_url 为唯一约束）
            self.client.table("repositories").upsert(data, on_conflict="repo_url").execute()

            return True
        except Exception as e:
            print(f"[Supabase] Error updating or inserting repository: {e}")
            return False


    def check_repo_processed(self, repo_url: str) -> bool:
        """
        Check if the repository has been processed in the last two days.
        """
        if not self.check_client():
            return False
        try:
            resp = self.client.table("repositories") \
                .select("last_updated") \
                .eq("repo_url", repo_url) \
                .limit(1) \
                .execute()

            if not resp.data or not resp.data[0].get("last_updated"):
                return False

            last_updated_raw = resp.data[0]["last_updated"]
            last_updated = parser.isoparse(last_updated_raw)

            # 用 UTC，避免 naive/aware datetime 混算
            now = datetime.now(timezone.utc)
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)

            return (now - last_updated) < timedelta(days=2)

        except Exception as e:
            print(f"[Supabase] Error checking repository processed: {e}")
            return False
    def get_repo_data(self, repo_url: str):
        """
        Get the repository data from Supabase.
        """
        if not self.check_client():
            return None
        try:
            response = self.client.table("repositories").select("*").eq("repo_url", repo_url).execute()
            if response.data:
                return response.data[0]
            else:
                return None
        except Exception as e:
            print(f"[Supabase] Error getting repository data: {e}")
            return None

    def get_vector_store_path(self, repo_url: str):
        """
        Get the vector store path from Supabase.
        """
        if not self.check_client():
            return None
        try:
            response = self.client.table("repositories").select("vector_store_path").eq("repo_url", repo_url).execute()
            return response.data[0]["vector_store_path"]
        except Exception as e:
            print(f"[Supabase] Error getting vector store path: {e}")
            return None

    # ============ Chat History Methods ============

    def create_chat_session(
        self,
        user_id: str,
        repo_url: str,
        title: Optional[str] = None,
    ) -> Optional[str]:
        """
        Create a new chat session in chat_history.
        
        Args:
            user_id: User ID
            repo_url: Repository URL
            title: Optional session title
            
        Returns:
            chat_id (UUID) if successful, None otherwise
        """
        if not self.check_client():
            return None
        
        try:
            data = {
                "user_id": user_id,
                "repo_url": repo_url,
                "title": title,
                "message_count": 0,
                "preview_text": "",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
            response = self.client.table("chat_history").insert(data).execute()
            if response.data:
                chat_id = response.data[0]["id"]
                print(f"[Supabase] Created chat session: {chat_id}")
                return chat_id
            return None
        except Exception as e:
            print(f"[Supabase] Error creating chat session: {e}")
            return None

    def get_chat_session(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a chat session by ID.
        
        Args:
            chat_id: Chat session ID
            
        Returns:
            Chat session dict if found, None otherwise
        """
        if not self.check_client():
            return None
        
        try:
            response = self.client.table("chat_history").select("*").eq("id", chat_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"[Supabase] Error getting chat session: {e}")
            return None

    def update_chat_session(
        self,
        chat_id: str,
        title: Optional[str] = None,
        preview_text: Optional[str] = None,
        message_count: Optional[int] = None,
    ) -> bool:
        """
        Update chat session metadata.
        
        Args:
            chat_id: Chat session ID
            title: Optional new title
            preview_text: Optional preview text (last message snippet)
            message_count: Optional message count
            
        Returns:
            True if successful, False otherwise
        """
        if not self.check_client():
            return False
        
        try:
            data: Dict[str, Any] = {"updated_at": datetime.now().isoformat()}
            if title is not None:
                data["title"] = title
            if preview_text is not None:
                data["preview_text"] = preview_text
            if message_count is not None:
                data["message_count"] = message_count
            
            self.client.table("chat_history").update(data).eq("id", chat_id).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error updating chat session: {e}")
            return False

    def add_chat_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Add a message to a chat session.
        
        Args:
            chat_id: Chat session ID
            role: 'user' or 'assistant'
            content: Message content
            metadata: Optional metadata (sources, token count, etc.)
            
        Returns:
            message_id if successful, None otherwise
        """
        if not self.check_client():
            return None
        
        try:
            data = {
                "chat_id": chat_id,
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "created_at": datetime.now().isoformat(),
            }
            response = self.client.table("chat_messages").insert(data).execute()
            if response.data:
                return response.data[0]["id"]
            return None
        except Exception as e:
            print(f"[Supabase] Error adding chat message: {e}")
            return None

    def get_chat_messages(
        self,
        chat_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Get messages for a chat session.
        
        Args:
            chat_id: Chat session ID
            limit: Maximum number of messages to return
            offset: Number of messages to skip
            
        Returns:
            List of message dicts, ordered by created_at ascending
        """
        if not self.check_client():
            return []
        
        try:
            response = (
                self.client.table("chat_messages")
                .select("*")
                .eq("chat_id", chat_id)
                .order("created_at", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return response.data or []
        except Exception as e:
            print(f"[Supabase] Error getting chat messages: {e}")
            return []

    def get_user_chat_sessions(
        self,
        user_id: str,
        repo_url: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Get all chat sessions for a user.
        
        Args:
            user_id: User ID
            repo_url: Optional filter by repository URL
            limit: Maximum number of sessions to return
            
        Returns:
            List of chat session dicts, ordered by updated_at descending
        """
        if not self.check_client():
            return []
        
        try:
            query = (
                self.client.table("chat_history")
                .select("*")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .limit(limit)
            )
            if repo_url:
                query = query.eq("repo_url", repo_url)
            
            response = query.execute()
            return response.data or []
        except Exception as e:
            print(f"[Supabase] Error getting user chat sessions: {e}")
            return []

    def delete_chat_session(self, chat_id: str) -> bool:
        """
        Delete a chat session and all its messages.
        
        Args:
            chat_id: Chat session ID
            
        Returns:
            True if successful, False otherwise
        """
        if not self.check_client():
            return False
        
        try:
            # Messages will be cascade deleted due to foreign key constraint
            self.client.table("chat_history").delete().eq("id", chat_id).execute()
            print(f"[Supabase] Deleted chat session: {chat_id}")
            return True
        except Exception as e:
            print(f"[Supabase] Error deleting chat session: {e}")
            return False

    def add_chat_messages_batch(
        self,
        chat_id: str,
        messages: List[Dict[str, Any]],
    ) -> bool:
        """
        Add multiple messages to a chat session in a single batch.
        
        Args:
            chat_id: Chat session ID
            messages: List of message dicts [{"role": "...", "content": "...", "metadata": {...}}]
            
        Returns:
            True if successful, False otherwise
        """
        if not self.check_client():
            return False
        
        try:
            now = datetime.now().isoformat()
            data = [
                {
                    "chat_id": chat_id,
                    "role": msg.get("role"),
                    "content": msg.get("content"),
                    "metadata": msg.get("metadata", {}),
                    "created_at": now,
                }
                for msg in messages
            ]
            self.client.table("chat_messages").insert(data).execute()
            return True
        except Exception as e:
            print(f"[Supabase] Error adding chat messages batch: {e}")
            return False


def update_repo_vector_path(repo_url: str, vector_store_path: str):
    """
    Helper function to update repository vector path in Supabase.
    """
    client = SupabaseClient()
    return client.update_repository_vector_path(repo_url, vector_store_path)


