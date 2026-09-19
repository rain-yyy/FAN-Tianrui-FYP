"""
code_graph.py — Language-agnostic, multi-granularity property graph builder.

Follows the Canonical Design Specification:
  Node types : file | class | function | variable | field | external_symbol
  Edge types : contains | imports | calls | inherits | implements | decorates

Key design decisions:
  - Qualified names include class context: "MyClass.my_method" avoids name collisions
  - Node IDs are "{rel_path}:{qualified_name}" making them globally unique per repo
  - All nodes carry rich properties (location, raw_code, signature, visibility, …)
  - Tree-sitter offsets are BYTE offsets; all AST slices use code_bytes[s:e].decode()
    to handle multi-byte characters (Chinese, emoji, …) correctly.
  - Two-phase build: nodes first, then edges (so call-resolution can use name index)
"""

import hashlib
import re
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
from tree_sitter import Node, Parser

try:
    from src.ingestion.ts_parser import TreeSitterParser
except (ImportError, ModuleNotFoundError):
    # Fallback for standalone execution: load ts_parser directly, bypassing the
    # package __init__.py which may pull in heavy/unavailable dependencies.
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "ts_parser", Path(__file__).parent / "ts_parser.py"
    )
    _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
    _spec.loader.exec_module(_mod)       # type: ignore[union-attr]
    TreeSitterParser = _mod.TreeSitterParser

_MAX_AMBIGUOUS_CROSS_FILE_TARGETS = 2
_JS_LANGS: Set[str] = {"javascript", "typescript", "tsx"}


# ---------------------------------------------------------------------------
# Byte-safe code slice (core correctness fix)
# ---------------------------------------------------------------------------

def _bslice(code_bytes: bytes, start: int, end: int) -> str:
    """Return the UTF-8 string for code_bytes[start:end].

    Tree-sitter's start_byte / end_byte are byte offsets into the UTF-8
    encoded source.  Python str indexing operates on Unicode code-points,
    so ``code[start_byte:end_byte]`` is WRONG for files with multi-byte
    characters (CJK, emoji, …).  Always use this helper for AST slices.
    """
    return code_bytes[start:end].decode("utf-8", errors="replace")


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _visibility_python(name: str) -> str:
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


