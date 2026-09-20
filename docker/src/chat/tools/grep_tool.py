"""
Text/regex search tool: prefers ripgrep (respects .gitignore, fast) and falls
back to a pure-Python file scanner when `rg` isn't on PATH.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool

from src.chat.tools.schemas import GrepSearchArgs
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.tools.grep")

_IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".cache", "coverage",
    ".idea", ".vscode", "target", "out", ".turbo",
    ".nuxt", ".output", "vendor",
}
_BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".lock", ".map", ".min.js", ".min.css",
    ".faiss", ".pkl", ".npy", ".npz", ".bin",
    ".zip", ".tar", ".gz", ".bz2",
}
MAX_FILE_SIZE = 2 * 1024 * 1024
CONTEXT_LINES_DEFAULT = 2


def _find_rg_binary() -> Optional[str]:
    for name in ("rg", "ripgrep"):
        p = shutil.which(name)
        if p:
            return p
    return None


class GrepSearchEngine:
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()

    def search(
        self,
        pattern: str,
        is_regex: bool = False,
        file_pattern: Optional[str] = None,
        max_results: int = 50,
        case_sensitive: bool = False,
        path_prefix: Optional[str] = None,
        context_lines: int = CONTEXT_LINES_DEFAULT,
    ) -> Tuple[str, Dict[str, Any]]:
        if not pattern or not pattern.strip():
            return "Empty search pattern provided.", {"error": "empty_pattern"}

        if is_regex:
            try:
                re.compile(pattern)
            except re.error as e:
                return f"Invalid regex pattern: {e}", {"error": "invalid_regex"}

        rg_path = _find_rg_binary()
        if rg_path:
            content, artifact = self._search_ripgrep(rg_path, pattern.strip(), is_regex, file_pattern, max_results, case_sensitive, path_prefix, context_lines)
            if artifact.get("error") not in ("rg_failed", "rg_timeout"):
                return content, artifact
            logger.warning("ripgrep error (%s), falling back to Python scanner", artifact.get("error"))

        return self._search_python(pattern.strip(), is_regex, file_pattern, max_results, case_sensitive, path_prefix, context_lines)

    def _search_ripgrep(
        self, rg_path: str, pattern: str, is_regex: bool, file_pattern: Optional[str],
        max_results: int, case_sensitive: bool, path_prefix: Optional[str], context_lines: int,
    ) -> Tuple[str, Dict[str, Any]]:
        search_root = (self.repo_root / path_prefix).resolve() if path_prefix else self.repo_root
        try:
            search_root.relative_to(self.repo_root)
        except ValueError:
            return "path_prefix escapes repository root.", {"error": "bad_path_prefix"}

        # No -C here: ripgrep's own context lines would arrive as separate
        # "context"-type JSON events this parser doesn't consume (it only
        # reads "begin"/"match"). Context is computed once, below, via
        # _attach_line_context instead — asking ripgrep for it too would
        # just be discarded work.
        cmd = [rg_path, "--json", "-n", "--max-filesize", "2M", "--max-columns", "500", "--max-columns-preview"]
        if not case_sensitive:
            cmd.append("-i")
        if file_pattern:
            cmd.extend(["--glob", file_pattern])
        if not is_regex:
            cmd.append("-F")
        cmd.extend(["-e", pattern, str(search_root)])

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(self.repo_root))
        except subprocess.TimeoutExpired:
            return "ripgrep search timed out.", {"error": "rg_timeout", "engine": "ripgrep"}
        except Exception as e:
            logger.exception("ripgrep subprocess error")
            return f"ripgrep failed: {e}", {"error": "rg_failed", "engine": "ripgrep"}

        if proc.returncode not in (0, 1):
            err = (proc.stderr or "").strip()[:500]
            return f"ripgrep exited with {proc.returncode}: {err}", {"error": "rg_failed", "engine": "ripgrep"}

        matches: List[Dict[str, Any]] = []
        files_seen: set = set()
        truncated = False
        for raw in (proc.stdout or "").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "begin":
                text = ((obj.get("data") or {}).get("path") or {}).get("text")
                if text:
                    files_seen.add(text)
            elif obj.get("type") == "match":
                data = obj.get("data") or {}
                rel_path = ((data.get("path") or {}).get("text")) or ""
                line_no = data.get("line_number")
                line_text = ((data.get("lines") or {}).get("text") or "").rstrip("\n")
                if not rel_path or line_no is None:
                    continue
                try:
                    pth = Path(rel_path)
                    rel_norm = str(pth.resolve().relative_to(self.repo_root)) if pth.is_absolute() else pth.as_posix()
                except Exception:
                    rel_norm = rel_path
                matches.append({"file": rel_norm, "line": int(line_no), "text": line_text[:500]})
                if len(matches) >= max_results:
                    truncated = True
                    break

        if not matches:
            return f"No matches found for pattern: {pattern}", {"pattern": pattern, "files_searched": len(files_seen), "engine": "ripgrep"}

        self._attach_line_context(matches[: min(len(matches), 25)], context_lines)
        content, sources = self._format_matches(matches, len(files_seen), truncated)
        return content, {
            "pattern": pattern, "num_matches": len(matches), "files_searched": len(files_seen),
            "truncated": truncated, "sources": sources, "engine": "ripgrep",
        }

    def _attach_line_context(self, matches: List[Dict[str, Any]], context_lines: int) -> None:
        by_file: Dict[str, List[Dict[str, Any]]] = {}
        for m in matches:
            by_file.setdefault(m["file"], []).append(m)
        for rel, items in by_file.items():
            full = self.repo_root / rel
            if not full.is_file():
                continue
            try:
                lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for m in items:
                idx = int(m["line"]) - 1
                if idx < 0 or idx >= len(lines):
                    continue
                lo, hi = max(0, idx - context_lines), min(len(lines), idx + context_lines + 1)
                m["before_context"] = [lines[i] for i in range(lo, idx)]
                m["after_context"] = [lines[i] for i in range(idx + 1, hi)]

    def _format_matches(self, matches: List[Dict[str, Any]], files_searched: int, truncated: bool) -> Tuple[str, List[str]]:
        lines = [f"Found {len(matches)} match(es) across {files_searched} file(s){' (truncated)' if truncated else ''}:\n"]
        seen_files: set = set()
        for m in matches:
            if m["file"] not in seen_files:
                lines.append(f"--- {m['file']} ---")
                seen_files.add(m["file"])
            for ln in m.get("before_context") or []:
                lines.append(f"  | {ln.rstrip()[:200]}")
            lines.append(f"  L{m['line']}: {m['text'][:200]}")
            for ln in m.get("after_context") or []:
                lines.append(f"  | {ln.rstrip()[:200]}")
        return "\n".join(lines), sorted({m["file"] for m in matches})

    def _search_python(
        self, pattern: str, is_regex: bool, file_pattern: Optional[str], max_results: int,
        case_sensitive: bool, path_prefix: Optional[str], context_lines: int,
    ) -> Tuple[str, Dict[str, Any]]:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern if is_regex else re.escape(pattern), flags)
        except re.error as e:
            return f"Invalid regex pattern: {e}", {"error": "invalid_regex", "engine": "python"}

        scan_root = (self.repo_root / path_prefix) if path_prefix else self.repo_root
        try:
            scan_root.resolve().relative_to(self.repo_root)
        except ValueError:
            return "path_prefix escapes repository root.", {"error": "bad_path_prefix", "engine": "python"}

        matches: List[Dict[str, Any]] = []
        files_searched = 0
        truncated = False
        for file_path in self._iter_files(scan_root, file_pattern):
            files_searched += 1
            matches.extend(self._search_file(file_path, compiled, max_results - len(matches), context_lines))
            if len(matches) >= max_results:
                matches = matches[:max_results]
                truncated = True
                break

        if not matches:
            return f"No matches found for pattern: {pattern}", {"pattern": pattern, "files_searched": files_searched, "engine": "python"}

        content, sources = self._format_matches(matches, files_searched, truncated)
        return content, {
            "pattern": pattern, "num_matches": len(matches), "files_searched": files_searched,
            "truncated": truncated, "sources": sources, "engine": "python",
        }

    def _iter_files(self, scan_root: Path, file_pattern: Optional[str]):
        for dirpath, dirnames, filenames in os.walk(scan_root, topdown=True):
            dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
            for fname in filenames:
                full = Path(dirpath) / fname
                if fname.startswith(".") or full.suffix.lower() in _BINARY_EXTENSIONS:
                    continue
                try:
                    if full.stat().st_size > MAX_FILE_SIZE:
                        continue
                except OSError:
                    continue
                try:
                    rel = str(full.relative_to(self.repo_root))
                except ValueError:
                    continue
                if file_pattern and not fnmatch.fnmatch(rel, file_pattern) and not fnmatch.fnmatch(fname, file_pattern):
                    continue
                yield full

    def _search_file(self, file_path: Path, compiled: "re.Pattern[str]", room: int, context_lines: int) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if room <= 0:
            return results
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return results
        rel = str(file_path.relative_to(self.repo_root))
        for idx, line in enumerate(lines):
            if compiled.search(line):
                lo, hi = max(0, idx - context_lines), min(len(lines), idx + context_lines + 1)
                results.append({
                    "file": rel, "line": idx + 1, "text": line.rstrip()[:500],
                    "before_context": [lines[i].rstrip("\n") for i in range(lo, idx)],
                    "after_context": [lines[i].rstrip("\n") for i in range(idx + 1, hi)],
                })
                if len(results) >= room:
                    break
        return results


def build_grep_search_tool(repo_root: str) -> StructuredTool:
    engine = GrepSearchEngine(repo_root)

    def _run(
        pattern: str, is_regex: bool = False, file_pattern: Optional[str] = None,
        max_results: int = 50, case_sensitive: bool = False, path_prefix: Optional[str] = None,
        context_lines: int = 2,
    ) -> Tuple[str, Dict[str, Any]]:
        try:
            return engine.search(pattern, is_regex, file_pattern, max_results, case_sensitive, path_prefix, context_lines)
        except Exception as e:
            logger.exception("grep_search failed")
            return f"Search failed: {e}", {"error": str(e)}

    async def _arun(
        pattern: str, is_regex: bool = False, file_pattern: Optional[str] = None,
        max_results: int = 50, case_sensitive: bool = False, path_prefix: Optional[str] = None,
        context_lines: int = 2,
    ) -> Tuple[str, Dict[str, Any]]:
        return await run_sync(_run, pattern, is_regex, file_pattern, max_results, case_sensitive, path_prefix, context_lines)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="grep_search",
        description="Search the live repository's files for exact text or a regex pattern (ripgrep-backed). Good for exact strings, config keys, error messages, TODOs.",
        args_schema=GrepSearchArgs,
        response_format="content_and_artifact",
    )
