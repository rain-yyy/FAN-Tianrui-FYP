"""
GitHub REST：仅拉取公开仓库的 stars 与简介；供 DB 缓存，避免前端直连 api.github.com。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

GITHUB_METADATA_TTL_SEC = 86400  # 24h


def coerce_stargazers_int(value: Any) -> Optional[int]:
    """Supabase/JSON 可能返回 float；统一为 int 供 API 与前端使用。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_owner_repo_from_url(repo_url: str) -> Optional[Tuple[str, str]]:
    if not repo_url or not repo_url.strip():
        return None
    parsed = urlsplit(repo_url.strip())
    host = (parsed.netloc or "").lower()
    if "github.com" not in host:
        return None
    path = parsed.path.strip().rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    return parts[0].lower(), parts[1].lower()


def fetch_github_repo_public_metadata(owner: str, name: str) -> Optional[Dict[str, Any]]:
    token = (os.getenv("GITHUB_TOKEN") or "").strip()
    api_url = f"https://api.github.com/repos/{owner}/{name}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "FYP-Wiki-Metadata",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(api_url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except HTTPError as e:
        logger.warning("GitHub API HTTP error %s for %s/%s: %s", e.code, owner, name, e.reason)
        return None
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        logger.warning("GitHub API request failed for %s/%s: %s", owner, name, e)
        return None


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def github_metadata_is_stale(row: Optional[Dict[str, Any]]) -> bool:
    if not row:
        return True
    updated = _parse_ts(row.get("github_metadata_updated_at"))
    if updated is None:
        return True
    age_sec = (datetime.now(timezone.utc) - updated).total_seconds()
    return age_sec >= GITHUB_METADATA_TTL_SEC


def github_metadata_needs_refresh(row: Optional[Dict[str, Any]]) -> bool:
    """无行、TTL 到期，或从未成功写入过 GitHub 公开字段时返回 True。"""
    if not row:
        return True
    if github_metadata_is_stale(row):
        return True
    if (
        row.get("stargazers_count") is None
        and row.get("github_short_description") is None
    ):
        return True
    return False


def apply_github_response_to_row_fields(data: Dict[str, Any]) -> Dict[str, Any]:
    desc = data.get("description")
    stars = data.get("stargazers_count")
    star_int: Optional[int] = None
    if isinstance(stars, int):
        star_int = stars
    elif isinstance(stars, float) and stars.is_integer():
        star_int = int(stars)
    return {
        "github_short_description": desc if isinstance(desc, str) else None,
        "stargazers_count": star_int,
    }


def refresh_github_metadata_for_repo_url(
    supabase_client: Any,
    repo_url: str,
    *,
    force_if_missing: bool = False,
) -> bool:
    """
    若 TTL 已过或强制补全缺失数据，则请求 GitHub 并写入 repositories 行。
    不修改 LLM 生成的 description 列。
    """
    if not supabase_client or not getattr(supabase_client, "client", None):
        return False

    repo_url = supabase_client._normalize_repo_url(repo_url)
    parsed = parse_owner_repo_from_url(repo_url)
    if not parsed:
        return False

    row = supabase_client.get_repo_information(repo_url)
    persist_url = (
        supabase_client._normalize_repo_url(row["repo_url"])
        if row and row.get("repo_url")
        else repo_url
    )

    if row is not None and not github_metadata_needs_refresh(row) and not force_if_missing:
        return True
    if force_if_missing and row is not None and not github_metadata_is_stale(row):
        stars = row.get("stargazers_count")
        gdesc = row.get("github_short_description")
        if stars is not None or (isinstance(gdesc, str) and gdesc.strip()):
            return True

    owner, name = parsed
    remote = fetch_github_repo_public_metadata(owner, name)
    if not remote:
        return False

    fields = apply_github_response_to_row_fields(remote)
    return bool(
        supabase_client.update_github_public_metadata(
            persist_url,
            github_short_description=fields.get("github_short_description"),
            stargazers_count=fields.get("stargazers_count"),
        )
    )


def refresh_github_metadata_batch(supabase_client: Any, repo_urls: list[str]) -> None:
    """顺序刷新，减轻并发命中速率限制。"""
    seen: set[str] = set()
    for raw in repo_urls:
        key = (supabase_client._normalize_repo_url(raw) if supabase_client else raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            refresh_github_metadata_for_repo_url(supabase_client, key, force_if_missing=False)
        except Exception as e:
            logger.warning("github metadata refresh failed for %s: %s", key, e)
        time.sleep(0.35)
