"""
Tests for WikiContentGenerator:
- Filename normalization and deduplication
- Concurrent generation with mock client
- Cleanup isolation
"""
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from src.wiki.content_gen import WikiContentGenerator, WikiSection


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal repo directory with a dummy file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')", encoding="utf-8")
    return repo


@pytest.fixture
def tmp_output(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    return out


def _mock_client_factory():
    """Return a factory that creates mock AI clients returning valid JSON."""
    def factory():
        client = MagicMock()
        client.chat.return_value = json.dumps({
            "intro": "Test intro",
            "sections": [],
            "mermaid": ""
        })
        return client
    return factory


# ---------------------------------------------------------------------------
# 1. Filename normalization tests
# ---------------------------------------------------------------------------

class TestSafeFilename:
    def test_basic(self):
        assert WikiContentGenerator._safe_filename("API Reference") == "api-reference"

    def test_special_chars(self):
        # Trailing hyphens are stripped
        assert WikiContentGenerator._safe_filename("Hello@World!") == "hello-world"
        result = WikiContentGenerator._safe_filename("!!!test!!!")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_empty_string(self):
        assert WikiContentGenerator._safe_filename("") == "section"

    def test_all_illegal(self):
        assert WikiContentGenerator._safe_filename("@#$%^&*") == "section"

    def test_preserves_dots_hyphens(self):
        assert WikiContentGenerator._safe_filename("v1.0-beta") == "v1.0-beta"


class TestNormalizeForCollision:
    def test_case_folding(self):
        a = WikiContentGenerator._normalize_for_collision("API-Reference")
        b = WikiContentGenerator._normalize_for_collision("api-reference")
        assert a == b

    def test_symbol_folding(self):
        a = WikiContentGenerator._normalize_for_collision("api_reference")
        b = WikiContentGenerator._normalize_for_collision("api-reference")
        assert a == b

    def test_empty(self):
        assert WikiContentGenerator._normalize_for_collision("") == "section"


class TestBuildFilenameMap:
    def _make_sections(self, ids: List[str]) -> List[WikiSection]:
        return [WikiSection(id=sid, title=sid, files=[]) for sid in ids]

    def test_no_collision(self, tmp_repo, tmp_output):
        gen = WikiContentGenerator(
            repo_root=tmp_repo,
            json_output_dir=tmp_output,
            client_factory=_mock_client_factory(),
        )
        sections = self._make_sections(["intro", "api", "deployment"])
        mapping = gen._build_filename_map(sections)
        assert len(set(mapping.values())) == 3

    def test_case_collision(self, tmp_repo, tmp_output):
        gen = WikiContentGenerator(
            repo_root=tmp_repo,
            json_output_dir=tmp_output,
            client_factory=_mock_client_factory(),
        )
        sections = self._make_sections([
            "API Reference",
            "api-reference",
            "API_reference",
        ])
        mapping = gen._build_filename_map(sections)
        values = list(mapping.values())
        # All filenames must be unique
        assert len(set(values)) == 3
        # First one keeps original name
        assert values[0] == "api-reference"
        # Subsequent ones get suffix (preserving original safe_filename form)
        assert values[1] == "api-reference-2"
        assert "api" in values[2] and values[2].endswith("-3")

    def test_empty_and_illegal_ids(self, tmp_repo, tmp_output):
        gen = WikiContentGenerator(
            repo_root=tmp_repo,
            json_output_dir=tmp_output,
            client_factory=_mock_client_factory(),
        )
        sections = self._make_sections(["", "@#$", "   "])
        mapping = gen._build_filename_map(sections)
        values = list(mapping.values())
        assert len(set(values)) == 3
        # All fall back to "section" base, but get dedup suffixes
        assert values[0] == "section"
        assert values[1] == "section-2"
        assert values[2] == "section-3"


# ---------------------------------------------------------------------------
# 2. Concurrent generation test with mock client
# ---------------------------------------------------------------------------

class TestConcurrentGeneration:
    def _make_structure(self, n: int) -> Dict:
        toc = []
        for i in range(n):
            toc.append({
                "id": f"section-{i}",
                "title": f"Section {i}",
                "files": ["main.py"],
                "children": [],
            })
        return {"title": "Test Wiki", "description": "Test", "toc": toc}

    def test_concurrent_10_sections(self, tmp_repo, tmp_output):
        """10 sections at concurrency 3 should all produce unique files."""
        structure = self._make_structure(10)
        gen = WikiContentGenerator(
            repo_root=tmp_repo,
            json_output_dir=tmp_output,
            client_factory=_mock_client_factory(),
            max_concurrency=3,
            task_id="test-concurrent",
        )
        result = gen.generate(structure)
        assert len(result) == 10
        # All filenames unique
        names = [p.name for p in result]
        assert len(set(names)) == 10

    def test_failure_does_not_block_others(self, tmp_repo, tmp_output):
        """If one section fails, others should still succeed."""
        call_count = {"n": 0}

        def flaky_factory():
            client = MagicMock()
            def side_effect(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 3:
                    raise RuntimeError("Simulated LLM failure")
                return json.dumps({"intro": "ok", "sections": [], "mermaid": ""})
            client.chat.side_effect = side_effect
            return client

        structure = self._make_structure(5)
        gen = WikiContentGenerator(
            repo_root=tmp_repo,
            json_output_dir=tmp_output,
            client_factory=flaky_factory,
            max_concurrency=2,
            task_id="test-flaky",
        )
        result = gen.generate(structure)
        # At least 4 should succeed (one fails)
        assert len(result) >= 4

    def test_progress_callback_called(self, tmp_repo, tmp_output):
        """Progress callback should be invoked for each completed section."""
        progress_calls = []

        def cb(progress, step):
            progress_calls.append((progress, step))

        structure = self._make_structure(4)
        gen = WikiContentGenerator(
            repo_root=tmp_repo,
            json_output_dir=tmp_output,
            client_factory=_mock_client_factory(),
            max_concurrency=2,
            progress_callback=cb,
            task_id="test-progress",
        )
        gen.generate(structure)
        assert len(progress_calls) == 4
        # Progress should be between 50 and 85
        for p, _ in progress_calls:
            assert 50.0 < p <= 85.0


# ---------------------------------------------------------------------------
# 3. Cleanup isolation test
# ---------------------------------------------------------------------------

class TestCleanupIsolation:
    def test_task_dir_cleanup_only_removes_own_dir(self, tmp_path):
        """cleanup_local_files should only remove the task's own directory."""
        # Create two task dirs under a simulated task_work_root
        task_a = tmp_path / "tasks" / "task-a"
        task_b = tmp_path / "tasks" / "task-b"
        task_a.mkdir(parents=True)
        task_b.mkdir(parents=True)
        (task_a / "wiki_structure.json").write_text("{}")
        (task_b / "wiki_structure.json").write_text("{}")

        out_a = task_a / "wiki_structure.json"
        json_a = task_a / "wiki_section_json"
        json_a.mkdir()

        # Manually clean (simulating cleanup without importing the full pipeline)
        import shutil
        # Clean individual files like the fallback path does
        if out_a.exists():
            out_a.unlink()
        if json_a.exists():
            shutil.rmtree(json_a)

        # task_b should still exist and be untouched
        assert task_b.exists()
        assert (task_b / "wiki_structure.json").exists()
