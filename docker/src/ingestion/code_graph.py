import re
import networkx as nx
from tree_sitter import Parser, Node
from typing import Dict, List, Optional, Set
from pathlib import Path
import json

from src.ingestion.ts_parser import TreeSitterParser

# Limit cross-file call fan-out to this many *distinct files* when the callee
# is ambiguous (no import edge to guide resolution).  Same-file and imported-
# file targets are always added regardless of this cap.
_MAX_AMBIGUOUS_CROSS_FILE_TARGETS = 2

# JS/TS language names as returned by EXTENSION_TO_LANGUAGE
_JS_LANGS: Set[str] = {"javascript", "typescript"}


class CodeGraphBuilder:
    """
    使用 Tree-sitter 和 NetworkX 构建代码知识图谱。

    图中节点类型：
      - file     : 源文件（节点 ID = 相对路径）
      - class    : 类定义（节点 ID = "相对路径:类名"）
      - function : 函数定义（节点 ID = "相对路径:函数名"）

    图中边类型：
      - contains : 文件 → 其内部类/函数
      - imports  : 文件 → 被该文件直接导入的文件（相对导入精确解析）
      - calls    : 调用方上下文 → 被调用函数节点（优先级：同文件 > 已导入文件 > 同目录有限个）
    """

    EXTENSION_TO_LANGUAGE = TreeSitterParser.EXTENSION_TO_LANGUAGE

    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        self.ts_parser = TreeSitterParser()
        self._all_rel_paths: Set[str] = set()
        self.current_context: str = ""

    def get_parser(self, language_name: str) -> Parser:
        return self.ts_parser.get_parser(language_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_graph(self, repo_root: str, file_paths: List[str]):
        """构建完整的代码库图谱。两阶段：先提取节点，再提取边。"""
        repo_root_path = Path(repo_root)

        # Pre-compute the set of all relative paths so import resolution can
        # do O(1) membership checks.
        self._all_rel_paths = set()
        for fp in file_paths:
            try:
                self._all_rel_paths.add(
                    str(Path(fp).relative_to(repo_root_path)).replace("\\", "/")
                )
            except ValueError:
                pass

        # Phase 1: nodes
        for file_path in file_paths:
            rel_path = str(Path(file_path).relative_to(repo_root_path)).replace("\\", "/")
            lang_name = self.EXTENSION_TO_LANGUAGE.get(Path(file_path).suffix.lower())
            if not lang_name:
                continue
            content = self._read_file(file_path)
            if content is None:
                continue
            self._extract_nodes(content, rel_path, lang_name)

        # Phase 2: edges (import first, then call — call resolution uses import edges)
        for file_path in file_paths:
            rel_path = str(Path(file_path).relative_to(repo_root_path)).replace("\\", "/")
            lang_name = self.EXTENSION_TO_LANGUAGE.get(Path(file_path).suffix.lower())
            if not lang_name:
                continue
            content = self._read_file(file_path)
            if content is None:
                continue
            self._extract_edges(content, rel_path, lang_name)

        return self.graph

    def save_graph(self, output_path: str) -> None:
        data = nx.node_link_data(self.graph)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_graph(self, input_path: str) -> None:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.graph = nx.node_link_graph(data)

    # ------------------------------------------------------------------
    # Node extraction
    # ------------------------------------------------------------------

    def _extract_nodes(self, code: str, rel_path: str, lang_name: str) -> None:
        """添加文件、类、函数节点及 contains 边。"""
        self.graph.add_node(rel_path, type="file", label=rel_path)

        parser = self.get_parser(lang_name)
        tree = parser.parse(bytes(code, "utf8"))

        def traverse(node: Node) -> None:
            if node.type in (
                "function_definition",
                "class_definition",
                "function_declaration",
                "class_declaration",
            ):
                name: Optional[str] = None
                for child in node.children:
                    if child.type == "identifier":
                        name = code[child.start_byte : child.end_byte]
                        break
                if name:
                    node_id = f"{rel_path}:{name}"
                    self.graph.add_node(
                        node_id,
                        type="class" if "class" in node.type else "function",
                        name=name,
                        file=rel_path,
                        label=name,
                    )
                    self.graph.add_edge(rel_path, node_id, type="contains")

            for child in node.children:
                traverse(child)

        traverse(tree.root_node)

    # ------------------------------------------------------------------
    # Import resolution
    # ------------------------------------------------------------------

    def _resolve_python_module(
        self, module: str, base_dir: str, dot_count: int
    ) -> Optional[str]:
        """将 Python 模块名解析为仓库内相对路径（找不到则返回 None）。"""
        mod_path = module.replace(".", "/")
        if dot_count > 0:
            parent = Path(base_dir) if base_dir else Path(".")
            for _ in range(dot_count - 1):
                parent = parent.parent
            candidate = str(parent / mod_path).replace("\\", "/")
        else:
            candidate = mod_path

        if f"{candidate}.py" in self._all_rel_paths:
            return f"{candidate}.py"
        init = f"{candidate}/__init__.py"
        if init in self._all_rel_paths:
            return init
        return None

    def _resolve_js_module(self, mod: str, base_dir: str) -> Optional[str]:
        """将相对 JS/TS import 路径解析为仓库内相对路径（绝对/node_modules 跳过）。"""
        if not mod.startswith("."):
            return None  # skip node_modules / bare specifiers
        base = Path(base_dir) if base_dir else Path(".")
        candidate = str(base / mod).replace("\\", "/")

        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
            if f"{candidate}{ext}" in self._all_rel_paths:
                return f"{candidate}{ext}"
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            idx = f"{candidate}/index{ext}"
            if idx in self._all_rel_paths:
                return idx
        if candidate in self._all_rel_paths:
            return candidate
        return None

    def _extract_import_edges(self, code: str, rel_path: str, lang_name: str) -> None:
        """
        提取文件级 import/require 语句，添加 imports 边。

        Python: from [.]module import x  |  import module
        JS/TS:  import X from './y'  |  import './y'  |  require('./y')

        只解析相对导入（Python 相对导入 + JS 以 '.' 开头的 import），
        项目内的包级导入（Python 绝对路径）也会尝试解析。
        """
        base_dir = str(Path(rel_path).parent).replace("\\", "/")
        if base_dir == ".":
            base_dir = ""

        targets: List[str] = []

        if lang_name == "python":
            # from [.]module import x
            for m in re.finditer(r"from\s+(\.*)(\w[\w.]*)\s+import", code):
                dots = len(m.group(1))
                mod = m.group(2)
                t = self._resolve_python_module(mod, base_dir, dots)
                if t:
                    targets.append(t)
            # from . import x  (dot-only relative, treat module = x)
            for m in re.finditer(r"from\s+(\.+)\s+import\s+(\w+)", code):
                dots = len(m.group(1))
                mod = m.group(2)
                t = self._resolve_python_module(mod, base_dir, dots)
                if t:
                    targets.append(t)
            # import module
            for m in re.finditer(r"^import\s+(\w[\w.]*)", code, re.MULTILINE):
                t = self._resolve_python_module(m.group(1), base_dir, 0)
                if t:
                    targets.append(t)

        elif lang_name in _JS_LANGS:
            patterns = [
                r"""import\s+[\s\S]*?\bfrom\s+['"]([^'"]+)['"]""",
                r"""import\s+['"]([^'"]+)['"]""",
                r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""",
            ]
            for pat in patterns:
                for m in re.finditer(pat, code):
                    t = self._resolve_js_module(m.group(1), base_dir)
                    if t:
                        targets.append(t)

        for target in targets:
            if target != rel_path and target in self._all_rel_paths:
                self.graph.add_edge(rel_path, target, type="imports")

    # ------------------------------------------------------------------
    # Call edge resolution
    # ------------------------------------------------------------------

    def _add_call_edges(
        self,
        rel_path: str,
        cur_dir: str,
        imported_files: Set[str],
        potential_targets: List[str],
    ) -> None:
        """
        向图中添加 call 边，按优先级限制假阳性：

        Priority 1 — 同文件调用：100 % 确定，全部添加，直接返回。
        Priority 2 — 已导入文件中的函数：有 import 证据，高置信度，全部添加，返回。
        Priority 3 — 无 import 关系的跨文件调用：名称歧义，限制在
                     _MAX_AMBIGUOUS_CROSS_FILE_TARGETS 个文件（同目录优先），
                     每文件最多 1 个符号。
        """
        # Group potential targets by their source file
        by_file: Dict[str, List[str]] = {}
        for t in potential_targets:
            f = t.split(":")[0] if ":" in t else t
            by_file.setdefault(f, []).append(t)

        # Priority 1: same file
        if rel_path in by_file:
            for target in by_file[rel_path]:
                if self.current_context != target:
                    self.graph.add_edge(self.current_context, target, type="calls")
            return

        # Priority 2: explicitly imported files
        imported_matches = {f: ts for f, ts in by_file.items() if f in imported_files}
        if imported_matches:
            for targets in imported_matches.values():
                for target in targets[:1]:
                    if self.current_context != target:
                        self.graph.add_edge(self.current_context, target, type="calls")
            return

        # Priority 3: ambiguous cross-file — cap fan-out, prefer same directory
        other_files = [f for f in by_file if f != rel_path]

        def _file_sort_key(f: str) -> tuple:
            return (0 if str(Path(f).parent) == cur_dir else 1, f)

        for f in sorted(other_files, key=_file_sort_key)[:_MAX_AMBIGUOUS_CROSS_FILE_TARGETS]:
            for target in by_file[f][:1]:
                if self.current_context != target:
                    self.graph.add_edge(self.current_context, target, type="calls")

    # ------------------------------------------------------------------
    # Edge extraction (orchestrates import + call)
    # ------------------------------------------------------------------

    def _extract_edges(self, code: str, rel_path: str, lang_name: str) -> None:
        """提取 import 和 call 边。import 先提取，call 解析借助已知 import 信息。"""
        self._extract_import_edges(code, rel_path, lang_name)

        # Build the set of files this file explicitly imports — used to guide
        # call resolution toward high-confidence cross-file targets.
        imported_files: Set[str] = {
            v
            for _, v, d in self.graph.out_edges(rel_path, data=True)
            if d.get("type") == "imports"
        }

        parser = self.get_parser(lang_name)
        tree = parser.parse(bytes(code, "utf8"))

        self.current_context = rel_path
        cur_dir = str(Path(rel_path).parent)

        def traverse(node: Node) -> None:
            old_context = self.current_context

            if node.type in (
                "function_definition",
                "class_definition",
                "function_declaration",
                "class_declaration",
            ):
                name: Optional[str] = None
                for child in node.children:
                    if child.type == "identifier":
                        name = code[child.start_byte : child.end_byte]
                        break
                if name:
                    self.current_context = f"{rel_path}:{name}"

            if node.type in ("call", "call_expression"):
                call_name: Optional[str] = None
                for child in node.children:
                    if child.type in ("identifier", "attribute", "member_expression"):
                        full_call = code[child.start_byte : child.end_byte]
                        call_name = full_call.split(".")[-1]
                        break
                if call_name:
                    potential_targets = [
                        n
                        for n, d in self.graph.nodes(data=True)
                        if d.get("type") == "function" and d.get("name") == call_name
                    ]
                    if potential_targets:
                        self._add_call_edges(
                            rel_path, cur_dir, imported_files, potential_targets
                        )

            for child in node.children:
                traverse(child)

            self.current_context = old_context

        traverse(tree.root_node)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_file(path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None
