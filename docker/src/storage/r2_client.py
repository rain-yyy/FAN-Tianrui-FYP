"""Cloudflare R2 storage client for uploading wiki data."""

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
import json
from urllib.parse import urlparse
import boto3
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config


import dotenv

dotenv.load_dotenv()


class R2Client:
    """Client for uploading files to Cloudflare R2 storage."""

    def __init__(
        self,
        account_id: Optional[str] = None,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        bucket_name: Optional[str] = None,
        custom_domain: Optional[str] = None,
    ):
        """
        Initialize R2 client with credentials.

        Args:
            account_id: Cloudflare Account ID
            access_key_id: R2 Access Key ID
            secret_access_key: R2 Secret Access Key
            bucket_name: R2 Bucket name
            custom_domain: Custom domain for public access (e.g., https://r2.example.com)
        """
        self.account_id = account_id or os.getenv("R2_ACCOUNT_ID")
        self.access_key_id = access_key_id or os.getenv("R2_ACCESS_KEY_ID")
        self.secret_access_key = secret_access_key or os.getenv("R2_SECRET_ACCESS_KEY")
        self.bucket_name = bucket_name or os.getenv("R2_BUCKET_NAME")
        self.custom_domain = custom_domain or os.getenv("R2_CUSTOM_DOMAIN")

        if not all([self.account_id, self.access_key_id, self.secret_access_key, self.bucket_name]):
            raise ValueError(
                "Missing R2 credentials. Please set R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, "
                "R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME environment variables."
            )

        # Configure boto3 client for R2
        # According to Cloudflare docs: region_name="auto" is required by SDK but not used by R2
        endpoint_url = f"https://{self.account_id}.r2.cloudflarestorage.com"
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name="auto",  # Required by SDK but not used by R2
            config=Config(
                signature_version="s3v4",
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )

    def _extract_repo_name(self, repo_url: str) -> str:
        """
        Extract repository name from URL or path
        """
        # Remove .git suffix if present
        repo_url = repo_url.rstrip('/').replace('.git', '')
        
        # Extract the last part of the path
        if '/' in repo_url:
            repo_name = repo_url.split('/')[-1]
        elif '\\' in repo_url:
            repo_name = repo_url.split('\\')[-1]
        else:
            repo_name = repo_url
            
        # Clean up the name (remove invalid characters)
        repo_name = repo_name.replace(' ', '-')
        return repo_name or 'unknown-repo'
    
    def _generate_date(self) -> str:
        """Generate date string in format YYYYMMDD."""
        return datetime.now().strftime("%Y%m%d")

    def _get_r2_path(self, repo_url: str, filename: str) -> str:
        """
        Generate R2 object path.

        Args:
            repo_url: Repository URL or path
            filename: File name or relative path

        Returns:
            R2 object key (path)
        """
        repo_name = self._extract_repo_name(repo_url)
        date = self._generate_date()
        return f"{repo_name}/{date}/{filename}"

    def upload_file(
        self,
        local_path: Path,
        r2_key: str,
        content_type: str = "application/json",
        max_retries: int = 3,
    ) -> bool:
        """
        Upload a single file to R2.

        Args:
            local_path: Local file path
            r2_key: R2 object key (path in bucket)
            content_type: MIME type of the file
            max_retries: Maximum number of retry attempts

        Returns:
            True if upload successful, False otherwise
        """
        if not local_path.exists():
            print(f"[ERROR] File not found: {local_path}")
            return False

        for attempt in range(max_retries):
            try:
                with open(local_path, "rb") as f:
                    self.s3_client.put_object(
                        Bucket=self.bucket_name,
                        Key=r2_key,
                        Body=f.read(),
                        ContentType=content_type,
                    )
                print(f"[INFO] Successfully uploaded: {r2_key}")
                return True
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                error_message = e.response.get("Error", {}).get("Message", str(e))
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    print(f"[WARN] Upload failed (attempt {attempt + 1}/{max_retries}): {error_code} - {error_message}")
                    print(f"[WARN] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"[ERROR] Failed to upload {r2_key} after {max_retries} attempts")
                    print(f"[ERROR] Error Code: {error_code}, Message: {error_message}")
                    return False
            except BotoCoreError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"[WARN] BotoCore error (attempt {attempt + 1}/{max_retries}): {e}")
                    print(f"[WARN] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"[ERROR] BotoCore error uploading {r2_key}: {e}")
                    return False
            except Exception as e:
                print(f"[ERROR] Unexpected error uploading {r2_key}: {type(e).__name__}: {e}")
                return False

        return False

    def upload_json_data(
        self,
        data: dict,
        r2_key: str,
        max_retries: int = 3,
    ) -> bool:
        """
        Upload JSON data directly to R2.

        Args:
            data: Dictionary to upload as JSON
            r2_key: R2 object key (path in bucket)
            max_retries: Maximum number of retry attempts

        Returns:
            True if upload successful, False otherwise
        """
        json_str = json.dumps(data, indent=2, ensure_ascii=False)
        json_bytes = json_str.encode("utf-8")

        for attempt in range(max_retries):
            try:
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=r2_key,
                    Body=json_bytes,
                    ContentType="application/json; charset=utf-8",
                )
                print(f"[INFO] Successfully uploaded JSON: {r2_key}")
                return True
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                error_message = e.response.get("Error", {}).get("Message", str(e))
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"[WARN] Upload failed (attempt {attempt + 1}/{max_retries}): {error_code} - {error_message}")
                    print(f"[WARN] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"[ERROR] Failed to upload JSON to {r2_key} after {max_retries} attempts")
                    print(f"[ERROR] Error Code: {error_code}, Message: {error_message}")
                    return False
            except BotoCoreError as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    print(f"[WARN] BotoCore error (attempt {attempt + 1}/{max_retries}): {e}")
                    print(f"[WARN] Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"[ERROR] BotoCore error uploading JSON to {r2_key}: {e}")
                    return False
            except Exception as e:
                print(f"[ERROR] Unexpected error uploading JSON to {r2_key}: {type(e).__name__}: {e}")
                return False

        return False

    def get_public_url(self, r2_key: str) -> str:
        """
        Generate public URL for an R2 object.

        Args:
            r2_key: R2 object key (path in bucket)

        Returns:
            Public URL
        """
        if self.custom_domain:
            # Remove leading slash if present
            domain = self.custom_domain.rstrip("/")
            # Ensure r2_key doesn't start with slash
            r2_key_clean = r2_key.lstrip("/")
            return f"{domain}/{r2_key_clean}"
        else:
            # Use R2 public bucket URL format
            # Format: https://<bucket-name>.<account-id>.r2.cloudflarestorage.com/<key>
            # Note: This requires the bucket to be configured as public
            r2_key_clean = r2_key.lstrip("/")
            return f"https://{self.bucket_name}.{self.account_id}.r2.cloudflarestorage.com/{r2_key_clean}"

    def upload_directory(
        self,
        local_dir: Path,
        r2_base_path: str,
        pattern: str = "*.json",
    ) -> List[Tuple[str, bool]]:
        """
        Upload all files matching pattern from a directory to R2.

        Args:
            local_dir: Local directory path
            r2_base_path: Base path in R2 (e.g., "repo_hash/timestamp/sections")
            pattern: File pattern to match (default: "*.json")

        Returns:
            List of tuples (r2_key, success)
        """
        results = []
        if not local_dir.exists() or not local_dir.is_dir():
            print(f"[WARN] Directory not found: {local_dir}")
            return results

        for file_path in local_dir.glob(pattern):
            if file_path.is_file():
                # Preserve filename in R2 path
                filename = file_path.name
                r2_key = f"{r2_base_path}/{filename}" if r2_base_path else filename
                success = self.upload_file(file_path, r2_key)
                results.append((r2_key, success))

        return results

    def list_objects_by_prefix(self, prefix: str) -> List[str]:
        """
        List all object keys under a given prefix.

        Args:
            prefix: R2 object key prefix (e.g., "repo_name/")

        Returns:
            List of object keys
        """
        keys = []
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                contents = page.get("Contents", [])
                for obj in contents:
                    keys.append(obj["Key"])
        except ClientError as e:
            print(f"[ERROR] Failed to list objects with prefix '{prefix}': {e}")
        except BotoCoreError as e:
            print(f"[ERROR] BotoCore error listing objects: {e}")
        return keys

    def delete_objects_by_prefix(self, prefix: str, max_retries: int = 3) -> bool:
        """
        Delete all objects under a given prefix (recursive folder deletion).

        Args:
            prefix: R2 object key prefix (e.g., "repo_name/")
            max_retries: Maximum number of retry attempts

        Returns:
            True if all deletions successful, False otherwise
        """
        # List all objects with this prefix
        keys = self.list_objects_by_prefix(prefix)
        
        if not keys:
            print(f"[INFO] No objects found with prefix '{prefix}'")
            return True
        
        print(f"[INFO] Found {len(keys)} objects to delete under prefix '{prefix}'")
        
        # R2/S3 delete_objects can handle up to 1000 objects per request
        batch_size = 1000
        all_success = True
        
        for i in range(0, len(keys), batch_size):
            batch_keys = keys[i:i + batch_size]
            delete_request = {"Objects": [{"Key": key} for key in batch_keys]}
            
            for attempt in range(max_retries):
                try:
                    response = self.s3_client.delete_objects(
                        Bucket=self.bucket_name,
                        Delete=delete_request
                    )
                    
                    # Check for errors in response
                    errors = response.get("Errors", [])
                    if errors:
                        print(f"[WARN] Some objects failed to delete: {errors}")
                        all_success = False
                    else:
                        deleted_count = len(response.get("Deleted", []))
                        print(f"[INFO] Successfully deleted {deleted_count} objects (batch {i // batch_size + 1})")
                    break
                    
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "Unknown")
                    error_message = e.response.get("Error", {}).get("Message", str(e))
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        print(f"[WARN] Delete failed (attempt {attempt + 1}/{max_retries}): {error_code} - {error_message}")
                        print(f"[WARN] Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"[ERROR] Failed to delete objects after {max_retries} attempts")
                        all_success = False
                        
                except BotoCoreError as e:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        print(f"[WARN] BotoCore error (attempt {attempt + 1}/{max_retries}): {e}")
                        time.sleep(wait_time)
                    else:
                        print(f"[ERROR] BotoCore error deleting objects: {e}")
                        all_success = False
        
        return all_success

    # ============ Chat History Storage Methods ============

    def get_chat_r2_key(self, user_id: str, chat_id: str) -> str:
        """
        Generate R2 key for a chat session.

        Args:
            user_id: User ID
            chat_id: Chat session ID

        Returns:
            R2 object key (e.g., "chats/user_id/chat_id.json")
        """
        return f"chats/{user_id}/{chat_id}.json"

    def upload_chat_snapshot(
        self,
        user_id: str,
        chat_id: str,
        messages: List[dict],
        metadata: Optional[dict] = None,
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        Upload chat history snapshot to R2.

        Args:
            user_id: User ID
            chat_id: Chat session ID
            messages: List of message dicts [{"role": "user/assistant", "content": "..."}]
            metadata: Optional metadata dict (repo_url, title, etc.)
            max_retries: Maximum number of retry attempts

        Returns:
            R2 key if successful, None otherwise
        """
        r2_key = self.get_chat_r2_key(user_id, chat_id)
        
        # Build the snapshot data
        snapshot = {
            "chat_id": chat_id,
            "user_id": user_id,
            "messages": messages,
            "message_count": len(messages),
            "updated_at": datetime.now().isoformat(),
        }
        if metadata:
            snapshot["metadata"] = metadata

        success = self.upload_json_data(snapshot, r2_key, max_retries=max_retries)
        if success:
            print(f"[R2] Chat snapshot uploaded: {r2_key} ({len(messages)} messages)")
            return r2_key
        return None

    def download_chat_snapshot(
        self,
        user_id: str,
        chat_id: str,
    ) -> Optional[dict]:
        """
        Download chat history snapshot from R2.

        Args:
            user_id: User ID
            chat_id: Chat session ID

        Returns:
            Chat snapshot dict if successful, None otherwise
        """
        r2_key = self.get_chat_r2_key(user_id, chat_id)
        
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=r2_key,
            )
            content = response["Body"].read().decode("utf-8")
            snapshot = json.loads(content)
            print(f"[R2] Chat snapshot downloaded: {r2_key}")
            return snapshot
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "NoSuchKey":
                print(f"[R2] Chat snapshot not found: {r2_key}")
            else:
                print(f"[R2] Error downloading chat snapshot: {error_code}")
            return None
        except Exception as e:
            print(f"[R2] Unexpected error downloading chat snapshot: {e}")
            return None

def transform_url(url: str) -> str:
    """
    Transform R2 URL to custom domain URL
    """
    if not url:
        return url
    if "r2.cloudflarestorage.com" in url:
        try:
            url_obj = urlparse(url)
            return f"https://cityu-fyp.livelive.fun{url_obj.path}"
        except Exception as e:
            print(f"[ERROR] Failed to transform URL: {e}")
            return url
    return url


def upload_chat_snapshot(
    user_id: str,
    chat_id: str,
    messages: List[dict],
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """
    Helper function to upload chat snapshot to R2.
    
    Args:
        user_id: User ID
        chat_id: Chat session ID
        messages: List of message dicts
        metadata: Optional metadata dict
        
    Returns:
        R2 key if successful, None otherwise
    """
    try:
        client = R2Client()
        return client.upload_chat_snapshot(user_id, chat_id, messages, metadata)
    except ValueError as e:
        print(f"[R2] Client initialization failed: {e}")
        return None


def download_chat_snapshot(
    user_id: str,
    chat_id: str,
) -> Optional[dict]:
    """
    Helper function to download chat snapshot from R2.
    
    Args:
        user_id: User ID
        chat_id: Chat session ID
        
    Returns:
        Chat snapshot dict if successful, None otherwise
    """
    try:
        client = R2Client()
        return client.download_chat_snapshot(user_id, chat_id)
    except ValueError as e:
        print(f"[R2] Client initialization failed: {e}")
        return None


def upload_wiki_to_r2(
    repo_url: str,
    wiki_structure: dict,
    structure_local_path: Optional[Path] = None,
    content_dir: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[List[str]]]:
    """
    Upload wiki structure and content files to R2.

    Args:
        repo_url: Repository URL or path
        wiki_structure: Wiki structure dictionary
        structure_local_path: Local path to wiki_structure.json (optional, will upload dict if not provided)
        content_dir: Local directory containing content JSON files (optional)

    Returns:
        Tuple of (structure_url, content_urls) or (None, None) if upload fails
    """
    try:
        client = R2Client()
    except ValueError as e:
        print(f"[WARN] R2 client initialization failed: {e}")
        print("[INFO] Skipping R2 upload, will return local paths instead.")
        return None, None

    # Generate base path
    repo_name = client._extract_repo_name(repo_url)
    date = client._generate_date()
    base_path = f"{repo_name}/{date}"

    # Delete existing files for this repo (all historical versions)
    repo_prefix = f"{repo_name}/"
    print(f"[INFO] Checking for existing files with prefix '{repo_prefix}'...")
    client.delete_objects_by_prefix(repo_prefix)

    # Upload wiki structure
    structure_key = f"{base_path}/wiki_structure.json"
    if structure_local_path and structure_local_path.exists():
        structure_success = client.upload_file(structure_local_path, structure_key)
    else:
        structure_success = client.upload_json_data(wiki_structure, structure_key)

    if not structure_success:
        print("[ERROR] Failed to upload wiki structure to R2")
        return None, None

    structure_url = client.get_public_url(structure_key)

    # Upload content files if provided
    content_urls = []
    if content_dir and content_dir.exists():
        sections_path = f"{base_path}/sections"
        results = client.upload_directory(content_dir, sections_path)
        if results:
            # Check if at least one file uploaded successfully
            for r2_key, success in results:
                if success:
                    content_urls.append(client.get_public_url(r2_key))
            
            if content_urls:
                print(f"[INFO] Uploaded {len(content_urls)}/{len(results)} content files to R2")
            else:
                print("[WARN] Failed to upload any content files to R2")

    # 对url进行加工，使用自定义域名访问R2
    structure_url = transform_url(structure_url)
    content_urls = [transform_url(url) for url in content_urls]
    
    return structure_url, content_urls if content_urls else None
