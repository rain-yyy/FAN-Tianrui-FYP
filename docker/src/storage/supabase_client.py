import os
from typing import Optional, List
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

    def update_repository_vector_path(self, repo_url: str, vector_store_path: str):
        """
        Update the vector_store_path for a repository in Supabase.
        """
        if not self.client:
            print("[Supabase] Client not initialized. Skipping update.")
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

def update_repo_vector_path(repo_url: str, vector_store_path: str):
    """
    Helper function to update repository vector path in Supabase.
    """
    client = SupabaseClient()
    return client.update_repository_vector_path(repo_url, vector_store_path)
