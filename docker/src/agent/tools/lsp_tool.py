"""
LSP 符号精确解析工具

使用静态分析库对仓库做精确符号定位：
- Python 仓库：优先使用 rope 库（无需启动 LSP server 进程）
- TypeScript/JavaScript 仓库：使用 tree-sitter 正则回退
- 其他语言：tree-sitter + 文件系统扫描 fallback

支持操作：
- find_definition: 找到符号的定义位置（文件 + 行号）
- find_references: 找到符号的所有引用位置
- hover: 获取符号的签名/类型摘要
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.agent.state import ContextPiece

logger = logging.getLogger("app.agent.tools.lsp")

_SUPPORTED_OPERATIONS = {"find_definition", "find_references", "hover"}

# 判断仓库主语言
_PYTHON_EXTENSIONS = {".py", ".pyi"}
_TS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _detect_repo_language(repo_root: Path) -> str:
    """快速检测仓库主语言（只扫顶层文件，不递归）"""
    py_count = ts_count = 0
    for entry in repo_root.iterdir():
        if entry.is_file():
            if entry.suffix in _PYTHON_EXTENSIONS:
                py_count += 1
            elif entry.suffix in _TS_EXTENSIONS:
                ts_count += 1
    if py_count >= ts_count:
        return "python"
    return "typescript"


def _try_import_rope():
    try:
        import rope.base.project  # type: ignore
        import rope.contrib.findit  # type: ignore
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Python static analysis via rope
# ---------------------------------------------------------------------------

def _rope_find_definition(
    repo_root: Path, symbol_name: str, file_hint: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """使用 rope 定位 Python 符号的定义位置"""
    try:
        import rope.base.project as rope_project
        import rope.contrib.findit as rope_findit

        project = rope_project.Project(str(repo_root))

        # 如果有 file_hint，先在该文件中尝试
        candidate_files: List[Path] = []
        if file_hint:
            hint_path = repo_root / file_hint
            if hint_path.exists():
                candidate_files.append(hint_path)

        # 扫描仓库找包含该符号名的 .py 文件（限速：最多扫 200 文件）
        scanned = 0
        for p in repo_root.rglob("*.py"):
            if scanned >= 200:
                break
            rel = str(p.relative_to(repo_root))
            skip_dirs = {".venv", "venv", "env", "node_modules", "__pycache__", ".git"}
            if any(part in skip_dirs for part in p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                if symbol_name in text:
                    candidate_files.append(p)
            except OSError:
                pass
            scanned += 1

        for py_file in candidate_files[:10]:
            try:
                resource = project.get_resource(str(py_file.relative_to(repo_root)))
                source = resource.read()
                # 找符号位置
                offset = source.find(f"def {symbol_name}")
                if offset == -1:
                    offset = source.find(f"class {symbol_name}")
                if offset == -1:
                    continue

                result = rope_findit.find_definition(project, resource, offset)
                if result:
                    def_resource = result.resource
                    line_no = result.lineno
                    return {
                        "definition_file": str(def_resource.path),
                        "definition_line": line_no,
                        "method": "rope",
                    }
            except Exception as inner_e:
                logger.debug("rope definition attempt failed for %s: %s", py_file, inner_e)
                continue

        project.close()
    except Exception as e:
        logger.debug("rope find_definition error: %s", e)
    return None


def _rope_find_references(
    repo_root: Path, symbol_name: str, file_hint: Optional[str] = None
) -> List[Dict[str, Any]]:
    """使用 rope 查找 Python 符号的所有引用"""
    refs: List[Dict[str, Any]] = []
    try:
        import rope.base.project as rope_project
        import rope.contrib.findit as rope_findit

        project = rope_project.Project(str(repo_root))

        # 找定义位置，再从定义位置出发调 find_occurrences
        def_info = _rope_find_definition(repo_root, symbol_name, file_hint)
        if def_info:
            def_file = repo_root / def_info["definition_file"]
            try:
                resource = project.get_resource(str(def_file.relative_to(repo_root)))
                source = resource.read()
                offset = source.find(f"def {symbol_name}")
                if offset == -1:
                    offset = source.find(f"class {symbol_name}")
                if offset >= 0:
                    occurrences = rope_findit.find_occurrences(project, resource, offset)
                    for occ in occurrences[:30]:
                        refs.append({
                            "file": str(occ.resource.path),
                            "line": occ.lineno,
                            "is_definition": occ.is_defined,
                        })
            except Exception as e:
                logger.debug("rope occurrences error: %s", e)

        project.close()
    except Exception as e:
        logger.debug("rope find_references error: %s", e)
    return refs


# ---------------------------------------------------------------------------
# Generic regex-based fallback (all languages)
# ---------------------------------------------------------------------------

def _regex_find_definition(
    repo_root: Path, symbol_name: str, file_hint: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    正则扫描查找符号定义（支持 Python/TS/JS/Go/Rust 等）。
    匹配常见模式：def X, class X, function X, const X =, export function X, func X
    """
    patterns = [
        rf"(?:def|class)\s+{re.escape(symbol_name)}\b",           # Python
        rf"(?:function|class)\s+{re.escape(symbol_name)}\b",       # JS/TS
        rf"(?:const|let|var)\s+{re.escape(symbol_name)}\s*(?:=|:)", # JS/TS const
        rf"export\s+(?:function|class|const)\s+{re.escape(symbol_name)}\b",  # TS export
        rf"func\s+{re.escape(symbol_name)}\b",                     # Go
        rf"fn\s+{re.escape(symbol_name)}\b",                       # Rust
        rf"(?:public|private|protected|static).*\s{re.escape(symbol_name)}\s*\(", # Java/C#
    ]
    combined = re.compile("|".join(patterns), re.MULTILINE)

    candidate_files: List[Path] = []
    if file_hint:
        hint_path = repo_root / file_hint
        if hint_path.exists():
            candidate_files.append(hint_path)

    scanned = 0
    skip_dirs = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", "dist", "build"}
    skip_exts = {".pyc", ".pyo", ".so", ".dylib", ".lock", ".min.js"}

    for p in repo_root.rglob("*"):
        if scanned >= 500:
            break
        if not p.is_file():
            continue
        if p.suffix in skip_exts:
            continue
        if any(part in skip_dirs for part in p.parts):
            continue
        if p.stat().st_size > 500_000:
            continue
        if symbol_name in p.read_text(encoding="utf-8", errors="ignore"):
            candidate_files.append(p)
        scanned += 1

    for src_file in candidate_files[:15]:
        try:
            text = src_file.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if combined.search(line):
                    return {
                        "definition_file": str(src_file.relative_to(repo_root)),
                        "definition_line": i,
                        "method": "regex",
                        "matched_line": line.strip()[:120],
                    }
        except OSError:
            continue
    return None


