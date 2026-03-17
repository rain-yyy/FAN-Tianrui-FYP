"""
代码知识图谱查询工具

基于已构建的 NetworkX 图谱，提供以下查询能力：
- find_definition: 查找符号定义位置
- find_callers: 查找调用某函数的位置
- find_callees: 查找某函数调用的其他函数
- get_class_hierarchy: 获取类的继承关系
- get_file_symbols: 获取文件中定义的所有符号
- find_imports: 查找导入关系
- get_module_dependencies: 获取模块依赖关系

增强版特性：
- 结构化返回带关系标签
- 支持锚点提取
- 返回更丰富的元数据
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Literal, Tuple

import networkx as nx

from src.agent.state import ContextPiece

logger = logging.getLogger("app.agent.tools.graph")


class CodeGraphTool:
    """
    代码知识图谱查询工具
    
    封装对 NetworkX 图谱的查询操作，提供结构化的代码关系查询能力。
    """
    
    OPERATIONS = Literal[
        "find_definition",
        "find_callers", 
        "find_callees",
        "get_class_hierarchy",
        "get_file_symbols",
        "get_all_symbols",
        "find_imports",
        "get_module_dependencies"
    ]
    
    # 关系类型标签
    RELATION_LABELS = {
        "calls": "调用",
        "contains": "包含",
        "inherits": "继承",
        "imports": "导入",
        "references": "引用",
        "implements": "实现",
    }
    
    def __init__(self, graph_path: Optional[str] = None):
        self.graph: nx.DiGraph = nx.DiGraph()
        self.graph_path = graph_path
        
        if graph_path and Path(graph_path).exists():
            self._load_graph(graph_path)
    
    def _load_graph(self, path: str) -> None:
        """加载图谱"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.graph = nx.node_link_graph(data)
            logger.info(f"Loaded code graph with {self.graph.number_of_nodes()} nodes and {self.graph.number_of_edges()} edges")
        except Exception as e:
            logger.error(f"Failed to load graph from {path}: {e}")
            self.graph = nx.DiGraph()
    
    def execute(
        self,
        operation: str,
        symbol_name: Optional[str] = None,
        file_path: Optional[str] = None,
        **kwargs
    ) -> ContextPiece:
        """
        执行图谱查询操作
        
        返回结构化的 ContextPiece，包含：
        - 查询结果内容
        - 关系标签
        - 用于锚点提取的元数据
        """
        try:
            if operation == "find_definition":
                result, metadata = self._find_definition(symbol_name, file_path)
            elif operation == "find_callers":
                result, metadata = self._find_callers(symbol_name, file_path)
            elif operation == "find_callees":
                result, metadata = self._find_callees(symbol_name, file_path)
            elif operation == "get_class_hierarchy":
                result, metadata = self._get_class_hierarchy(symbol_name)
            elif operation == "get_file_symbols":
                result, metadata = self._get_file_symbols(file_path)
            elif operation == "get_all_symbols":
                result, metadata = self._get_all_symbols()
            elif operation == "find_imports":
                result, metadata = self._find_imports(file_path)
            elif operation == "get_module_dependencies":
                result, metadata = self._get_module_dependencies(file_path or symbol_name)
            else:
                result = f"Unknown operation: {operation}"
                metadata = {"error": "unknown_operation"}
            
            # 计算相关性分数
            relevance = self._compute_relevance(operation, metadata)
            
            return ContextPiece(
                source=f"code_graph.{operation}",
                content=result,
                file_path=metadata.get("primary_file"),
                line_range=metadata.get("line_range"),
                relevance_score=relevance,
                metadata={
                    "operation": operation,
                    "symbol_name": symbol_name,
                    "file_path": file_path,
                    "relation_type": metadata.get("relation_type"),
                    "symbols_found": metadata.get("symbols_found", []),
                    "anchor_candidates": metadata.get("anchor_candidates", []),
                }
            )
            
        except Exception as e:
            logger.error(f"Graph query failed: {e}")
            return ContextPiece(
                source=f"code_graph.{operation}",
                content=f"Query failed: {str(e)}",
                relevance_score=0.0,
                metadata={"error": str(e)}
            )
    
    def _compute_relevance(self, operation: str, metadata: Dict[str, Any]) -> float:
        """计算结果的相关性分数"""
        base_scores = {
            "find_definition": 0.9,
            "find_callers": 0.85,
            "find_callees": 0.85,
            "get_class_hierarchy": 0.8,
            "get_file_symbols": 0.75,
            "get_all_symbols": 0.7,
            "find_imports": 0.75,
            "get_module_dependencies": 0.75,
        }
        
        score = base_scores.get(operation, 0.7)
        
        # 找到结果则提高分数
        if metadata.get("symbols_found"):
            score = min(1.0, score + 0.05)
        
        # 有错误则降低分数
        if metadata.get("error"):
            score = 0.0
        
        return score
    
    def _find_definition(self, symbol_name: str, file_path: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """查找符号定义位置"""
        if not symbol_name:
            return "Error: symbol_name is required", {"error": "missing_symbol"}
        
        matches = []
        for node_id, data in self.graph.nodes(data=True):
            node_name = data.get("name", "")
            node_type = data.get("type", "")
            node_file = data.get("file", "")
            
            if node_name == symbol_name or node_id.endswith(f":{symbol_name}"):
                if file_path and file_path not in node_file:
                    continue
                matches.append({
                    "node_id": node_id,
                    "type": node_type,
                    "file": node_file,
                    "name": node_name,
                    "line": data.get("line"),
                })
        
        if not matches:
            return f"No definition found for symbol: {symbol_name}", {"symbols_found": []}
        
        result_lines = [f"Found {len(matches)} definition(s) for '{symbol_name}':"]
        result_lines.append("")
        
        anchor_candidates = []
        for match in matches:
            result_lines.append(f"  [{match['type']}] {match['node_id']}")
            result_lines.append(f"    📁 File: {match['file']}")
            if match.get('line'):
                result_lines.append(f"    📍 Line: {match['line']}")
            result_lines.append("")
            
            anchor_candidates.append({
                "anchor_type": "definition",
                "symbol_name": match['name'],
                "file_path": match['file'],
                "line_number": match.get('line'),
            })
        
        metadata = {
            "relation_type": "definition",
            "symbols_found": [m['name'] for m in matches],
            "primary_file": matches[0]['file'] if matches else None,
            "anchor_candidates": anchor_candidates,
        }
        
        return "\n".join(result_lines), metadata
    
    def _find_callers(self, symbol_name: str, file_path: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """查找调用某符号的位置"""
        if not symbol_name:
            return "Error: symbol_name is required", {"error": "missing_symbol"}
        
        target_nodes = [
            node_id for node_id, data in self.graph.nodes(data=True)
            if data.get("name") == symbol_name or node_id.endswith(f":{symbol_name}")
        ]
        
        if not target_nodes:
            return f"Symbol not found in graph: {symbol_name}", {"symbols_found": []}
        
        callers = []
        for target in target_nodes:
            for pred in self.graph.predecessors(target):
                edge_data = self.graph.get_edge_data(pred, target)
                if edge_data and edge_data.get("type") == "calls":
                    pred_data = self.graph.nodes[pred]
                    if file_path and file_path not in pred_data.get("file", ""):
                        continue
                    callers.append({
                        "caller": pred,
                        "caller_name": pred_data.get("name", pred),
                        "file": pred_data.get("file", "unknown"),
                        "type": pred_data.get("type", "unknown"),
                        "line": pred_data.get("line"),
                    })
        
        if not callers:
            return f"No callers found for: {symbol_name}", {"relation_type": "calls", "symbols_found": []}
        
        result_lines = [f"Found {len(callers)} caller(s) of '{symbol_name}':"]
        result_lines.append("")
        
        for caller in callers:
            result_lines.append(f"  [{caller['type']}] {caller['caller_name']}")
            result_lines.append(f"    📁 File: {caller['file']}")
            result_lines.append(f"    → calls → {symbol_name}")
            result_lines.append("")
        
        metadata = {
            "relation_type": "calls",
            "symbols_found": [c['caller_name'] for c in callers],
            "primary_file": callers[0]['file'] if callers else None,
            "anchor_candidates": [
                {
                    "anchor_type": "reference",
                    "symbol_name": c['caller_name'],
                    "file_path": c['file'],
                    "line_number": c.get('line'),
                }
                for c in callers[:5]
            ],
        }
        
        return "\n".join(result_lines), metadata
    
    def _find_callees(self, symbol_name: str, file_path: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
        """查找某符号调用的其他符号"""
        if not symbol_name:
            return "Error: symbol_name is required", {"error": "missing_symbol"}
        
        source_nodes = [
            node_id for node_id, data in self.graph.nodes(data=True)
            if data.get("name") == symbol_name or node_id.endswith(f":{symbol_name}")
        ]
        
        if not source_nodes:
            return f"Symbol not found in graph: {symbol_name}", {"symbols_found": []}
        
        callees = []
        for source in source_nodes:
            source_data = self.graph.nodes[source]
            for succ in self.graph.successors(source):
                edge_data = self.graph.get_edge_data(source, succ)
                if edge_data and edge_data.get("type") == "calls":
                    succ_data = self.graph.nodes[succ]
                    callees.append({
                        "callee": succ,
                        "callee_name": succ_data.get("name", succ),
                        "file": succ_data.get("file", "unknown"),
                        "type": succ_data.get("type", "unknown"),
                        "line": succ_data.get("line"),
                        "source_file": source_data.get("file", "unknown"),
                    })
        
        if not callees:
            return f"No callees found for: {symbol_name}", {"relation_type": "calls", "symbols_found": []}
        
        result_lines = [f"'{symbol_name}' calls {len(callees)} function(s):"]
        result_lines.append("")
        
        for callee in callees:
            result_lines.append(f"  {symbol_name} → calls → [{callee['type']}] {callee['callee_name']}")
            result_lines.append(f"    📁 Defined in: {callee['file']}")
            result_lines.append("")
        
        metadata = {
            "relation_type": "calls",
            "symbols_found": [c['callee_name'] for c in callees],
            "primary_file": callees[0]['source_file'] if callees else None,
            "anchor_candidates": [
                {
                    "anchor_type": "reference",
                    "symbol_name": c['callee_name'],
                    "file_path": c['file'],
                    "line_number": c.get('line'),
                }
                for c in callees[:5]
            ],
        }
        
        return "\n".join(result_lines), metadata
    
    def _get_class_hierarchy(self, class_name: str) -> Tuple[str, Dict[str, Any]]:
        """获取类的继承层次"""
        if not class_name:
            return "Error: class_name is required", {"error": "missing_symbol"}
        
        class_nodes = [
            (node_id, data) for node_id, data in self.graph.nodes(data=True)
            if data.get("type") == "class" and 
            (data.get("name") == class_name or node_id.endswith(f":{class_name}"))
        ]
        
        if not class_nodes:
            return f"Class not found: {class_name}", {"symbols_found": []}
        
        result_lines = [f"Class hierarchy for '{class_name}':"]
        result_lines.append("")
        
        symbols_found = []
        anchor_candidates = []
        
        for node_id, data in class_nodes:
            result_lines.append(f"📦 Class: {data.get('name', node_id)}")
            result_lines.append(f"  📁 File: {data.get('file', 'unknown')}")
            
            symbols_found.append(data.get('name', node_id))
            anchor_candidates.append({
                "anchor_type": "definition",
                "symbol_name": data.get('name', node_id),
                "file_path": data.get('file', 'unknown'),
            })
            
            # 父类
            parents = []
            for pred in self.graph.predecessors(node_id):
                edge_data = self.graph.get_edge_data(pred, node_id)
                if edge_data and edge_data.get("type") == "inherits":
                    pred_data = self.graph.nodes[pred]
                    parents.append({
                        "name": pred_data.get("name", pred),
                        "file": pred_data.get("file", "unknown"),
                    })
            
            if parents:
                result_lines.append(f"  ⬆️ Extends:")
                for p in parents:
                    result_lines.append(f"    - {p['name']} ({p['file']})")
            
            # 子类
            children = []
            for succ in self.graph.successors(node_id):
                edge_data = self.graph.get_edge_data(node_id, succ)
                if edge_data and edge_data.get("type") == "inherits":
                    succ_data = self.graph.nodes[succ]
                    children.append({
                        "name": succ_data.get("name", succ),
                        "file": succ_data.get("file", "unknown"),
                    })
            
            if children:
                result_lines.append(f"  ⬇️ Subclasses:")
                for c in children:
                    result_lines.append(f"    - {c['name']} ({c['file']})")
            
            # 方法
            methods = []
            for succ in self.graph.successors(node_id):
                edge_data = self.graph.get_edge_data(node_id, succ)
                if edge_data and edge_data.get("type") == "contains":
                    succ_data = self.graph.nodes[succ]
                    if succ_data.get("type") == "function":
                        methods.append(succ_data.get("name", succ))
            
            if methods:
                result_lines.append(f"  🔧 Methods: {', '.join(methods[:10])}")
                if len(methods) > 10:
                    result_lines.append(f"      ... and {len(methods) - 10} more")
            
            result_lines.append("")
        
        metadata = {
            "relation_type": "inherits",
            "symbols_found": symbols_found,
            "primary_file": class_nodes[0][1].get('file') if class_nodes else None,
            "anchor_candidates": anchor_candidates,
        }
        
        return "\n".join(result_lines), metadata
    
    def _get_file_symbols(self, file_path: str) -> Tuple[str, Dict[str, Any]]:
        """获取文件中定义的所有符号"""
        if not file_path:
            return "Error: file_path is required", {"error": "missing_file_path"}
        
        symbols = []
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if file_path in node_file or node_file.endswith(file_path):
                symbols.append({
                    "name": data.get("name", node_id),
                    "type": data.get("type", "unknown"),
                    "node_id": node_id,
                    "line": data.get("line"),
                })
        
        if not symbols:
            return f"No symbols found in file: {file_path}", {"symbols_found": []}
        
        classes = [s for s in symbols if s["type"] == "class"]
        functions = [s for s in symbols if s["type"] == "function"]
        others = [s for s in symbols if s["type"] not in ("class", "function", "file")]
        
        result_lines = [f"Symbols in '{file_path}':"]
        result_lines.append("")
        
        if classes:
            result_lines.append(f"📦 Classes ({len(classes)}):")
            for c in classes:
                line_info = f" [line {c['line']}]" if c.get('line') else ""
                result_lines.append(f"  - {c['name']}{line_info}")
        
        if functions:
            result_lines.append(f"\n🔧 Functions ({len(functions)}):")
            for f in functions[:20]:
                line_info = f" [line {f['line']}]" if f.get('line') else ""
                result_lines.append(f"  - {f['name']}{line_info}")
            if len(functions) > 20:
                result_lines.append(f"  ... and {len(functions) - 20} more")
        
        if others:
            result_lines.append(f"\n📋 Other ({len(others)}):")
            for o in others[:10]:
                result_lines.append(f"  - [{o['type']}] {o['name']}")
        
        metadata = {
            "relation_type": "contains",
            "symbols_found": [s['name'] for s in symbols],
            "primary_file": file_path,
            "anchor_candidates": [
                {
                    "anchor_type": "definition",
                    "symbol_name": s['name'],
                    "file_path": file_path,
                    "line_number": s.get('line'),
                }
                for s in (classes + functions)[:10]
            ],
        }
        
        return "\n".join(result_lines), metadata
    
    def _get_all_symbols(self, limit: int = 50) -> Tuple[str, Dict[str, Any]]:
        """获取图谱中所有主要符号的概览"""
        classes = []
        functions = []
        files = []
        
        for node_id, data in self.graph.nodes(data=True):
            node_type = data.get("type", "")
            if node_type == "class":
                classes.append({
                    "name": data.get("name", node_id),
                    "file": data.get("file", "unknown"),
                })
            elif node_type == "function":
                functions.append({
                    "name": data.get("name", node_id),
                    "file": data.get("file", "unknown"),
                })
            elif node_type == "file":
                files.append(node_id)
        
        result_lines = [
            "📊 Code Graph Overview:",
            f"  Total nodes: {self.graph.number_of_nodes()}",
            f"  Total edges: {self.graph.number_of_edges()}",
            f"  Files: {len(files)}",
            f"  Classes: {len(classes)}",
            f"  Functions: {len(functions)}",
            "",
        ]
        
        if classes:
            result_lines.append("📦 Key Classes:")
            for c in classes[:15]:
                result_lines.append(f"  - {c['name']} ({c['file']})")
            if len(classes) > 15:
                result_lines.append(f"  ... and {len(classes) - 15} more")
        
        if functions:
            result_lines.append("\n🔧 Key Functions:")
            for f in functions[:15]:
                result_lines.append(f"  - {f['name']} ({f['file']})")
            if len(functions) > 15:
                result_lines.append(f"  ... and {len(functions) - 15} more")
        
        metadata = {
            "relation_type": "overview",
            "symbols_found": [c['name'] for c in classes[:10]] + [f['name'] for f in functions[:10]],
            "anchor_candidates": [],
        }
        
        return "\n".join(result_lines), metadata
    
    def _find_imports(self, file_path: str) -> Tuple[str, Dict[str, Any]]:
        """查找文件的导入关系"""
        if not file_path:
            return "Error: file_path is required", {"error": "missing_file_path"}
        
        imports = []
        imported_by = []
        
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if file_path in node_file or node_file.endswith(file_path):
                # 该文件导入的
                for succ in self.graph.successors(node_id):
                    edge_data = self.graph.get_edge_data(node_id, succ)
                    if edge_data and edge_data.get("type") == "imports":
                        succ_data = self.graph.nodes[succ]
                        imports.append({
                            "name": succ_data.get("name", succ),
                            "file": succ_data.get("file", "unknown"),
                        })
                
                # 被其他文件导入的
                for pred in self.graph.predecessors(node_id):
                    edge_data = self.graph.get_edge_data(pred, node_id)
                    if edge_data and edge_data.get("type") == "imports":
                        pred_data = self.graph.nodes[pred]
                        imported_by.append({
                            "name": pred_data.get("name", pred),
                            "file": pred_data.get("file", "unknown"),
                        })
        
        result_lines = [f"Import relationships for '{file_path}':"]
        result_lines.append("")
        
        if imports:
            result_lines.append("⬇️ Imports:")
            for imp in imports[:20]:
                result_lines.append(f"  - {imp['name']} from {imp['file']}")
        
        if imported_by:
            result_lines.append("\n⬆️ Imported by:")
            for imp in imported_by[:20]:
                result_lines.append(f"  - {imp['name']} in {imp['file']}")
        
        if not imports and not imported_by:
            result_lines.append("No import relationships found.")
        
        metadata = {
            "relation_type": "imports",
            "symbols_found": [i['name'] for i in imports + imported_by],
            "primary_file": file_path,
        }
        
        return "\n".join(result_lines), metadata
    
    def _get_module_dependencies(self, module_path: str) -> Tuple[str, Dict[str, Any]]:
        """获取模块级依赖关系"""
        if not module_path:
            return "Error: module_path is required", {"error": "missing_module_path"}
        
        # 找到模块内的所有文件
        module_files = []
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if module_path in node_file:
                module_files.append(node_file)
        
        module_files = list(set(module_files))
        
        # 找出依赖
        external_deps = set()
        internal_deps = set()
        
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if module_path not in node_file:
                continue
            
            for succ in self.graph.successors(node_id):
                edge_data = self.graph.get_edge_data(node_id, succ)
                if edge_data and edge_data.get("type") in ["imports", "calls"]:
                    succ_data = self.graph.nodes[succ]
                    succ_file = succ_data.get("file", "")
                    
                    if module_path in succ_file:
                        internal_deps.add(succ_file)
                    elif succ_file:
                        external_deps.add(succ_file)
        
        result_lines = [f"Module dependencies for '{module_path}':"]
        result_lines.append(f"  Files in module: {len(module_files)}")
        result_lines.append("")
        
        if internal_deps:
            result_lines.append("🔄 Internal dependencies:")
            for dep in sorted(internal_deps)[:15]:
                result_lines.append(f"  - {dep}")
        
        if external_deps:
            result_lines.append("\n📦 External dependencies:")
            for dep in sorted(external_deps)[:15]:
                result_lines.append(f"  - {dep}")
        
        metadata = {
            "relation_type": "module_dependency",
            "symbols_found": list(internal_deps | external_deps)[:20],
            "primary_file": module_path,
        }
        
        return "\n".join(result_lines), metadata
    
    def is_loaded(self) -> bool:
        """检查图谱是否已加载"""
        return self.graph.number_of_nodes() > 0
