"""_check_wiki_generation_cache must not trust a fresh `repositories.last_updated`
row alone — that only reflects Supabase/R2 state, not whether this machine's
disk actually still has the repo clone (e.g. after a fresh persistent volume
on a redeploy). No LLM/Supabase/network involved: fast, always runs.
"""

import shutil
from datetime import UTC, datetime, timedelta

from src.core.wiki_pipeline import _check_wiki_generation_cache
from src.paths import REPO_STORE_ROOT, repo_disk_dirname


class _FakeSupabaseClient:
    def __init__(self, repo_info):
        self._repo_info = repo_info
        self.wiki_artifacts_from_row_called_with = None

    def get_repo_information(self, url_link):
        return self._repo_info

    def wiki_artifacts_from_row(self, repo_info, url_link):
        self.wiki_artifacts_from_row_called_with = (repo_info, url_link)
        return {"cached": True}


def _fresh_repo_info():
    return {"last_updated": datetime.now(UTC).isoformat()}


def test_cache_hit_when_fresh_and_local_repo_dir_present(tmp_path_factory):
    url = "https://github.com/example/fresh-and-present"
    repo_dir = REPO_STORE_ROOT / repo_disk_dirname(url)
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text("hello")
    try:
        client = _FakeSupabaseClient(_fresh_repo_info())
        result = _check_wiki_generation_cache(client, url)
        assert result == {"cached": True}
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


def test_cache_miss_when_fresh_row_but_local_repo_dir_missing():
    """This is the bug: repositories.last_updated is recent (R2/Supabase have
    the wiki), but the repo was never cloned onto this machine's disk (e.g. a
    new Fly volume) — must force full regeneration, not serve a cache pointing
    at files that don't exist locally.
    """
    url = "https://github.com/example/fresh-but-missing-locally"
    repo_dir = REPO_STORE_ROOT / repo_disk_dirname(url)
    shutil.rmtree(repo_dir, ignore_errors=True)

    client = _FakeSupabaseClient(_fresh_repo_info())
    result = _check_wiki_generation_cache(client, url)
    assert result is None
    assert client.wiki_artifacts_from_row_called_with is None


def test_cache_miss_when_local_repo_dir_present_but_empty():
    url = "https://github.com/example/present-but-empty"
    repo_dir = REPO_STORE_ROOT / repo_disk_dirname(url)
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        client = _FakeSupabaseClient(_fresh_repo_info())
        result = _check_wiki_generation_cache(client, url)
        assert result is None
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


def test_cache_miss_when_stale_regardless_of_local_dir():
    url = "https://github.com/example/stale-row"
    repo_dir = REPO_STORE_ROOT / repo_disk_dirname(url)
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "README.md").write_text("hello")
    try:
        stale_info = {
            "last_updated": (datetime.now(UTC) - timedelta(days=30)).isoformat()
        }
        client = _FakeSupabaseClient(stale_info)
        result = _check_wiki_generation_cache(client, url)
        assert result is None
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)
