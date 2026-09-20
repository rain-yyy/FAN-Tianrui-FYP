"""
Code graph query tool: structural queries (definitions, callers/callees, class
hierarchy, imports, module deps) against the pre-built NetworkX property graph
produced by ingestion (`src/ingestion/code_graph.py`).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from langchain_core.tools import StructuredTool

from src.chat.tools.schemas import CodeGraphArgs
from src.utils.async_utils import run_sync

logger = logging.getLogger("app.chat.tools.graph")


@lru_cache(maxsize=16)
def _load_graph_cached(path: str, mtime: float) -> nx.DiGraph:
    """
    `CodeGraphEngine` is rebuilt fresh on every chat turn (see
    `build_tools_for_session`), but the underlying property graph file only
    changes when ingestion re-runs for that repo — so the parsed graph itself
    is cached here, keyed on the file's mtime, instead of re-reading and
    re-parsing potentially tens of thousands of nodes on every message. The
    mtime in the key makes this self-invalidating: a re-ingested graph gets a
    new mtime and simply misses the cache. Safe to share across concurrent
    engines/requests since every query in this module only reads the graph.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return nx.node_link_graph(data)


class CodeGraphEngine:
    def __init__(self, graph_path: Optional[str] = None):
        self.graph: nx.DiGraph = nx.DiGraph()
        if graph_path and Path(graph_path).exists():
            self._load_graph(graph_path)

    def _load_graph(self, path: str) -> None:
        try:
            mtime = Path(path).stat().st_mtime
            self.graph = _load_graph_cached(path, mtime)
            logger.info(
                "Loaded code graph with %d nodes and %d edges",
                self.graph.number_of_nodes(),
                self.graph.number_of_edges(),
            )
        except Exception as e:
            logger.error("Failed to load graph from %s: %s", path, e)
            self.graph = nx.DiGraph()

    def query(
        self,
        operation: str,
        symbol_name: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        handlers = {
            "find_definition": lambda: self._find_definition(symbol_name, file_path),
            "find_callers": lambda: self._find_callers(symbol_name, file_path),
            "find_callees": lambda: self._find_callees(symbol_name, file_path),
            "get_class_hierarchy": lambda: self._get_class_hierarchy(symbol_name),
            "get_file_symbols": lambda: self._get_file_symbols(file_path),
            "get_all_symbols": lambda: self._get_all_symbols(),
            "find_imports": lambda: self._find_imports(file_path),
            "get_module_dependencies": lambda: self._get_module_dependencies(file_path or symbol_name),
        }
        handler = handlers.get(operation)
        if handler is None:
            return f"Unknown operation: {operation}", {"error": "unknown_operation"}
        return handler()

    def _find_definition(self, symbol_name: Optional[str], file_path: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not symbol_name:
            return "Error: symbol_name is required", {"error": "missing_symbol"}

        matches = []
        for node_id, data in self.graph.nodes(data=True):
            name_hit = (
                data.get("name") == symbol_name
                or node_id.endswith(f":{symbol_name}")
                or (data.get("qualified_name") or "") == symbol_name
            )
            if not name_hit:
                continue
            if file_path and file_path not in data.get("file", ""):
                continue
            matches.append({
                "type": data.get("type", ""),
                "file": data.get("file", ""),
                "qualified_name": data.get("qualified_name", data.get("name", "")),
                "start_line": data.get("start_line"),
                "end_line": data.get("end_line"),
                "signature": data.get("signature"),
            })

        if not matches:
            return f"No definition found for symbol: {symbol_name}", {"symbols_found": []}

        lines = [f"Found {len(matches)} definition(s) for '{symbol_name}':", ""]
        for m in matches:
            lines.append(f"  [{m['type']}] {m['qualified_name']}")
            lines.append(f"    File: {m['file']}")
            if m.get("start_line"):
                loc = f"{m['start_line']}-{m.get('end_line') or m['start_line']}"
                lines.append(f"    Lines: {loc}")
            if m.get("signature"):
                lines.append(f"    Signature: {m['signature']}")
            lines.append("")

        return "\n".join(lines), {
            "relation_type": "definition",
            "symbols_found": [m["qualified_name"] for m in matches],
            "matches": matches,
        }

    def _find_callers(self, symbol_name: Optional[str], file_path: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not symbol_name:
            return "Error: symbol_name is required", {"error": "missing_symbol"}

        targets = [
            n for n, d in self.graph.nodes(data=True)
            if d.get("name") == symbol_name or n.endswith(f":{symbol_name}") or (d.get("qualified_name") or "") == symbol_name
        ]
        if not targets:
            return f"Symbol not found in graph: {symbol_name}", {"symbols_found": []}

        seen: set = set()
        callers = []
        for target in targets:
            for pred in self.graph.predecessors(target):
                if pred in seen:
                    continue
                edge = self.graph.get_edge_data(pred, target)
                if not (edge and edge.get("type") == "calls"):
                    continue
                pred_data = self.graph.nodes[pred]
                if file_path and file_path not in pred_data.get("file", ""):
                    continue
                seen.add(pred)
                callers.append({
                    "caller_name": pred_data.get("qualified_name") or pred_data.get("name", pred),
                    "file": pred_data.get("file", "unknown"),
                    "start_line": pred_data.get("start_line"),
                })

        if not callers:
            return f"No callers found for: {symbol_name}", {"relation_type": "calls", "symbols_found": []}

        lines = [f"Found {len(callers)} caller(s) of '{symbol_name}':", ""]
        for c in callers:
            loc = f" [line {c['start_line']}]" if c.get("start_line") else ""
            lines.append(f"  {c['caller_name']}{loc} (in {c['file']}) -> calls -> {symbol_name}")

        return "\n".join(lines), {
            "relation_type": "calls",
            "symbols_found": [c["caller_name"] for c in callers],
            "matches": callers,
        }

    def _find_callees(self, symbol_name: Optional[str], file_path: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not symbol_name:
            return "Error: symbol_name is required", {"error": "missing_symbol"}

        sources = [
            n for n, d in self.graph.nodes(data=True)
            if d.get("name") == symbol_name or n.endswith(f":{symbol_name}") or (d.get("qualified_name") or "") == symbol_name
        ]
        if not sources:
            return f"Symbol not found in graph: {symbol_name}", {"symbols_found": []}

        seen: set = set()
        callees = []
        for source in sources:
            source_data = self.graph.nodes[source]
            if file_path and file_path not in source_data.get("file", ""):
                continue
            for succ in self.graph.successors(source):
                if succ in seen:
                    continue
                edge = self.graph.get_edge_data(source, succ)
                if not (edge and edge.get("type") == "calls"):
                    continue
                seen.add(succ)
                succ_data = self.graph.nodes[succ]
                callees.append({
                    "callee_name": succ_data.get("qualified_name") or succ_data.get("name", succ),
                    "file": succ_data.get("file", "unknown"),
                    "start_line": succ_data.get("start_line"),
                })

        if not callees:
            return f"No callees found for: {symbol_name}", {"relation_type": "calls", "symbols_found": []}

        lines = [f"'{symbol_name}' calls {len(callees)} function(s):", ""]
        for c in callees:
            loc = f" [line {c['start_line']}]" if c.get("start_line") else ""
            lines.append(f"  {symbol_name} -> {c['callee_name']}{loc} (defined in {c['file']})")

        return "\n".join(lines), {
            "relation_type": "calls",
            "symbols_found": [c["callee_name"] for c in callees],
            "matches": callees,
        }

    def _get_class_hierarchy(self, class_name: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not class_name:
            return "Error: class_name is required", {"error": "missing_symbol"}

        class_nodes = [
            (n, d) for n, d in self.graph.nodes(data=True)
            if d.get("type") == "class" and (d.get("name") == class_name or n.endswith(f":{class_name}"))
        ]
        if not class_nodes:
            return f"Class not found: {class_name}", {"symbols_found": []}

        lines = [f"Class hierarchy for '{class_name}':", ""]
        symbols_found = []
        for node_id, data in class_nodes:
            cls_name = data.get("name", node_id)
            lines.append(f"Class: {cls_name} (in {data.get('file', 'unknown')})")
            symbols_found.append(cls_name)

            parents = [
                self.graph.nodes[succ].get("name", succ)
                for succ in self.graph.successors(node_id)
                if (self.graph.get_edge_data(node_id, succ) or {}).get("type") == "inherits"
            ]
            if parents:
                lines.append(f"  Inherits from: {', '.join(parents)}")

            subclasses = [
                self.graph.nodes[pred].get("name", pred)
                for pred in self.graph.predecessors(node_id)
                if (self.graph.get_edge_data(pred, node_id) or {}).get("type") == "inherits"
            ]
            if subclasses:
                lines.append(f"  Subclasses: {', '.join(subclasses)}")

            methods = [
                self.graph.nodes[succ].get("name", succ)
                for succ in self.graph.successors(node_id)
                if (self.graph.get_edge_data(node_id, succ) or {}).get("type") == "contains"
                and self.graph.nodes[succ].get("type") == "function"
            ]
            if methods:
                lines.append(f"  Methods ({len(methods)}): {', '.join(methods[:12])}")
            lines.append("")

        return "\n".join(lines), {"relation_type": "inherits", "symbols_found": symbols_found}

    def _get_file_symbols(self, file_path: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not file_path:
            return "Error: file_path is required", {"error": "missing_file_path"}

        symbols = []
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if not (file_path in node_file or node_file.endswith(file_path)):
                continue
            if data.get("type") == "file":
                continue
            symbols.append({
                "name": data.get("qualified_name") or data.get("name", node_id),
                "type": data.get("type", "unknown"),
                "start_line": data.get("start_line"),
            })

        if not symbols:
            return f"No symbols found in file: {file_path}", {"symbols_found": []}

        symbols.sort(key=lambda s: s.get("start_line") or 0)
        lines = [f"Symbols in '{file_path}':", ""]
        for s in symbols[:40]:
            loc = f" [line {s['start_line']}]" if s.get("start_line") else ""
            lines.append(f"  [{s['type']}] {s['name']}{loc}")
        if len(symbols) > 40:
            lines.append(f"  ... and {len(symbols) - 40} more")

        return "\n".join(lines), {"relation_type": "contains", "symbols_found": [s["name"] for s in symbols]}

    def _get_all_symbols(self, limit: int = 50) -> Tuple[str, Dict[str, Any]]:
        classes, functions, files = [], [], []
        for node_id, data in self.graph.nodes(data=True):
            node_type = data.get("type", "")
            if node_type == "class":
                classes.append({"name": data.get("name", node_id), "file": data.get("file", "unknown")})
            elif node_type == "function":
                functions.append({
                    "name": data.get("qualified_name") or data.get("name", node_id),
                    "file": data.get("file", "unknown"),
                    "function_type": data.get("function_type", "function"),
                })
            elif node_type == "file":
                files.append(node_id)

        lines = [
            "Code Graph Overview:",
            f"  Nodes: {self.graph.number_of_nodes()}  Edges: {self.graph.number_of_edges()}",
            f"  Files: {len(files)}  Classes: {len(classes)}  Functions: {len(functions)}",
            "",
        ]
        top_fns = [f for f in functions if f["function_type"] != "method"]
        if classes:
            lines.append(f"Classes ({len(classes)}):")
            for c in classes[:limit // 2]:
                lines.append(f"  - {c['name']} ({c['file']})")
        if top_fns:
            lines.append(f"\nTop-level functions ({len(top_fns)}):")
            for fn in top_fns[:limit // 2]:
                lines.append(f"  - {fn['name']} ({fn['file']})")

        return "\n".join(lines), {
            "relation_type": "overview",
            "symbols_found": [c["name"] for c in classes[:10]] + [f["name"] for f in functions[:10]],
        }

    def _find_imports(self, file_path: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not file_path:
            return "Error: file_path is required", {"error": "missing_file_path"}

        file_node_id = None
        for node_id, data in self.graph.nodes(data=True):
            if data.get("type") == "file" and (node_id == file_path or node_id.endswith(file_path) or file_path in node_id):
                file_node_id = node_id
                break
        if file_node_id is None:
            return f"File node not found in graph: {file_path}", {"symbols_found": []}

        imports = [
            succ for succ in self.graph.successors(file_node_id)
            if (self.graph.get_edge_data(file_node_id, succ) or {}).get("type") == "imports"
        ]
        imported_by = [
            pred for pred in self.graph.predecessors(file_node_id)
            if (self.graph.get_edge_data(pred, file_node_id) or {}).get("type") == "imports"
        ]

        lines = [f"Import relationships for '{file_node_id}':", ""]
        if imports:
            lines.append(f"Imports ({len(imports)}): {', '.join(imports[:20])}")
        if imported_by:
            lines.append(f"Imported by ({len(imported_by)}): {', '.join(imported_by[:20])}")
        if not imports and not imported_by:
            lines.append("No import relationships found.")

        return "\n".join(lines), {"relation_type": "imports", "symbols_found": imports + imported_by}

    def _get_module_dependencies(self, module_path: Optional[str]) -> Tuple[str, Dict[str, Any]]:
        if not module_path:
            return "Error: module_path is required", {"error": "missing_module_path"}

        internal_deps: set = set()
        external_deps: set = set()
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if module_path not in node_file:
                continue
            for succ in self.graph.successors(node_id):
                edge = self.graph.get_edge_data(node_id, succ)
                if not (edge and edge.get("type") in ("imports", "calls")):
                    continue
                succ_file = self.graph.nodes[succ].get("file", "")
                if not succ_file:
                    continue
                (internal_deps if module_path in succ_file else external_deps).add(succ_file)

        lines = [f"Module dependencies for '{module_path}':", ""]
        if internal_deps:
            lines.append(f"Internal ({len(internal_deps)}): {', '.join(sorted(internal_deps)[:15])}")
        if external_deps:
            lines.append(f"External ({len(external_deps)}): {', '.join(sorted(external_deps)[:15])}")

        return "\n".join(lines), {
            "relation_type": "module_dependency",
            "symbols_found": list(internal_deps | external_deps)[:20],
        }

    def is_loaded(self) -> bool:
        return self.graph.number_of_nodes() > 0


def build_code_graph_tool(graph_path: str) -> StructuredTool:
    engine = CodeGraphEngine(graph_path)

    def _run(
        operation: str,
        symbol_name: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        try:
            return engine.query(operation, symbol_name, file_path)
        except Exception as e:
            logger.exception("Code graph query failed")
            return f"Query failed: {e}", {"error": str(e)}

    async def _arun(
        operation: str,
        symbol_name: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        return await run_sync(_run, operation, symbol_name, file_path)

    return StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name="code_graph",
        description=(
            "Query structural relationships in this repository's code graph: symbol definitions, "
            "callers/callees, class hierarchy, file symbols, imports, and module dependencies. "
            "Prefer this over rag_search when precision about exact locations/relationships matters."
        ),
        args_schema=CodeGraphArgs,
        response_format="content_and_artifact",
    )
