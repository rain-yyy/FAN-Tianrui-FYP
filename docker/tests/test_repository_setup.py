"""
"Project input" clone step: scripts/setup_repository.py turns a repo URL into
a local checkout under REPO_STORE_ROOT. No LLM, no Supabase, no Qdrant — just
git + filesystem — so these run fast and cheap every time.
"""
import shutil
from pathlib import Path

import pytest
from git import GitCommandError

from scripts.setup_repository import setup_repository
from src.paths import REPO_STORE_ROOT, repo_disk_dirname


def test_setup_repository_clones_to_repo_store_root(test_repo_url):
    repo_path = setup_repository(test_repo_url)
    try:
        expected_dir = (REPO_STORE_ROOT / repo_disk_dirname(test_repo_url)).resolve()
        assert Path(repo_path) == expected_dir
        assert Path(repo_path).is_dir()
        assert any(Path(repo_path).iterdir()), "cloned repo directory is empty"
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)


def test_setup_repository_recloning_discards_stale_local_state(test_repo_url):
    """Re-running setup_repository() for the same URL must wipe whatever was
    there before (docstring: "每次拉取前清理旧目录，避免脏状态"), not merge with it."""
    first_path = setup_repository(test_repo_url)
    stale_marker = Path(first_path) / "PYTEST_STALE_MARKER.txt"
    stale_marker.write_text("stale local edit that should not survive a reclone")

    try:
        second_path = setup_repository(test_repo_url)
        assert second_path == first_path
        assert not (Path(second_path) / "PYTEST_STALE_MARKER.txt").exists()
    finally:
        shutil.rmtree(first_path, ignore_errors=True)


def test_setup_repository_invalid_url_raises_value_error():
    with pytest.raises(ValueError):
        setup_repository("https://github.com/this-owner-does-not-exist-abc123/definitely-not-a-repo-xyz.git")


def test_setup_repository_invalid_url_leaves_no_directory():
    bogus_url = "https://github.com/this-owner-does-not-exist-abc123/definitely-not-a-repo-xyz.git"
    target_dir = REPO_STORE_ROOT / repo_disk_dirname(bogus_url)
    shutil.rmtree(target_dir, ignore_errors=True)

    with pytest.raises(ValueError):
        setup_repository(bogus_url)

    assert not target_dir.exists()