def _regex_find_references(
    repo_root: Path, symbol_name: str, max_refs: int = 30
) -> List[Dict[str, Any]]:
    """正则扫描查找符号引用"""
    refs: List[Dict[str, Any]] = []
    pattern = re.compile(rf"\b{re.escape(symbol_name)}\b")
    skip_dirs = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", "dist", "build"}
    skip_exts = {".pyc", ".pyo", ".so", ".dylib", ".lock"}

    for p in repo_root.rglob("*"):
        if len(refs) >= max_refs:
            break
        if not p.is_file():
            continue
        if p.suffix in skip_exts:
            continue
        if any(part in skip_dirs for part in p.parts):
            continue
        if p.stat().st_size > 300_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            if symbol_name not in text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    refs.append({
                        "file": str(p.relative_to(repo_root)),
                        "line": i,
                        "snippet": line.strip()[:100],
                    })
                    if len(refs) >= max_refs:
                        break
        except OSError:
            continue
    return refs


# ---------------------------------------------------------------------------
# Main tool class
# ---------------------------------------------------------------------------

class LSPResolveTool:
    """
    LSP 符号精确解析工具。

    不启动 LSP server 进程，使用：
    - rope（Python 仓库的静态分析）
    - 正则扫描 fallback（所有其他语言）
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root).resolve()
        self._lang = _detect_repo_language(self.repo_root)
        self._has_rope = _try_import_rope()
        logger.info(
            "[LSPResolveTool] repo_root=%s lang=%s rope=%s",
            self.repo_root, self._lang, self._has_rope,
        )

    def execute(
        self,
        symbol_name: str,
        operation: str = "find_definition",
        file_hint: Optional[str] = None,
    ) -> ContextPiece:
        """
        执行 LSP 解析操作。

        Args:
            symbol_name: 要查找的符号名（函数名、类名等）
            operation: "find_definition" | "find_references" | "hover"
            file_hint: 可选的起始文件路径（加速搜索）

        Returns:
            ContextPiece，包含解析结果和定位元数据
        """
        if not symbol_name or not symbol_name.strip():
            return ContextPiece(
                source="lsp_resolve",
                content="Empty symbol name provided.",
                relevance_score=0.0,
                metadata={"error": "empty_symbol"},
            )

        operation = operation.strip().lower()
        if operation not in _SUPPORTED_OPERATIONS:
            operation = "find_definition"

        symbol_name = symbol_name.strip()

        if operation == "find_definition":
            return self._find_definition(symbol_name, file_hint)
        elif operation == "find_references":
            return self._find_references(symbol_name, file_hint)
        else:  # hover
            return self._hover(symbol_name, file_hint)

    def _find_definition(self, symbol_name: str, file_hint: Optional[str]) -> ContextPiece:
        def_info: Optional[Dict[str, Any]] = None

        # Python repos: try rope first
        if self._lang == "python" and self._has_rope:
            def_info = _rope_find_definition(self.repo_root, symbol_name, file_hint)

        # Fallback: regex scan
        if not def_info:
            def_info = _regex_find_definition(self.repo_root, symbol_name, file_hint)

        if def_info:
            rel_file = def_info["definition_file"]
            line_no = def_info["definition_line"]
            method = def_info.get("method", "regex")
            matched = def_info.get("matched_line", "")

            content_lines = [
                f"**Symbol**: `{symbol_name}`",
                f"**Definition found at**: `{rel_file}` line {line_no}",
            ]
            if matched:
                content_lines.append(f"**Definition line**: `{matched}`")

            content = "\n".join(content_lines)
            return ContextPiece(
                source="lsp_resolve",
                content=content,
                file_path=rel_file,
                line_range=(line_no, line_no),
                relevance_score=0.95 if method == "rope" else 0.88,
                metadata={
                    "operation": "find_definition",
                    "symbol_name": symbol_name,
                    "definition_file": rel_file,
                    "definition_line": line_no,
                    "method": method,
                },
            )
        else:
            return ContextPiece(
                source="lsp_resolve",
                content=f"Could not locate definition of `{symbol_name}` in repository.",
                relevance_score=0.0,
                metadata={
                    "operation": "find_definition",
                    "symbol_name": symbol_name,
                    "method": "not_found",
                },
            )

    def _find_references(self, symbol_name: str, file_hint: Optional[str]) -> ContextPiece:
        refs: List[Dict[str, Any]] = []

        if self._lang == "python" and self._has_rope:
            refs = _rope_find_references(self.repo_root, symbol_name, file_hint)

        if not refs:
            refs = _regex_find_references(self.repo_root, symbol_name)

        if refs:
            # Group by file
            by_file: Dict[str, List[Dict]] = {}
            for r in refs:
                f = r["file"]
                by_file.setdefault(f, []).append(r)

            lines = [f"**Symbol**: `{symbol_name}` — {len(refs)} reference(s) across {len(by_file)} file(s)\n"]
            for f, file_refs in list(by_file.items())[:10]:
                line_nums = ", ".join(str(r["line"]) for r in file_refs[:5])
                lines.append(f"- `{f}` lines [{line_nums}]")

            content = "\n".join(lines)
            return ContextPiece(
                source="lsp_resolve",
                content=content,
                relevance_score=0.85,
                metadata={
                    "operation": "find_references",
                    "symbol_name": symbol_name,
                    "references_count": len(refs),
                    "files_count": len(by_file),
                    "references": refs[:20],
                    "method": "rope" if (self._lang == "python" and self._has_rope) else "regex",
                },
            )
        else:
            return ContextPiece(
                source="lsp_resolve",
                content=f"No references found for `{symbol_name}`.",
                relevance_score=0.0,
                metadata={
                    "operation": "find_references",
                    "symbol_name": symbol_name,
                    "method": "not_found",
                },
            )

    def _hover(self, symbol_name: str, file_hint: Optional[str]) -> ContextPiece:
        """Hover: 返回符号定义行及上下文（约 5 行）"""
        def_info = None

        if self._lang == "python" and self._has_rope:
            def_info = _rope_find_definition(self.repo_root, symbol_name, file_hint)

        if not def_info:
            def_info = _regex_find_definition(self.repo_root, symbol_name, file_hint)

        if not def_info:
            return ContextPiece(
                source="lsp_resolve",
                content=f"Could not find hover info for `{symbol_name}`.",
                relevance_score=0.0,
                metadata={"operation": "hover", "symbol_name": symbol_name, "method": "not_found"},
            )

        rel_file = def_info["definition_file"]
        line_no = int(def_info["definition_line"])
        method = def_info.get("method", "regex")

        # Read surrounding lines for context
        full_path = self.repo_root / rel_file
        snippet_lines: List[str] = []
        try:
            all_lines = full_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            start = max(0, line_no - 1)
            end = min(len(all_lines), start + 10)
            snippet_lines = all_lines[start:end]
        except OSError:
            pass

        snippet = "\n".join(snippet_lines) if snippet_lines else def_info.get("matched_line", "")
        content = (
            f"**Symbol**: `{symbol_name}` at `{rel_file}:{line_no}`\n\n"
            f"```\n{snippet}\n```"
        )
        return ContextPiece(
            source="lsp_resolve",
            content=content,
            file_path=rel_file,
            line_range=(line_no, line_no + len(snippet_lines)),
            relevance_score=0.90 if method == "rope" else 0.82,
            metadata={
                "operation": "hover",
                "symbol_name": symbol_name,
                "definition_file": rel_file,
                "definition_line": line_no,
                "method": method,
            },
        )
