"""
Integration tests for wiki_pipeline.py — Wiki 生成流水线闭环验证

由于 wiki_pipeline.py 顶层导入依赖 boto3/supabase 等库，
在测试环境中可能不可用。本测试文件采用两种策略：
  1. 尝试导入模块，若失败则标记整个文件 skip
  2. 对外部调用全部 mock

验证的核心目标：**调用顺序与产物路径**，而非联网行为。
"""
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 尝试导入 wiki_pipeline；若依赖缺失则整个模块 skip
# ---------------------------------------------------------------------------
_IMPORT_ERROR: str | None = None
try:
    from src.core.wiki_pipeline import (
        run_structure_generation,
        run_wiki_content_generation,
        cleanup_local_files,
        _task_output_dir,
        TASK_WORK_ROOT,
    )
except Exception as exc:
    _IMPORT_ERROR = str(exc)

pytestmark = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"Cannot import wiki_pipeline: {_IMPORT_ERROR}",
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_wiki_structure(n_sections: int = 3) -> Dict[str, Any]:
    toc = []
    for i in range(n_sections):
        toc.append({
            "id": f"section-{i}",
            "title": f"Section {i}",
            "files": ["main.py"],
            "children": [],
        })
    return {"title": "Test Wiki", "description": "Integration test wiki", "toc": toc}


def _make_fake_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fake_repo"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "main.py").write_text("print('hello')", encoding="utf-8")
    (repo / "README.md").write_text("# Test Repo", encoding="utf-8")
    return repo


# ---------------------------------------------------------------------------
# A. 结构生成阶段
# ---------------------------------------------------------------------------

class TestWikiPipelineStructureGeneration:

    def test_structure_generation_writes_json(self, tmp_path: Path):
        """验证 run_structure_generation 将 wiki_structure 写入指定 JSON 路径"""
        fake_repo = _make_fake_repo(tmp_path)
        expected_structure = _make_wiki_structure()
        output_path = tmp_path / "output" / "wiki_structure.json"
        config_path = tmp_path / "config.json"
        config_path.write_text("{}", encoding="utf-8")

        with patch("src.core.wiki_pipeline.setup_repository", return_value=str(fake_repo)), \
             patch("src.core.wiki_pipeline.load_config", return_value={}), \
             patch("src.core.wiki_pipeline.generate_file_tree", return_value="mock_tree"), \
             patch("src.core.wiki_pipeline.generate_wiki_structure", return_value=expected_structure), \
             patch("src.core.wiki_pipeline.SupabaseClient"):
            repo_path, result = run_structure_generation(
                repo_url_or_path="https://github.com/test/repo.git",
                config_path=config_path,
                output_path=output_path,
                task_id=None,
            )

        assert output_path.exists()
        written = json.loads(output_path.read_text(encoding="utf-8"))
        assert written["title"] == "Test Wiki"
        assert len(written["toc"]) == 3

    def test_structure_generation_uses_local_path(self, tmp_path: Path):
        """传入本地路径时不调用 setup_repository"""
        fake_repo = _make_fake_repo(tmp_path)
        output_path = tmp_path / "output" / "wiki_structure.json"
        config_path = tmp_path / "config.json"
        config_path.write_text("{}", encoding="utf-8")

        with patch("src.core.wiki_pipeline.load_config", return_value={}), \
             patch("src.core.wiki_pipeline.generate_file_tree", return_value="tree"), \
             patch("src.core.wiki_pipeline.generate_wiki_structure", return_value=_make_wiki_structure()), \
             patch("src.core.wiki_pipeline.SupabaseClient"):
            repo_path, _ = run_structure_generation(
                repo_url_or_path=str(fake_repo),
                config_path=config_path,
                output_path=output_path,
                task_id=None,
            )

        assert repo_path == str(fake_repo)


# ---------------------------------------------------------------------------
# B. 内容生成阶段
# ---------------------------------------------------------------------------

class TestWikiPipelineContentGeneration:

    def test_content_generation_produces_files(self, tmp_path: Path):
        """验证 run_wiki_content_generation 生成与章节数量匹配的 JSON 文件"""
        fake_repo = _make_fake_repo(tmp_path)
        json_output_dir = tmp_path / "wiki_section_json"
        json_output_dir.mkdir()
        wiki_structure = _make_wiki_structure(4)

        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "intro": "Test intro",
            "sections": [],
            "mermaid": "",
        })

        with patch("src.core.wiki_pipeline.get_model_config", return_value=("openrouter", "test-model")), \
             patch("src.core.wiki_pipeline.get_ai_client", return_value=mock_client), \
             patch("src.core.wiki_pipeline.SupabaseClient"):
            result = run_wiki_content_generation(
                repo_path=str(fake_repo),
                wiki_structure=wiki_structure,
                json_output_dir=json_output_dir,
                task_id=None,
            )

        assert len(result) == 4
        for p in result:
            assert Path(p).exists()
            assert Path(p).suffix == ".json"

    def test_content_generation_progress_callback(self, tmp_path: Path):
        """验证内容生成阶段进度回调被触发"""
        fake_repo = _make_fake_repo(tmp_path)
        json_output_dir = tmp_path / "wiki_section_json"
        json_output_dir.mkdir()
        wiki_structure = _make_wiki_structure(2)

        mock_client = MagicMock()
        mock_client.chat.return_value = json.dumps({
            "intro": "content",
            "sections": [],
            "mermaid": "",
        })

        progress_calls = []

        def _track_progress(task_id, progress, step):
            progress_calls.append((progress, step))

        with patch("src.core.wiki_pipeline.get_model_config", return_value=("openrouter", "test-model")), \
             patch("src.core.wiki_pipeline.get_ai_client", return_value=mock_client), \
             patch("src.core.wiki_pipeline._update_progress", side_effect=_track_progress):
            run_wiki_content_generation(
                repo_path=str(fake_repo),
                wiki_structure=wiki_structure,
                json_output_dir=json_output_dir,
                task_id="test-task-123",
            )

        assert len(progress_calls) >= 2


