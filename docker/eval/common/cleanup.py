"""Deletes everything the eval run touched in the shared environment, acting only
on entries recorded in `eval_manifest.json` (never by pattern-matching), so it
can't reach data a real user created.

Removes, per manifest entry: `tasks` rows (via the existing DELETE endpoint),
the `repositories` row, `chat_history`/`chat_messages` rows for any chat_ids
created during Stage 3, Qdrant points filtered by repo_id in both collections,
and the on-disk repo checkout + vector store directories.

Run standalone: `python -m common.cleanup` (from `docker/eval/`), or
`python -m common.cleanup --dry-run` to see what would be removed first.
"""

from __future__ import annotations

import argparse
import shutil

from . import bootstrap  # noqa: F401  (sets up sys.path + .env before src.* imports)
from .config import require_eval_user_id
from .manifest import load_entries


def cleanup(dry_run: bool = False) -> None:
    from qdrant_client import models

    from src.ingestion.vector_store import COLLECTIONS, get_qdrant_client, repo_filter
    from src.paths import REPO_STORE_ROOT, VECTOR_STORE_ROOT
    from src.storage.supabase_client import get_supabase_client

    user_id = require_eval_user_id()
    supabase = get_supabase_client()
    qdrant = None
    try:
        qdrant = get_qdrant_client()
    except ValueError:
        print("[cleanup] QDRANT_URL not configured; skipping Qdrant point deletion")

    entries = load_entries()
    if not entries:
        print("[cleanup] manifest is empty, nothing to do")
        return

    for repo_url, entry in entries.items():
        print(f"\n[cleanup] {repo_url} (repo_id={entry.repo_id})")

        for task_id in entry.task_ids:
            print(f"  - tasks row {task_id}")
            if not dry_run:
                try:
                    supabase.delete_task(task_id, user_id)
                except Exception as exc:
                    print(f"    ! failed: {exc}")

        for chat_id in entry.chat_ids:
            print(f"  - chat session {chat_id}")
            if not dry_run and supabase.client:
                try:
                    supabase.client.table("chat_messages").delete().eq(
                        "chat_id", chat_id
                    ).execute()
                    supabase.client.table("chat_history").delete().eq(
                        "id", chat_id
                    ).execute()
                except Exception as exc:
                    print(f"    ! failed: {exc}")

        if supabase.client:
            normalized = supabase._normalize_repo_url(repo_url)
            print(f"  - repositories row {normalized}")
            if not dry_run:
                try:
                    supabase.client.table("repositories").delete().eq(
                        "repo_url", normalized
                    ).execute()
                except Exception as exc:
                    print(f"    ! failed: {exc}")

        if entry.repo_id and qdrant is not None:
            for collection_name in COLLECTIONS.values():
                print(
                    f"  - qdrant points in {collection_name} (repo_id={entry.repo_id})"
                )
                if not dry_run:
                    try:
                        if qdrant.collection_exists(collection_name):
                            qdrant.delete(
                                collection_name=collection_name,
                                points_selector=models.FilterSelector(
                                    filter=repo_filter(entry.repo_id)
                                ),
                            )
                    except Exception as exc:
                        print(f"    ! failed: {exc}")

        if entry.repo_id:
            for root in (REPO_STORE_ROOT, VECTOR_STORE_ROOT):
                target = root / entry.repo_id
                if target.exists():
                    print(f"  - disk dir {target}")
                    if not dry_run:
                        try:
                            shutil.rmtree(target)
                        except OSError as exc:
                            print(f"    ! failed: {exc}")

    print("\n[cleanup] done" + (" (dry run, nothing deleted)" if dry_run else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    cleanup(dry_run=args.dry_run)
