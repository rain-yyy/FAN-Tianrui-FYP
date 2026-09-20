"""
Tests for the chat agent's `StructuredTool` wrappers in `src/chat/tools/`.

file_read/grep_search/repo_map/code_graph run against a synthetic temp repo
and graph, no external services needed. rag_search needs a real Qdrant
index and is covered by the `slow` end-to-end tests instead.
"""
import json

import networkx as nx
import pytest

from src.chat.tools.file_tool import build_file_read_tool, build_repo_map_tool
from src.chat.tools.graph_tool import build_code_graph_tool
from src.chat.tools.grep_tool import build_grep_search_tool
from src.chat.tools import build_tools_for_session


@pytest.fixture
def sample_repo(tmp_path):
    (tmp_path / "app.py").write_text(
        "def add(a, b):\n"
        '    """Add two numbers together."""\n'
        "    return a + b\n"
    )
    return tmp_path


@pytest.fixture
def sample_graph_path(tmp_path):
    graph = nx.DiGraph()
    graph.add_node("app.py:add", type="function", name="add", qualified_name="add", file="app.py", start_line=1, end_line=3)
    graph.add_node("app.py:caller", type="function", name="caller", qualified_name="caller", file="app.py", start_line=5, end_line=6)
    graph.add_edge("app.py:caller", "app.py:add", type="calls")

    path = tmp_path / "code_graph.json"
    path.write_text(json.dumps(nx.node_link_data(graph)))
    return str(path)


def test_file_read_tool_returns_content_and_artifact(sample_repo):
    tool = build_file_read_tool(str(sample_repo))
    content, artifact = tool.func(file_path="app.py")
    assert "def add(a, b):" in content
    assert artifact["file_path"] == "app.py"
    assert artifact["line_range"][0] == 1


def test_file_read_tool_rejects_path_escape(sample_repo):
    tool = build_file_read_tool(str(sample_repo))
    content, artifact = tool.func(file_path="../outside.py")
    assert artifact["error"] == "path_escapes_repo_root"


def test_grep_search_tool_finds_match(sample_repo):
    tool = build_grep_search_tool(str(sample_repo))
    content, artifact = tool.func(pattern="def add")
    assert artifact["num_matches"] >= 1
    assert "app.py" in artifact["sources"]


def test_repo_map_tool_lists_files(sample_repo):
    tool = build_repo_map_tool(str(sample_repo))
    content, artifact = tool.func()
    assert "app.py" in content


def test_code_graph_tool_find_definition(sample_graph_path):
    tool = build_code_graph_tool(sample_graph_path)
    content, artifact = tool.func(operation="find_definition", symbol_name="add")
    assert "add" in artifact["symbols_found"]


def test_code_graph_tool_find_callers(sample_graph_path):
    tool = build_code_graph_tool(sample_graph_path)
    content, artifact = tool.func(operation="find_callers", symbol_name="add")
    assert artifact["symbols_found"] == ["caller"]


def test_build_tools_for_session_includes_repo_and_graph_tools(sample_repo, sample_graph_path):
    tools = build_tools_for_session(
        vector_store_path=str(sample_repo / "vs"),
        graph_path=sample_graph_path,
        repo_root=str(sample_repo),
        web_search_config={"enabled": False},
    )
    names = {t.name for t in tools}
    assert names == {"rag_search", "code_graph", "file_read", "repo_map", "grep_search"}


def test_build_tools_for_session_minimal_without_repo_or_graph():
    tools = build_tools_for_session(
        vector_store_path="/nonexistent",
        graph_path=None,
        repo_root=None,
        web_search_config={"enabled": False},
    )
    assert [t.name for t in tools] == ["rag_search"]