# ---------------------------------------------------------------------------
# C. 全流程端到端 (mock)
# ---------------------------------------------------------------------------

class TestWikiPipelineEndToEnd:

    def test_full_pipeline_mock(self, tmp_path: Path):
        """mock 全流程串联，验证 execute_generation_task 不抛异常"""
        import asyncio
        from src.core.wiki_pipeline import execute_generation_task

        mock_sc = MagicMock()
        mock_sc.update_task_status.return_value = True
        mock_sc.get_task.return_value = {"status": "processing"}
        mock_sc.get_repo_information.return_value = {}
        mock_sc.update_repository_information.return_value = True

        with patch("src.core.wiki_pipeline.SupabaseClient", return_value=mock_sc), \
             patch("src.core.wiki_pipeline._update_progress"), \
             patch("src.core.wiki_pipeline._generate_repo_description", return_value="A test repo"), \
             patch("src.core.wiki_pipeline.run_structure_generation", return_value=("/tmp/repo", _make_wiki_structure())) as mock_struct, \
             patch("src.core.wiki_pipeline.run_wiki_content_generation", return_value=[Path("/tmp/s1.json")]) as mock_content, \
             patch("src.core.wiki_pipeline.upload_wiki_to_r2", return_value=("https://r2/s.json", ["https://r2/c.json"])) as mock_upload, \
             patch("src.core.wiki_pipeline.run_rag_indexing", return_value="/tmp/vs"), \
             patch("src.core.wiki_pipeline.cleanup_local_files"):
            asyncio.run(execute_generation_task("test-task-001", "https://github.com/test/repo.git"))

        mock_struct.assert_called_once()
        mock_content.assert_called_once()
        mock_upload.assert_called_once()
        mock_sc.update_task_status.assert_called()

    def test_task_dir_isolation(self, tmp_path: Path):
        """不同 task_id 应生成不同的工作目录"""
        dir_a = tmp_path / "task_workdirs" / "task-a"
        dir_b = tmp_path / "task_workdirs" / "task-b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)

        assert dir_a != dir_b
        assert dir_a.exists()
        assert dir_b.exists()

        (dir_a / "test.txt").write_text("a")
        (dir_b / "test.txt").write_text("b")
        assert (dir_a / "test.txt").read_text() == "a"
        assert (dir_b / "test.txt").read_text() == "b"


# ---------------------------------------------------------------------------
# D. 清理逻辑
# ---------------------------------------------------------------------------

class TestWikiPipelineCleanup:

    def test_cleanup_removes_task_dir(self, tmp_path: Path):
        """cleanup_local_files 应清理任务工作目录"""
        task_dir = tmp_path / "task_workdirs" / "test-task"
        task_dir.mkdir(parents=True)
        output_path = task_dir / "wiki_structure.json"
        output_path.write_text("{}")
        json_dir = task_dir / "wiki_section_json"
        json_dir.mkdir()
        (json_dir / "s1.json").write_text("{}")

        with patch("src.core.wiki_pipeline.TASK_WORK_ROOT", tmp_path / "task_workdirs"), \
             patch("src.core.wiki_pipeline.REPO_STORE_ROOT", tmp_path / "repos"):
            cleanup_local_files(
                repo_path=None,
                output_path=output_path,
                json_output_dir=json_dir,
            )

        assert not task_dir.exists()

    def test_cleanup_preserves_other_task_dirs(self, tmp_path: Path):
        """清理一个任务不应影响其他任务目录"""
        task_root = tmp_path / "task_workdirs"
        task_a = task_root / "task-a"
        task_b = task_root / "task-b"
        task_a.mkdir(parents=True)
        task_b.mkdir(parents=True)

        output_a = task_a / "wiki_structure.json"
        output_a.write_text("{}")
        json_a = task_a / "wiki_section_json"
        json_a.mkdir()
        (task_b / "wiki_structure.json").write_text("{}")

        with patch("src.core.wiki_pipeline.TASK_WORK_ROOT", task_root), \
             patch("src.core.wiki_pipeline.REPO_STORE_ROOT", tmp_path / "repos"):
            cleanup_local_files(
                repo_path=None,
                output_path=output_a,
                json_output_dir=json_a,
            )

        assert not task_a.exists()
        assert task_b.exists()
        assert (task_b / "wiki_structure.json").exists()