def _docstring_python(node: Node, code_bytes: bytes) -> Optional[str]:
    """Return the first string literal inside a Python function/class body."""
    for child in node.children:
        if child.type == "block":
            for stmt in child.children:
                if stmt.type == "expression_statement":
                    for expr in stmt.children:
                        if expr.type in ("string", "concatenated_string"):
                            raw = _bslice(code_bytes, expr.start_byte, expr.end_byte)
                            return raw.strip("\"' \n\t")
            break
    return None


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class CodeGraphBuilder:
    """
    Build a code property graph using Tree-sitter AST parsing + NetworkX.

    Nodes carry rich metadata; edges carry typed relationship labels.
    Compatible with CommunityEngine (uses node["type"] and node["file"]).
    """

    EXTENSION_TO_LANGUAGE = TreeSitterParser.EXTENSION_TO_LANGUAGE

    def __init__(self) -> None:
        self.graph: nx.DiGraph = nx.DiGraph()
        self.ts_parser = TreeSitterParser()
        self._all_rel_paths: Set[str] = set()
        # Short name → [node_id, …] for call-edge resolution
        self._name_to_nodes: Dict[str, List[str]] = {}
        self._call_context: str = ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_graph(self, repo_root: str, file_paths: List[str]) -> nx.DiGraph:
        """Two-phase build: extract nodes first, then edges."""
        repo_root_path = Path(repo_root)

        self._all_rel_paths = set()
        for fp in file_paths:
            try:
                self._all_rel_paths.add(
                    str(Path(fp).relative_to(repo_root_path)).replace("\\", "/")
                )
            except ValueError:
                pass

        # Phase 1 — nodes
        for fp in file_paths:
            rel = self._rel(fp, repo_root_path)
            lang = self.EXTENSION_TO_LANGUAGE.get(Path(fp).suffix.lower())
            if not lang:
                continue
            content = self._read_file(fp)
            if content is None:
                continue
            self._extract_nodes(content, rel, lang)

        self._build_name_index()

        # Phase 2 — edges
        for fp in file_paths:
            rel = self._rel(fp, repo_root_path)
            lang = self.EXTENSION_TO_LANGUAGE.get(Path(fp).suffix.lower())
            if not lang:
                continue
            content = self._read_file(fp)
            if content is None:
                continue
            self._extract_edges(content, rel, lang)

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
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _rel(fp: str, repo_root: Path) -> str:
        try:
            return str(Path(fp).relative_to(repo_root)).replace("\\", "/")
        except ValueError:
            return fp.replace("\\", "/")

    @staticmethod
    def _read_file(path: str) -> Optional[str]:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def _get_parser(self, lang: str) -> Optional[Parser]:
        try:
            return self.ts_parser.get_parser(lang)
        except ValueError:
            return None

    def _node_id(self, rel_path: str, qualified_name: str) -> str:
        return f"{rel_path}:{qualified_name}"

    def _upsert_node(self, node_id: str, **attrs: Any) -> None:
        if node_id in self.graph:
            self.graph.nodes[node_id].update(attrs)
        else:
            self.graph.add_node(node_id, **attrs)

    def _build_name_index(self) -> None:
        self._name_to_nodes = {}
        for nid, data in self.graph.nodes(data=True):
            if data.get("type") in ("function", "class"):
                name = data.get("name")
                if name:
                    self._name_to_nodes.setdefault(name, []).append(nid)

    # ------------------------------------------------------------------
    # Node extraction — top-level dispatcher
    # ------------------------------------------------------------------

    def _extract_nodes(self, code: str, rel_path: str, lang: str) -> None:
        cb = code.encode("utf-8")
        lines = code.splitlines()
        self._upsert_node(
            rel_path,
            type="file",
            node_type="FILE",
            name=Path(rel_path).name,
            qualified_name=rel_path,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=Path(rel_path).name,
            start_line=1,
            end_line=len(lines),
            line_count=len(lines),
            size_bytes=len(cb),
            is_entry_point=Path(rel_path).name in {
                "__init__.py", "main.py", "index.ts", "index.js",
                "index.tsx", "main.go", "lib.rs",
            },
        )

        parser = self._get_parser(lang)
        if parser is None:
            return
        tree = parser.parse(cb)

        if lang == "python":
            self._py_nodes(tree.root_node, cb, rel_path, lang, rel_path, [])
        elif lang in _JS_LANGS:
            self._js_nodes(tree.root_node, cb, rel_path, lang, rel_path, [])
        else:
            self._generic_nodes(tree.root_node, cb, rel_path, lang, rel_path, [])

    # ------------------------------------------------------------------
    # Shared AST helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ident(node: Node, cb: bytes) -> Optional[str]:
        """Return the first direct `identifier` child (byte-safe)."""
        for child in node.children:
            if child.type == "identifier":
                return _bslice(cb, child.start_byte, child.end_byte)
        return None

    @staticmethod
    def _js_name(node: Node, cb: bytes) -> Optional[str]:
        """Return first `identifier` or `type_identifier` child."""
        for child in node.children:
            if child.type in ("identifier", "type_identifier"):
                return _bslice(cb, child.start_byte, child.end_byte)
        return None

    # ------------------------------------------------------------------
    # Python node extraction
    # ------------------------------------------------------------------

    def _py_nodes(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
        decorator_names: Optional[List[str]] = None,
    ) -> None:
        if node.type == "decorated_definition":
            decs: List[str] = []
            inner: Optional[Node] = None
            for child in node.children:
                if child.type == "decorator":
                    txt = _bslice(cb, child.start_byte, child.end_byte)
                    decs.append(txt.lstrip("@").split("(")[0].strip())
                elif child.type in (
                    "function_definition", "class_definition", "async_function_definition"
                ):
                    inner = child
            if inner is not None:
                self._py_nodes(inner, cb, rel_path, lang, parent_id, class_stack, decs)
            return

        if node.type in ("function_definition", "async_function_definition"):
            self._py_function(node, cb, rel_path, lang, parent_id, class_stack, decorator_names)
            return

        if node.type == "class_definition":
            self._py_class(node, cb, rel_path, lang, parent_id, class_stack, decorator_names)
            return

        # Module-level or class-level constants / type aliases
        if node.type == "expression_statement" and len(class_stack) == 0:
            for child in node.children:
                if child.type == "assignment":
                    self._py_assignment(child, cb, rel_path, lang, parent_id, class_stack)
            return

        for child in node.children:
            self._py_nodes(child, cb, rel_path, lang, parent_id, class_stack)

    def _py_class(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
        decorator_names: Optional[List[str]] = None,
    ) -> None:
        name = self._ident(node, cb)
        if not name:
            return
        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)

        base_classes: List[str] = []
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type in ("identifier", "attribute"):
                        base_classes.append(_bslice(cb, arg.start_byte, arg.end_byte))

        docstring = _docstring_python(node, cb)
        self._upsert_node(
            nid,
            type="class",
            node_type="CLASS",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=(
                f"class {name}({', '.join(base_classes)})" if base_classes else f"class {name}"
            ),
            docstring=docstring,
            base_classes=base_classes,
            decorators=decorator_names or [],
            class_type="class",
            visibility=_visibility_python(name),
            is_exported=not name.startswith("_"),
            is_deprecated="deprecated" in (docstring or "").lower(),
            line_count=node.end_point[0] - node.start_point[0] + 1,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

        new_stack = class_stack + [name]
        for child in node.children:
            if child.type == "block":
                for stmt in child.children:
                    self._py_nodes(stmt, cb, rel_path, lang, nid, new_stack)

    def _py_function(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
        decorator_names: Optional[List[str]] = None,
    ) -> None:
        name = self._ident(node, cb)
        if not name:
            return
        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)
        is_method = len(class_stack) > 0
        is_async = node.type == "async_function_definition" or any(
            c.type == "async" for c in node.children
        )

        func_type = "method" if is_method else "function"
        if name == "__init__":
            func_type = "constructor"
        elif name == "__del__":
            func_type = "destructor"

        params: List[Dict[str, Any]] = []
        return_type: Optional[str] = None
        for child in node.children:
            if child.type == "parameters":
                for p in child.children:
                    pname: Optional[str] = None
                    ptype: Optional[str] = None
                    if p.type == "identifier":
                        pname = _bslice(cb, p.start_byte, p.end_byte)
                    elif p.type in ("typed_parameter", "typed_default_parameter"):
                        for pc in p.children:
                            if pc.type == "identifier" and pname is None:
                                pname = _bslice(cb, pc.start_byte, pc.end_byte)
                            elif pc.type == "type":
                                ptype = _bslice(cb, pc.start_byte, pc.end_byte)
                    elif p.type == "default_parameter":
                        for pc in p.children:
                            if pc.type == "identifier":
                                pname = _bslice(cb, pc.start_byte, pc.end_byte)
                                break
                    if pname and pname not in ("self", "cls"):
                        params.append({"name": pname, "type": ptype, "default_value": None})
            elif child.type == "type":
                return_type = _bslice(cb, child.start_byte, child.end_byte)

        param_str = ", ".join(
            (f"{p['name']}: {p['type']}" if p.get("type") else p["name"]) for p in params
        )
        sig = f"def {name}({param_str})"
        if return_type:
            sig += f" -> {return_type}"

        dec_list = decorator_names or []
        docstring = _docstring_python(node, cb)

        self._upsert_node(
            nid,
            type="function",
            node_type="FUNCTION",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=sig,
            docstring=docstring,
            params=params,
            return_type=return_type,
            function_type=func_type,
            is_async=is_async,
            is_static="staticmethod" in dec_list,
            is_abstract="abstractmethod" in dec_list,
            decorators=dec_list,
            visibility=_visibility_python(name),
            is_exported=not name.startswith("_"),
            is_deprecated="deprecated" in (docstring or "").lower(),
            line_count=node.end_point[0] - node.start_point[0] + 1,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

        new_stack = class_stack + [name]
        for child in node.children:
            if child.type == "block":
                for stmt in child.children:
                    self._py_nodes(stmt, cb, rel_path, lang, nid, new_stack)

    def _py_assignment(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        """Extract module-level constants (ALL_CAPS) and CapWords type aliases."""
        lhs: Optional[str] = None
        for i, child in enumerate(node.children):
            if i == 0 and child.type == "identifier":
                lhs = _bslice(cb, child.start_byte, child.end_byte)
                break
        if not lhs:
            return
        if not (lhs.isupper() or (lhs[0].isupper() and "_" not in lhs)):
            return

        qname = ".".join(class_stack + [lhs])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)
        var_type = "field" if class_stack else "variable"

        self._upsert_node(
            nid,
            type=var_type,
            node_type="FIELD" if class_stack else "VARIABLE",
            name=lhs,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=lhs,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=raw.split("\n")[0],
            is_const=lhs.isupper(),
            scope="class" if class_stack else "module",
            visibility="public",
            is_exported=True,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

    # ------------------------------------------------------------------
    # JavaScript / TypeScript node extraction
    # ------------------------------------------------------------------

    def _js_nodes(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        t = node.type

        if t == "export_statement":
            for child in node.children:
                if child.type not in ("export", "default", ";"):
                    self._js_nodes(child, cb, rel_path, lang, parent_id, class_stack)
            return

        if t in ("class_declaration", "class"):
            self._js_class(node, cb, rel_path, lang, parent_id, class_stack)
            return

        if t == "interface_declaration":
            self._js_interface(node, cb, rel_path, lang, parent_id, class_stack)
            return

        if t == "type_alias_declaration":
            self._js_type_alias(node, cb, rel_path, lang, parent_id, class_stack)
            return

        if t in ("function_declaration", "generator_function_declaration"):
            self._js_function(node, cb, rel_path, lang, parent_id, class_stack)
            return

        if t == "method_definition":
            self._js_method(node, cb, rel_path, lang, parent_id, class_stack)
            return

        if t in ("lexical_declaration", "variable_declaration"):
            self._js_variable(node, cb, rel_path, lang, parent_id, class_stack)
            return

        for child in node.children:
            self._js_nodes(child, cb, rel_path, lang, parent_id, class_stack)

    def _js_class(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        name = self._js_name(node, cb)
        if not name:
            return
        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)

        base_classes: List[str] = []
        implements_list: List[str] = []
        for child in node.children:
            if child.type == "extends_clause":
                for ec in child.children:
                    if ec.type in ("identifier", "member_expression"):
                        base_classes.append(_bslice(cb, ec.start_byte, ec.end_byte))
            elif child.type == "implements_clause":
                for ic in child.children:
                    if ic.type in ("identifier", "type_identifier", "generic_type"):
                        implements_list.append(_bslice(cb, ic.start_byte, ic.end_byte))

        self._upsert_node(
            nid,
            type="class",
            node_type="CLASS",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=f"class {name}",
            base_classes=base_classes,
            implements=implements_list,
            class_type="class",
            visibility="public",
            is_exported=True,
            line_count=node.end_point[0] - node.start_point[0] + 1,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

        new_stack = class_stack + [name]
        for child in node.children:
            if child.type == "class_body":
                for stmt in child.children:
                    self._js_nodes(stmt, cb, rel_path, lang, nid, new_stack)

    def _js_interface(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        name = self._js_name(node, cb)
        if not name:
            return
        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)

        self._upsert_node(
            nid,
            type="class",
            node_type="CLASS",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=f"interface {name}",
            class_type="interface",
            visibility="public",
            is_exported=True,
            line_count=node.end_point[0] - node.start_point[0] + 1,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

    def _js_type_alias(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        name = self._js_name(node, cb)
        if not name:
            return
        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)

        self._upsert_node(
            nid,
            type="variable",
            node_type="TYPE",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=raw.split("\n")[0],
            visibility="public",
            is_exported=True,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

    def _js_function(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        name = self._ident(node, cb)
        if not name:
            return
        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)
        is_async = any(c.type == "async" for c in node.children)

        self._upsert_node(
            nid,
            type="function",
            node_type="FUNCTION",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_col=node.start_point[1],
            end_col=node.end_point[1],
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=raw.split("\n")[0].rstrip("{").strip(),
            function_type="function",
            is_async=is_async,
            visibility="public",
            is_exported=True,
            line_count=node.end_point[0] - node.start_point[0] + 1,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

        new_stack = class_stack + [name]
        for child in node.children:
            if child.type == "statement_block":
                for stmt in child.children:
                    self._js_nodes(stmt, cb, rel_path, lang, nid, new_stack)

    def _js_method(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        name: Optional[str] = None
        is_static = False
        func_type = "method"
        for child in node.children:
            if child.type == "property_identifier":
                name = _bslice(cb, child.start_byte, child.end_byte)
            elif child.type == "static":
                is_static = True
            elif child.type == "get":
                func_type = "getter"
            elif child.type == "set":
                func_type = "setter"
        if name is None:
            name = self._ident(node, cb)
        if not name:
            return
        if name == "constructor":
            func_type = "constructor"

        qname = ".".join(class_stack + [name])
        nid = self._node_id(rel_path, qname)
        raw = _bslice(cb, node.start_byte, node.end_byte)
        is_async = any(c.type == "async" for c in node.children)

        self._upsert_node(
            nid,
            type="function",
            node_type="FUNCTION",
            name=name,
            qualified_name=qname,
            language=lang,
            file_path=rel_path,
            file=rel_path,
            label=name,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            raw_code=raw,
            code_hash=_sha256(raw),
            signature=raw.split("\n")[0].rstrip("{").strip(),
            function_type=func_type,
            is_async=is_async,
            is_static=is_static,
            visibility="public",
            is_exported=True,
            line_count=node.end_point[0] - node.start_point[0] + 1,
        )
        self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

    def _js_variable(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            var_name: Optional[str] = None
            value_node: Optional[Node] = None
            for vc in child.children:
                if vc.type == "identifier" and var_name is None:
                    var_name = _bslice(cb, vc.start_byte, vc.end_byte)
                elif vc.type in ("arrow_function", "function", "generator_function"):
                    value_node = vc
            if not var_name:
                continue

            if value_node is not None:
                qname = ".".join(class_stack + [var_name])
                nid = self._node_id(rel_path, qname)
                raw = _bslice(cb, node.start_byte, node.end_byte)
                is_async = any(c.type == "async" for c in value_node.children)
                self._upsert_node(
                    nid,
                    type="function",
                    node_type="FUNCTION",
                    name=var_name,
                    qualified_name=qname,
                    language=lang,
                    file_path=rel_path,
                    file=rel_path,
                    label=var_name,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    raw_code=raw,
                    code_hash=_sha256(raw),
                    signature=_bslice(cb, child.start_byte, child.end_byte).split("\n")[0],
                    function_type=(
                        "arrow_function" if value_node.type == "arrow_function" else "function"
                    ),
                    is_async=is_async,
                    visibility="public",
                    is_exported=True,
                    line_count=child.end_point[0] - child.start_point[0] + 1,
                )
                self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")
            elif not class_stack and (var_name.isupper() or var_name[0].isupper()):
                qname = var_name
                nid = self._node_id(rel_path, qname)
                raw = _bslice(cb, node.start_byte, node.end_byte)
                self._upsert_node(
                    nid,
                    type="variable",
                    node_type="VARIABLE",
                    name=var_name,
                    qualified_name=qname,
                    language=lang,
                    file_path=rel_path,
                    file=rel_path,
                    label=var_name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    raw_code=raw,
                    code_hash=_sha256(raw),
                    signature=raw.split("\n")[0],
                    is_const=var_name.isupper(),
                    scope="module",
                    visibility="public",
                    is_exported=True,
                )
                self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")

    # ------------------------------------------------------------------
    # Generic node extraction (Go, Java, Rust, C/C++, Ruby …)
    # ------------------------------------------------------------------

    _GENERIC_CLASS_TYPES: Set[str] = {
        "class_declaration", "class_definition",
        "struct_item", "impl_item",
        "type_declaration",
        "enum_declaration",
        "interface_declaration",
    }
    _GENERIC_FUNC_TYPES: Set[str] = {
        "function_definition", "function_declaration",
        "method_declaration", "method_definition",
        "fn_item",
        "func_literal",
        "constructor_declaration",
    }

    def _generic_nodes(
        self,
        node: Node,
        cb: bytes,
        rel_path: str,
        lang: str,
        parent_id: str,
        class_stack: List[str],
    ) -> None:
        if node.type in self._GENERIC_CLASS_TYPES:
            name = self._ident(node, cb) or self._js_name(node, cb)
            if name:
                qname = ".".join(class_stack + [name])
                nid = self._node_id(rel_path, qname)
                raw = _bslice(cb, node.start_byte, node.end_byte)
                self._upsert_node(
                    nid,
                    type="class",
                    node_type="CLASS",
                    name=name,
                    qualified_name=qname,
                    language=lang,
                    file_path=rel_path,
                    file=rel_path,
                    label=name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    raw_code=raw,
                    code_hash=_sha256(raw),
                    signature=f"class {name}",
                    class_type="class",
                    visibility="public",
                    is_exported=True,
                    line_count=node.end_point[0] - node.start_point[0] + 1,
                )
                self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")
                new_stack = class_stack + [name]
                for child in node.children:
                    self._generic_nodes(child, cb, rel_path, lang, nid, new_stack)
                return

        elif node.type in self._GENERIC_FUNC_TYPES:
            name = self._ident(node, cb)
            if not name:
                for child in node.children:
                    if child.type == "field_identifier":
                        name = _bslice(cb, child.start_byte, child.end_byte)
                        break
            if name:
                qname = ".".join(class_stack + [name])
                nid = self._node_id(rel_path, qname)
                raw = _bslice(cb, node.start_byte, node.end_byte)
                self._upsert_node(
                    nid,
                    type="function",
                    node_type="FUNCTION",
                    name=name,
                    qualified_name=qname,
                    language=lang,
                    file_path=rel_path,
                    file=rel_path,
                    label=name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    raw_code=raw,
                    code_hash=_sha256(raw),
                    signature=raw.split("\n")[0],
                    function_type="method" if class_stack else "function",
                    visibility="public",
                    is_exported=True,
                    line_count=node.end_point[0] - node.start_point[0] + 1,
                )
                self.graph.add_edge(parent_id, nid, type="contains", edge_type="CONTAINS")
                new_stack = class_stack + [name]
                for child in node.children:
                    self._generic_nodes(child, cb, rel_path, lang, nid, new_stack)
                return

        for child in node.children:
            self._generic_nodes(child, cb, rel_path, lang, parent_id, class_stack)

    # ------------------------------------------------------------------
    # Edge extraction — phase 2
    # ------------------------------------------------------------------

    def _extract_edges(self, code: str, rel_path: str, lang: str) -> None:
        self._extract_import_edges(code, rel_path, lang)
        self._extract_semantic_edges(code, rel_path, lang)
        self._extract_call_edges(code, rel_path, lang)

    # ── Imports ────────────────────────────────────────────────────────

    def _resolve_python_module(self, module: str, base_dir: str, dots: int) -> Optional[str]:
        mod_path = module.replace(".", "/")
        if dots > 0:
            parent = Path(base_dir) if base_dir else Path(".")
            for _ in range(dots - 1):
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
        if not mod.startswith("."):
            return None
        base = Path(base_dir) if base_dir else Path(".")
        candidate = str(base / mod).replace("\\", "/")
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
            if f"{candidate}{ext}" in self._all_rel_paths:
                return f"{candidate}{ext}"
        for ext in (".ts", ".tsx", ".js", ".jsx"):
            if f"{candidate}/index{ext}" in self._all_rel_paths:
                return f"{candidate}/index{ext}"
        if candidate in self._all_rel_paths:
            return candidate
        return None

    def _extract_import_edges(self, code: str, rel_path: str, lang: str) -> None:
        base_dir = str(Path(rel_path).parent).replace("\\", "/")
        if base_dir == ".":
            base_dir = ""
        targets: List[str] = []

        if lang == "python":
            for m in re.finditer(r"from\s+(\.*)(\w[\w.]*)\s+import", code):
                t = self._resolve_python_module(m.group(2), base_dir, len(m.group(1)))
                if t:
                    targets.append(t)
            for m in re.finditer(r"from\s+(\.+)\s+import\s+(\w+)", code):
                t = self._resolve_python_module(m.group(2), base_dir, len(m.group(1)))
                if t:
                    targets.append(t)
            for m in re.finditer(r"^import\s+(\w[\w.]*)", code, re.MULTILINE):
                t = self._resolve_python_module(m.group(1), base_dir, 0)
                if t:
                    targets.append(t)
        elif lang in _JS_LANGS:
            for pat in [
                r"""import\s+[\s\S]*?\bfrom\s+['"]([^'"]+)['"]""",
                r"""import\s+['"]([^'"]+)['"]""",
                r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""",
            ]:
                for m in re.finditer(pat, code):
                    t = self._resolve_js_module(m.group(1), base_dir)
                    if t:
                        targets.append(t)

        for target in set(targets):
            if target != rel_path and target in self._all_rel_paths:
                if not self.graph.has_node(target):
                    self.graph.add_node(target, type="file", file=target, label=target)
                self.graph.add_edge(rel_path, target, type="imports", edge_type="IMPORTS")

    # ── Semantic edges (inherits / implements) ─────────────────────────

    def _extract_semantic_edges(self, code: str, rel_path: str, lang: str) -> None:
        parser = self._get_parser(lang)
        if parser is None:
            return
        cb = code.encode("utf-8")
        tree = parser.parse(cb)
        if lang == "python":
            self._py_semantic(tree.root_node, cb, rel_path, [])
        elif lang in _JS_LANGS:
            self._js_semantic(tree.root_node, cb, rel_path, [])

    def _py_semantic(
        self, node: Node, cb: bytes, rel_path: str, class_stack: List[str]
    ) -> None:
        if node.type == "decorated_definition":
            inner: Optional[Node] = None
            for child in node.children:
                if child.type in (
                    "class_definition", "function_definition", "async_function_definition"
                ):
                    inner = child
            if inner is not None:
                self._py_semantic(inner, cb, rel_path, class_stack)
            return

        if node.type == "class_definition":
            name = self._ident(node, cb)
            if name:
                qname = ".".join(class_stack + [name])
                src_id = self._node_id(rel_path, qname)
                for child in node.children:
                    if child.type == "argument_list":
                        for arg in child.children:
                            if arg.type in ("identifier", "attribute"):
                                base = _bslice(cb, arg.start_byte, arg.end_byte).split(".")[-1]
                                for cid in self._name_to_nodes.get(base, []):
                                    if self.graph.nodes[cid].get("type") == "class":
                                        if self.graph.has_node(src_id):
                                            self.graph.add_edge(
                                                src_id, cid,
                                                type="inherits",
                                                edge_type="INHERITS",
                                            )
                                        break
                new_stack = class_stack + [name]
                for child in node.children:
                    if child.type == "block":
                        for stmt in child.children:
                            self._py_semantic(stmt, cb, rel_path, new_stack)
            return

        for child in node.children:
            self._py_semantic(child, cb, rel_path, class_stack)

    def _js_semantic(
        self, node: Node, cb: bytes, rel_path: str, class_stack: List[str]
    ) -> None:
        if node.type == "export_statement":
            for child in node.children:
                self._js_semantic(child, cb, rel_path, class_stack)
            return

        if node.type in ("class_declaration", "class"):
            name = self._js_name(node, cb)
            if name:
                qname = ".".join(class_stack + [name])
                src_id = self._node_id(rel_path, qname)
                for child in node.children:
                    if child.type == "extends_clause":
                        for ec in child.children:
                            if ec.type in ("identifier", "member_expression"):
                                base = _bslice(cb, ec.start_byte, ec.end_byte).split(".")[-1]
                                for cid in self._name_to_nodes.get(base, []):
                                    if self.graph.nodes[cid].get("type") == "class":
                                        if self.graph.has_node(src_id):
                                            self.graph.add_edge(
                                                src_id, cid,
                                                type="inherits",
                                                edge_type="INHERITS",
                                            )
                                        break
                    elif child.type == "implements_clause":
                        for ic in child.children:
                            if ic.type in ("identifier", "type_identifier"):
                                iface = _bslice(cb, ic.start_byte, ic.end_byte)
                                for cid in self._name_to_nodes.get(iface, []):
                                    if self.graph.nodes[cid].get("type") == "class":
                                        if self.graph.has_node(src_id):
                                            self.graph.add_edge(
                                                src_id, cid,
                                                type="implements",
                                                edge_type="IMPLEMENTS",
                                            )
                                        break
                new_stack = class_stack + [name]
                for child in node.children:
                    if child.type == "class_body":
                        for stmt in child.children:
                            self._js_semantic(stmt, cb, rel_path, new_stack)
            return

        for child in node.children:
            self._js_semantic(child, cb, rel_path, class_stack)

    # ── Call edges ─────────────────────────────────────────────────────

    def _extract_call_edges(self, code: str, rel_path: str, lang: str) -> None:
        imported_files: Set[str] = {
            v
            for _, v, d in self.graph.out_edges(rel_path, data=True)
            if d.get("type") == "imports"
        }
        parser = self._get_parser(lang)
        if parser is None:
            return
        cb = code.encode("utf-8")
        tree = parser.parse(cb)
        self._call_context = rel_path
        cur_dir = str(Path(rel_path).parent)

        def traverse(node: Node) -> None:
            old_ctx = self._call_context

            if node.type in (
                "function_definition", "async_function_definition",
                "class_definition",
                "function_declaration", "class_declaration",
                "method_definition", "fn_item", "method_declaration",
                "generator_function_declaration",
            ):
                name = self._ident(node, cb)
                if name is None and node.type == "method_definition":
                    for child in node.children:
                        if child.type == "property_identifier":
                            name = _bslice(cb, child.start_byte, child.end_byte)
                            break
                if name:
                    sl = node.start_point[0] + 1
                    # Match by file prefix + name + start_line to resolve qualified name
                    candidates = [
                        nid for nid in self.graph.nodes
                        if nid.startswith(f"{rel_path}:")
                        and self.graph.nodes[nid].get("name") == name
                        and self.graph.nodes[nid].get("start_line") == sl
                    ]
                    if candidates:
                        self._call_context = candidates[0]

            if node.type in ("call", "call_expression"):
                call_name: Optional[str] = None
                for child in node.children:
                    if child.type in ("identifier", "attribute", "member_expression"):
                        full = _bslice(cb, child.start_byte, child.end_byte)
                        call_name = full.split(".")[-1]
                        break
                if call_name:
                    targets = self._name_to_nodes.get(call_name, [])
                    if targets:
                        self._add_call_edges(rel_path, cur_dir, imported_files, targets)

            for child in node.children:
                traverse(child)

            self._call_context = old_ctx

        traverse(tree.root_node)

    def _add_call_edges(
        self,
        rel_path: str,
        cur_dir: str,
        imported_files: Set[str],
        potential_targets: List[str],
    ) -> None:
        by_file: Dict[str, List[str]] = {}
        for t in potential_targets:
            f = t.split(":")[0] if ":" in t else t
            by_file.setdefault(f, []).append(t)

        # Priority 1 — same file
        if rel_path in by_file:
            for target in by_file[rel_path]:
                if self._call_context != target and self.graph.has_node(target):
                    self.graph.add_edge(
                        self._call_context, target, type="calls", edge_type="CALLS"
                    )
            return

        # Priority 2 — explicitly imported files
        imported_matches = {f: ts for f, ts in by_file.items() if f in imported_files}
        if imported_matches:
            for ts in imported_matches.values():
                for target in ts[:1]:
                    if self._call_context != target and self.graph.has_node(target):
                        self.graph.add_edge(
                            self._call_context, target, type="calls", edge_type="CALLS"
                        )
            return

        # Priority 3 — ambiguous cross-file (limit fan-out, same dir preferred)
        other_files = [f for f in by_file if f != rel_path]

        def sort_key(f: str) -> Tuple[int, str]:
            return (0 if str(Path(f).parent) == cur_dir else 1, f)

        for f in sorted(other_files, key=sort_key)[:_MAX_AMBIGUOUS_CROSS_FILE_TARGETS]:
            for target in by_file[f][:1]:
                if self._call_context != target and self.graph.has_node(target):
                    self.graph.add_edge(
                        self._call_context, target, type="calls", edge_type="CALLS"
                    )

