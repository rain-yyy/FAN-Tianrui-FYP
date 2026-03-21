"""
文本/正则搜索工具

优先使用 ripgrep (rg) 做仓库级词法检索（尊重 .gitignore、高性能）；
不可用时回退到 Python 逐文件扫描。

返回结构化 metadata：matches、上下文行、truncated、engine、files_searched。
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
from typing import Any, Dict, List, Optional

from src.agent.state import ContextPiece

logger = logging.getLogger("app.agent.tools.grep")

# Python 回退：默认忽略的目录
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

MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
MAX_MATCHES_DEFAULT = 50
CONTEXT_LINES_DEFAULT = 2


def _find_rg_binary() -> Optional[str]:
    for name in ("rg", "ripgrep"):
        p = shutil.which(name)
        if p:
            return p
    return None


class GrepSearchTool:
    """
    跨仓库文本搜索工具（rg 优先，Python 回退）。
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()

    def execute(
        self,
        pattern: str,
        is_regex: bool = False,
        file_pattern: Optional[str] = None,
        max_results: int = MAX_MATCHES_DEFAULT,
        case_sensitive: bool = False,
        path_prefix: Optional[str] = None,
        context_lines: int = CONTEXT_LINES_DEFAULT,
    ) -> ContextPiece:
        if not pattern or not pattern.strip():
            return ContextPiece(
                source="grep_search",
                content="Empty search pattern provided.",
                relevance_score=0.0,
                metadata={"error": "empty_pattern"},
            )

        if is_regex:
            try:
                re.compile(pattern)
            except re.error as e:
                return ContextPiece(
                    source="grep_search",
                    content=f"Invalid regex pattern: {e}",
                    relevance_score=0.0,
                    metadata={"error": "invalid_regex"},
                )

        rg_path = _find_rg_binary()
        if rg_path:
            piece = self._execute_ripgrep(
                rg_path=rg_path,
                pattern=pattern.strip(),
                is_regex=is_regex,
                file_pattern=file_pattern,
                max_results=max_results,
                case_sensitive=case_sensitive,
                path_prefix=path_prefix,
                context_lines=context_lines,
            )
            err = piece.metadata.get("error")
            if err not in ("rg_failed", "rg_timeout"):
                return piece
            logger.warning("ripgrep error (%s), falling back to Python scanner", err)

        return self._execute_python_fallback(
            pattern=pattern.strip(),
            is_regex=is_regex,
            file_pattern=file_pattern,
            max_results=max_results,
            case_sensitive=case_sensitive,
            path_prefix=path_prefix,
            context_lines=context_lines,
        )

    def _execute_ripgrep(
        self,
        rg_path: str,
        pattern: str,
        is_regex: bool,
        file_pattern: Optional[str],
        max_results: int,
        case_sensitive: bool,
        path_prefix: Optional[str],
        context_lines: int,
    ) -> ContextPiece:
        search_root = (self.repo_root / path_prefix).resolve() if path_prefix else self.repo_root
        try:
            search_root.relative_to(self.repo_root)
        except ValueError:
            return ContextPiece(
                source="grep_search",
                content="path_prefix escapes repository root.",
                relevance_score=0.0,
                metadata={"error": "bad_path_prefix"},
            )

        cmd: List[str] = [
            rg_path,
            "--json",
            "-n",
            "--max-filesize",
            "2M",
            "--max-columns",
            "500",
            "--max-columns-preview",
        ]
        if context_lines > 0:
            cmd.extend(["-C", str(context_lines)])
        if not case_sensitive:
            cmd.append("-i")
        if file_pattern:
            cmd.extend(["--glob", file_pattern])
        if not is_regex:
            cmd.append("-F")
        cmd.extend(["-e", pattern])

        cmd.append(str(search_root))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                cwd=str(self.repo_root),
            )
        except subprocess.TimeoutExpired:
            return ContextPiece(
                source="grep_search",
                content="ripgrep search timed out.",
                relevance_score=0.0,
                metadata={"error": "rg_timeout", "engine": "ripgrep"},
            )
        except Exception as e:
            logger.exception("ripgrep subprocess error: %s", e)
            return ContextPiece(
                source="grep_search",
                content=f"ripgrep failed: {e}",
                relevance_score=0.0,
                metadata={"error": "rg_failed", "engine": "ripgrep"},
            )

        # ripgrep: 0 = matches, 1 = no matches, 2+ = error
        if proc.returncode not in (0, 1):
            err = (proc.stderr or "").strip()[:500]
            return ContextPiece(
                source="grep_search",
                content=f"ripgrep exited with {proc.returncode}: {err}",
                relevance_score=0.0,
                metadata={
                    "error": "rg_failed",
                    "returncode": proc.returncode,
                    "stderr": err,
                    "engine": "ripgrep",
                },
            )

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
            t = obj.get("type")
            if t == "begin":
                p = (obj.get("data") or {}).get("path") or {}
                text = p.get("text")
                if text:
                    files_seen.add(text)
            elif t == "match":
                data = obj.get("data") or {}
                path_obj = data.get("path") or {}
                rel_path = path_obj.get("text") or ""
                line_no = data.get("line_number")
                lines_obj = data.get("lines") or {}
                line_text = (lines_obj.get("text") or "").rstrip("\n")
                if not rel_path or line_no is None:
                    continue
                try:
                    pth = Path(rel_path)
                    if pth.is_absolute():
                        rel_norm = str(pth.resolve().relative_to(self.repo_root))
                    else:
                        rel_norm = str(pth.as_posix())
                except Exception:
                    rel_norm = rel_path
                entry: Dict[str, Any] = {
                    "file": rel_norm,
                    "line": int(line_no),
                    "text": line_text[:500],
                }
                sub = data.get("submatches") or []
                if sub and isinstance(sub, list) and sub[0].get("match"):
                    entry["match_span"] = sub[0].get("match")
                matches.append(entry)
                if len(matches) >= max_results:
                    truncated = True
                    break

        files_searched = len(files_seen)
        if not matches:
            return ContextPiece(
                source="grep_search",
                content=f"No matches found for pattern: {pattern}",
                relevance_score=0.0,
                metadata={
                    "pattern": pattern,
                    "is_regex": is_regex,
                    "files_searched": files_searched,
                    "engine": "ripgrep",
                },
            )

        self._attach_line_context(matches[: min(len(matches), 25)])

        content, sources = self._format_matches_output(matches, files_searched, pattern, truncated)
        relevance = min(0.72 + 0.005 * len(matches), 0.94)
        first = matches[0]
        line_no = int(first["line"])

        return ContextPiece(
            source="grep_search",
            content=content,
            file_path=first["file"],
            line_range=(line_no, line_no),
            relevance_score=relevance,
            metadata={
                "pattern": pattern,
                "is_regex": is_regex,
                "case_sensitive": case_sensitive,
                "path_prefix": path_prefix,
                "num_matches": len(matches),
                "files_searched": files_searched,
                "truncated": truncated,
                "sources": sources,
                "grep_matches": matches,
                "engine": "ripgrep",
                "used_python_fallback": False,
            },
        )

    def _attach_line_context(self, matches: List[Dict[str, Any]]) -> None:
        """为前若干条命中读取前后文（按文件分组读一次）。"""
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
                lo = max(0, idx - CONTEXT_LINES_DEFAULT)
                hi = min(len(lines), idx + CONTEXT_LINES_DEFAULT + 1)
                m["before_context"] = [lines[i] for i in range(lo, idx)]
                m["after_context"] = [lines[i] for i in range(idx + 1, hi)]

    def _format_matches_output(
        self,
        matches: List[Dict[str, Any]],
        files_searched: int,
        pattern: str,
        truncated: bool,
    ) -> tuple:
        result_lines = [
            f"Found {len(matches)} match(es) across {files_searched} file(s)"
            f"{' (truncated)' if truncated else ''}:\n",
        ]
        seen_files: set = set()
        for m in matches:
            rel = m["file"]
            if rel not in seen_files:
                result_lines.append(f"--- {rel} ---")
                seen_files.add(rel)
            for ln in m.get("before_context") or []:
                result_lines.append(f"  | {ln.rstrip()[:200]}")
            result_lines.append(f"  L{m['line']}: {m['text'][:200]}")
            for ln in m.get("after_context") or []:
                result_lines.append(f"  | {ln.rstrip()[:200]}")
        result_lines.append("")
        sources = sorted({m["file"] for m in matches})
        return "\n".join(result_lines), sources

    def _execute_python_fallback(
        self,
        pattern: str,
        is_regex: bool,
        file_pattern: Optional[str],
        max_results: int,
        case_sensitive: bool,
        path_prefix: Optional[str],
        context_lines: int,
    ) -> ContextPiece:
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern if is_regex else re.escape(pattern), flags)
        except re.error as e:
            return ContextPiece(
                source="grep_search",
                content=f"Invalid regex pattern: {e}",
                relevance_score=0.0,
                metadata={"error": "invalid_regex", "engine": "python"},
            )

        scan_root = (self.repo_root / path_prefix) if path_prefix else self.repo_root
        try:
            scan_root.resolve().relative_to(self.repo_root)
        except ValueError:
            return ContextPiece(
                source="grep_search",
                content="path_prefix escapes repository root.",
                relevance_score=0.0,
                metadata={"error": "bad_path_prefix", "engine": "python"},
            )

        matches: List[Dict[str, Any]] = []
        files_searched = 0
        truncated = False

        for file_path in self._iter_files(scan_root, file_pattern):
            files_searched += 1
            file_matches = self._search_file_lines(file_path, compiled, max_results - len(matches), context_lines)
            matches.extend(file_matches)
            if len(matches) >= max_results:
                matches = matches[:max_results]
                truncated = True
                break

        if not matches:
            return ContextPiece(
                source="grep_search",
                content=f"No matches found for pattern: {pattern}",
                relevance_score=0.0,
                metadata={
                    "pattern": pattern,
                    "files_searched": files_searched,
                    "engine": "python",
                    "used_python_fallback": True,
                },
            )

        content, sources = self._format_matches_output(matches, files_searched, pattern, truncated)
        relevance = min(0.7 + 0.01 * len(matches), 0.93)
        first = matches[0]
        line_no = int(first["line"])

        return ContextPiece(
            source="grep_search",
            content=content,
            file_path=first["file"],
            line_range=(line_no, line_no),
            relevance_score=relevance,
            metadata={
                "pattern": pattern,
                "is_regex": is_regex,
                "case_sensitive": case_sensitive,
                "path_prefix": path_prefix,
                "num_matches": len(matches),
                "files_searched": files_searched,
                "truncated": truncated,
                "sources": sources,
                "grep_matches": matches,
                "engine": "python",
                "used_python_fallback": True,
            },
        )

    def _iter_files(self, scan_root: Path, file_pattern: Optional[str]):
        for dirpath, dirnames, filenames in os.walk(scan_root, topdown=True):
            dirnames[:] = [
                d for d in dirnames
                if d not in _IGNORED_DIRS and not d.startswith(".")
            ]
            for fname in filenames:
                full = Path(dirpath) / fname
                if fname.startswith("."):
                    continue
                if full.suffix.lower() in _BINARY_EXTENSIONS:
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

    def _search_file_lines(
        self,
        file_path: Path,
        compiled: "re.Pattern[str]",
        room: int,
        context_lines: int,
    ) -> List[Dict[str, Any]]:
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
                lo = max(0, idx - context_lines)
                hi = min(len(lines), idx + context_lines + 1)
                results.append({
                    "file": rel,
                    "line": idx + 1,
                    "text": line.rstrip()[:500],
                    "before_context": [lines[i].rstrip("\n") for i in range(lo, idx)],
                    "after_context": [lines[i].rstrip("\n") for i in range(idx + 1, hi)],
                })
                if len(results) >= room:
                    break
        return results
