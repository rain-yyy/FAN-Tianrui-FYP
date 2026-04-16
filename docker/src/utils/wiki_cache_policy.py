"""Wiki /generate 短路缓存策略（无 Supabase/boto3 依赖，便于单测）。

生产环境优先在 PostgreSQL 内比较 `repositories.last_updated` 与 `timezone('utc', now())`（见 rpc
`repository_wiki_cache_ttl_fresh`）。本模块为 RPC 不可用或单测时的回退逻辑，仍只读取表中时间戳，
「当前时刻」回退为应用进程 UTC（与 DB 有微小偏差可能）。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

# /generate 短路缓存：超过该整天数则不应短路；窗口边界与 SQL RPC 一致（含边界当天仍可命中缓存）。
WIKI_GENERATION_CACHE_MAX_AGE_DAYS = 2


def parse_supabase_timestamp(raw: Any) -> Optional[datetime]:
    """将 Supabase 返回的时间戳解析为带 UTC 时区的 datetime。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            if s.endswith("Z") and "+00:00" not in s and "T" in s:
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def wiki_generation_cache_is_stale(repo_info: dict, max_age_days: int) -> bool:
    """
    RPC 回退路径：仅使用行内 `last_updated`（Supabase 返回值），与进程 UTC 比较。
    与 SQL 一致：过期当且仅当 last_updated < (now_utc - max_age_days)；
    last_updated >= 该阈值则视为未过期。
    """
    if max_age_days < 0:
        return False
    dt = parse_supabase_timestamp(repo_info.get("last_updated"))
    if dt is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return dt < cutoff
