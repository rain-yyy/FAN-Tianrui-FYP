"""Cloudflare R2 storage client for uploading wiki data."""

import os
import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
import json

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

    def _generate_repo_hash(self, repo_url: str) -> str:
        """Generate a short hash from repository URL."""
        hash_obj = hashlib.sha256(repo_url.encode())
        return hash_obj.hexdigest()[:16]
    # TODO: 删除timestamp和repo_hash的逻辑，直接使用日期+repo name
    def _generate_timestamp(self) -> str:
        """Generate timestamp string in format YYYYMMDD_HHMMSS."""
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _get_r2_path(self, repo_url: str, filename: str) -> str:
        """
        Generate R2 object path.

        Args:
            repo_url: Repository URL or path
            filename: File name or relative path

        Returns:
            R2 object key (path)
        """
        repo_hash = self._generate_repo_hash(repo_url)
        timestamp = self._generate_timestamp()
        return f"{repo_hash}/{timestamp}/{filename}"

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


def upload_wiki_to_r2(
    repo_url: str,
    wiki_structure: dict,
    structure_local_path: Optional[Path] = None,
    content_dir: Optional[Path] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Upload wiki structure and content files to R2.

    Args:
        repo_url: Repository URL or path
        wiki_structure: Wiki structure dictionary
        structure_local_path: Local path to wiki_structure.json (optional, will upload dict if not provided)
        content_dir: Local directory containing content JSON files (optional)

    Returns:
        Tuple of (structure_url, content_base_url) or (None, None) if upload fails
    """
    try:
        client = R2Client()
    except ValueError as e:
        print(f"[WARN] R2 client initialization failed: {e}")
        print("[INFO] Skipping R2 upload, will return local paths instead.")
        return None, None

    # Generate base path
    repo_hash = client._generate_repo_hash(repo_url)
    timestamp = client._generate_timestamp()
    base_path = f"{repo_hash}/{timestamp}"

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
    content_base_url = None
    if content_dir and content_dir.exists():
        sections_path = f"{base_path}/sections"
        results = client.upload_directory(content_dir, sections_path)
        if results:
            # Check if at least one file uploaded successfully
            if any(success for _, success in results):
                content_base_url = client.get_public_url(sections_path)
                print(f"[INFO] Uploaded {sum(1 for _, s in results if s)}/{len(results)} content files to R2")
            else:
                print("[WARN] Failed to upload any content files to R2")

    return structure_url, content_base_url
