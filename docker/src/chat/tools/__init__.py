"""
Per-session tool registry assembly for the chat agent.

Adding a new tool means adding one `build_x_tool()` factory in this package
and one line in `build_tools_for_session` — not touching a dispatch table,
an anchor-extraction pass, and an intent-routing table as the old
`docker/src/agent/graph.py` required.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool

from src.chat.tools.file_tool import build_file_read_tool, build_repo_map_tool
from src.chat.tools.graph_tool import build_code_graph_tool
from src.chat.tools.grep_tool import build_grep_search_tool
from src.chat.tools.rag_tool import build_rag_search_tool
from src.chat.tools.web_tool import build_web_search_tool

__all__ = ["build_tools_for_session"]


def build_tools_for_session(
    vector_store_path: str,
    graph_path: Optional[str],
    repo_root: Optional[str],
    web_search_config: Optional[Dict[str, Any]] = None,
) -> List[BaseTool]:
    """
    Build the tool set available to the agent for one chat turn.

    rag_search is always available (it's the only tool that doesn't need a
    checked-out repo, just the vector index); code_graph/file_read/repo_map/
    grep_search require the physical repo checkout (`repo_root`)/code graph
    (`graph_path`) to exist; web_search is opt-in via config.
    """
    tools: List[BaseTool] = [build_rag_search_tool(vector_store_path)]

    if graph_path:
        tools.append(build_code_graph_tool(graph_path))

    if repo_root:
        tools.append(build_file_read_tool(repo_root))
        tools.append(build_repo_map_tool(repo_root))
        tools.append(build_grep_search_tool(repo_root))

    web_search_config = web_search_config or {}
    if web_search_config.get("enabled", True):
        tools.append(build_web_search_tool(web_search_config))

    return tools
