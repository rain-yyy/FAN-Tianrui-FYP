"""Repo LOC counting for the eval report's "up to NK LOC" claim.

Reuses `find_relevant_files` (the exact git-tracked, extension-filtered file
set the ingestion pipeline itself processes) rather than a generic `cloc` run,
so the reported LOC matches what the tool actually ingests. Falls back to a
plain line count; `cloc` (if installed) is used only as an optional
cross-check, never required.
"""

from __future__ import annotations

import shutil
import subprocess

from src.ingestion.file_processor import find_relevant_files


def count_ingested_loc(repo_path: str) -> int:
    """Sum of line counts across every file the ingestion pipeline would process."""
    total = 0
    for file_path in find_relevant_files(repo_path):
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                total += sum(1 for _ in f)
        except OSError:
            continue
    return total


def count_loc_via_cloc(repo_path: str) -> int | None:
    """Optional cross-check using `cloc` if it's on PATH; returns None otherwise."""
    if shutil.which("cloc") is None:
        return None
    try:
        proc = subprocess.run(
            ["cloc", "--json", "--quiet", repo_path],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        import json

        data = json.loads(proc.stdout)
        return int(data.get("SUM", {}).get("code", 0))
    except (subprocess.SubprocessError, ValueError, OSError):
        return None
