"""
File-read and repo-map tools: precise line-range file reads sandboxed to the
repo root, and a high-level repo structure overview (tree + signatures).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import StructuredTool

from src.chat.tools.schemas import FileReadArgs, RepoMapArgs
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.tools.file")


class FileReadEngine:
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)

    def _resolve_safe_path(self, file_path: str) -> Tuple[Optional[Path], Optional[str]]:
        if not file_path or not str(file_path).strip():
            return None, "empty_path"
        root = self.repo_root.resolve()
        raw = Path(file_path)
        if raw.is_absolute():
            return None, "absolute_path_not_allowed"
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None, "path_escapes_repo_root"
        return candidate, None

    def read(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_lines: int = 100,
    ) -> Tuple[str, Dict[str, Any]]:
        full_path, path_err = self._resolve_safe_path(file_path)
        if path_err:
            return f"Invalid file path ({path_err}): {file_path}", {"error": path_err}

        if not full_path.exists():
            basename = Path(file_path).name
            alt_paths = list(self.repo_root.rglob(f"*{basename}")) if basename else []
            if alt_paths:
                full_path = alt_paths[0]
                file_path = str(full_path.relative_to(self.repo_root))
            else:
                return f"File not found: {file_path}", {"error": "file_not_found"}

        if not full_path.is_file():
            return f"Not a file: {file_path}", {"error": "not_a_file"}

        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total_lines = len(lines)

        if start_line is None:
            start_line = 1
        if end_line is None:
            end_line = min(start_line + max_lines - 1, total_lines)

        start_idx = max(0, start_line - 1)
        end_idx = min(total_lines, end_line)
        selected = lines[start_idx:end_idx]

        content_lines = [f"{i:4d} | {line.rstrip()}" for i, line in enumerate(selected, start=start_idx + 1)]
        header = f"File: {file_path} (lines {start_idx + 1}-{end_idx} of {total_lines})\n" + "=" * 60 + "\n"

        return header + "\n".join(content_lines), {
            "file_path": file_path,
            "line_range": (start_idx + 1, end_idx),
            "total_lines": total_lines,
        }


class RepoMapEngine:
    IGNORED_DIRS = {
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".cache", "coverage",
        ".idea", ".vscode", "target", "out", ".turbo",
        ".nuxt", ".output", "vendor",
    }
    IGNORED_EXTENSIONS = {
        ".pyc", ".pyo", ".so", ".o", ".a", ".dylib",
        ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg",
        ".woff", ".woff2", ".ttf", ".eot",
        ".lock", ".log", ".map", ".min.js", ".min.css",
    }
    ENTRYPOINT_PATTERNS = [
        "main.py", "app.py", "index.py", "__main__.py", "manage.py", "wsgi.py", "asgi.py",
        "index.ts", "index.tsx", "index.js", "index.jsx", "main.ts", "main.tsx",
        "app.ts", "app.tsx", "server.ts", "server.js",
        "page.tsx", "layout.tsx", "route.ts", "route.tsx",
        "api.py", "routes.py", "urls.py", "views.py",
    ]
    CONFIG_PATTERNS = [
        "package.json", "tsconfig.json", "next.config.js", "next.config.ts",
        "vite.config.ts", "vite.config.js", "webpack.config.js",
        "pyproject.toml", "setup.py", "requirements.txt",
        ".env.example", "docker-compose.yml", "Dockerfile",
    ]

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)
        self._cache: Dict[str, Tuple[str, Dict[str, Any]]] = {}

    def build(self, include_signatures: bool = True, max_depth: int = 3, max_files: int = 200) -> Tuple[str, Dict[str, Any]]:
        cache_key = f"sig={include_signatures}&depth={max_depth}&files={max_files}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        file_stats: Dict[str, int] = {}
        lines = ["Repository Structure:", "=" * 50]
        lines.extend(self._build_tree(self.repo_root, "", 0, max_depth, file_stats, max_files))

        lines.append("\n" + "=" * 50 + "\nFile Statistics:")
        for ext, count in sorted(file_stats.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"  {ext}: {count} files")

        entrypoints = self._detect_entrypoints()
        if entrypoints:
            lines.append("\n" + "=" * 50 + "\nDetected Entrypoints:")
            lines.extend(f"  {ep}" for ep in entrypoints[:10])

        configs = self._detect_configs()
        if configs:
            lines.append("\n" + "=" * 50 + "\nConfiguration Files:")
            lines.extend(f"  {cfg}" for cfg in configs[:10])

        if include_signatures:
            signatures = self._extract_key_signatures(40)
            if signatures:
                lines.append("\n" + "=" * 50 + "\nKey Symbols:")
                lines.extend(signatures)

        content = "\n".join(lines)
        artifact = {
            "file_stats": file_stats,
            "entrypoints": entrypoints[:5],
            "configs": configs[:5],
        }
        result = (content, artifact)
        self._cache[cache_key] = result
        return result

    def _build_tree(self, path: Path, prefix: str, depth: int, max_depth: int, file_stats: Dict[str, int], max_files: int) -> List[str]:
        if depth > max_depth:
            return ["    " * depth + "..."]
        try:
            entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return []

        dirs, files = [], []
        for entry in entries:
            if entry.name.startswith(".") and entry.name != ".env.example":
                continue
            if entry.is_dir() and entry.name in self.IGNORED_DIRS:
                continue
            if entry.is_file() and entry.suffix.lower() in self.IGNORED_EXTENSIONS:
                continue
            (dirs if entry.is_dir() else files).append(entry)

        lines: List[str] = []
        for d in dirs:
            lines.append(f"{prefix}{d.name}/")
            lines.extend(self._build_tree(d, prefix + "    ", depth + 1, max_depth, file_stats, max_files))

        for i, f in enumerate(files):
            ext = f.suffix.lower() or "(no ext)"
            file_stats[ext] = file_stats.get(ext, 0) + 1
            if i < 15:
                lines.append(f"{prefix}{f.name}")
            elif i == 15:
                lines.append(f"{prefix}    ... and {len(files) - 15} more files")
        return lines

    def _detect_entrypoints(self) -> List[str]:
        found = []
        for pattern in self.ENTRYPOINT_PATTERNS:
            for path in self.repo_root.rglob(pattern):
                if any(ignored in str(path) for ignored in self.IGNORED_DIRS):
                    continue
                found.append(str(path.relative_to(self.repo_root)))
        return sorted(set(found))

    def _detect_configs(self) -> List[str]:
        found = []
        for pattern in self.CONFIG_PATTERNS:
            for path in self.repo_root.rglob(pattern):
                if any(ignored in str(path) for ignored in self.IGNORED_DIRS):
                    continue
                found.append(str(path.relative_to(self.repo_root)))
        return sorted(set(found))

    def _extract_key_signatures(self, max_symbols: int) -> List[str]:
        signatures: List[str] = []
        for py_file in list(self.repo_root.rglob("*.py"))[:50]:
            if any(ignored in str(py_file) for ignored in self.IGNORED_DIRS):
                continue
            try:
                rel_path = py_file.relative_to(self.repo_root)
                for line in py_file.read_text(encoding="utf-8", errors="replace").split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("class ") and ":" in stripped:
                        signatures.append(f"  [py] [{rel_path}] {stripped.split(':')[0]}")
                    elif stripped.startswith("def ") and not stripped.startswith("def _") and ":" in stripped:
                        sig = stripped.split(":")[0]
                        if len(sig) < 80:
                            signatures.append(f"  [py] [{rel_path}] {sig}")
                    if len(signatures) >= max_symbols:
                        break
            except Exception:
                continue
            if len(signatures) >= max_symbols:
                break

        for pattern in ("*.ts", "*.tsx", "*.js", "*.jsx"):
            for ts_file in list(self.repo_root.rglob(pattern))[:30]:
                if any(ignored in str(ts_file) for ignored in self.IGNORED_DIRS) or ".min." in ts_file.name or ".d.ts" in ts_file.name:
                    continue
                try:
                    rel_path = ts_file.relative_to(self.repo_root)
                    lang = "ts" if ts_file.suffix in (".ts", ".tsx") else "js"
                    for line in ts_file.read_text(encoding="utf-8", errors="replace").split("\n"):
                        stripped = line.strip()
                        for prefix, kind in (("export function ", "function"), ("export class ", "class"), ("export interface ", "interface")):
                            if stripped.startswith(prefix):
                                m = re.match(rf"{prefix}(\w+)", stripped)
                                if m:
                                    signatures.append(f"  [{lang}] [{rel_path}] export {kind} {m.group(1)}")
                        if len(signatures) >= max_symbols:
                            break
                except Exception:
                    continue
                if len(signatures) >= max_symbols:
                    break
        return signatures[:max_symbols]


def build_file_read_tool(repo_root: str) -> StructuredTool:
    engine = FileReadEngine(repo_root)

    def _run(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None, max_lines: int = 100) -> Tuple[str, Dict[str, Any]]:
        try:
            return engine.read(file_path, start_line, end_line, max_lines)
        except Exception as e:
            logger.exception("File read failed")
            return f"Error reading file: {e}", {"error": str(e)}

    async def _arun(file_path: str, start_line: Optional[int] = None, end_line: Optional[int] = None, max_lines: int = 100) -> Tuple[str, Dict[str, Any]]:
        return await run_sync(_run, file_path, start_line, end_line, max_lines)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="file_read",
        description="Read a precise line range from a file in this repository. Use after identifying which file to read via another tool.",
        args_schema=FileReadArgs,
        response_format="content_and_artifact",
    )


def build_repo_map_tool(repo_root: str) -> StructuredTool:
    engine = RepoMapEngine(repo_root)

    def _run(include_signatures: bool = True, max_depth: int = 3) -> Tuple[str, Dict[str, Any]]:
        try:
            return engine.build(include_signatures=include_signatures, max_depth=max_depth)
        except Exception as e:
            logger.exception("Repo map generation failed")
            return f"Error generating repo map: {e}", {"error": str(e)}

    async def _arun(include_signatures: bool = True, max_depth: int = 3) -> Tuple[str, Dict[str, Any]]:
        return await run_sync(_run, include_signatures, max_depth)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="repo_map",
        description="Get a high-level overview of the repository's structure: directory tree, file stats, entrypoints, config files, and key signatures.",
        args_schema=RepoMapArgs,
        response_format="content_and_artifact",
    )
