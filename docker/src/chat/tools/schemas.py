"""
Pydantic argument schemas for the chat agent's tools.

These are handed to `StructuredTool.from_function(args_schema=...)` and, via
`bind_tools()`, converted into native OpenRouter/OpenAI-style tool-call
schemas (`convert_to_openai_tool`) — the model sees the field names,
types, and descriptions below directly, instead of the hand-written JSON
examples the old `agent/prompts.py` embedded in prose.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class RagSearchArgs(BaseModel):
    query: str = Field(..., description="Focused search query — natural language question or symbol/topic name.")
    top_k: int = Field(20, description="Number of ranked results to return.")


class CodeGraphArgs(BaseModel):
    operation: Literal[
        "find_definition",
        "find_callers",
        "find_callees",
        "get_class_hierarchy",
        "get_file_symbols",
        "get_all_symbols",
        "find_imports",
        "get_module_dependencies",
    ] = Field(..., description="Which structural query to run against the repository's code graph.")
    symbol_name: Optional[str] = Field(None, description="Symbol or class name, required for most operations except get_all_symbols/get_file_symbols/find_imports.")
    file_path: Optional[str] = Field(None, description="Repo-relative file path, required for get_file_symbols/find_imports/get_module_dependencies.")


class FileReadArgs(BaseModel):
    file_path: str = Field(..., description="Repo-relative path of the file to read.")
    start_line: Optional[int] = Field(None, description="1-indexed first line to read (defaults to line 1).")
    end_line: Optional[int] = Field(None, description="1-indexed last line to read (defaults to start_line + max_lines - 1).")
    max_lines: int = Field(100, description="Maximum number of lines to read when end_line is not given.")


class RepoMapArgs(BaseModel):
    include_signatures: bool = Field(True, description="Include extracted class/function signatures in the overview.")
    max_depth: int = Field(3, description="Maximum directory depth to display in the tree.")


class GrepSearchArgs(BaseModel):
    pattern: str = Field(..., description="Text or regex pattern to search for across the live repository.")
    is_regex: bool = Field(False, description="Treat `pattern` as a regular expression instead of a literal string.")
    file_pattern: Optional[str] = Field(None, description="Optional glob to restrict which files are searched, e.g. '*.py'.")
    max_results: int = Field(50, description="Maximum number of matches to return.")
    case_sensitive: bool = Field(False, description="Whether the search is case-sensitive.")
    path_prefix: Optional[str] = Field(None, description="Optional subdirectory (relative to repo root) to restrict the search to.")
    context_lines: int = Field(2, description="Number of context lines to include before/after each match.")


class WebSearchArgs(BaseModel):
    query: str = Field(..., description="Web search query for external knowledge not contained in this repository.")
    search_type: Literal["general", "code_docs", "version", "cve"] = Field("general", description="Search intent, used to enhance the query.")
    max_results: int = Field(5, description="Maximum number of results to return.")
    domain_filter: Optional[str] = Field(None, description="Restrict results to a single domain, e.g. 'docs.python.org'.")
