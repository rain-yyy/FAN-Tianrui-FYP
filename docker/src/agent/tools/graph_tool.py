"""
代码知识图谱查询工具

基于已构建的 NetworkX 图谱，提供以下查询能力：
- find_definition: 查找符号定义位置
- find_callers: 查找调用某函数的位置
- find_callees: 查找某函数调用的其他函数
- get_class_hierarchy: 获取类的继承关系
- get_file_symbols: 获取文件中定义的所有符号
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Literal

import networkx as nx

from src.agent.state import ContextPiece, ToolType

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
        "get_all_symbols"
    ]
    
    def __init__(self, graph_path: Optional[str] = None):
        """
        初始化图谱工具
        
        Args:
            graph_path: 图谱 JSON 文件路径，如果不提供则创建空图
        """
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
        
        Args:
            operation: 操作类型
            symbol_name: 符号名称（函数名、类名等）
            file_path: 文件路径过滤
            
        Returns:
            ContextPiece: 包含查询结果的上下文片段
        """
        try:
            if operation == "find_definition":
                result = self._find_definition(symbol_name, file_path)
            elif operation == "find_callers":
                result = self._find_callers(symbol_name, file_path)
            elif operation == "find_callees":
                result = self._find_callees(symbol_name, file_path)
            elif operation == "get_class_hierarchy":
                result = self._get_class_hierarchy(symbol_name)
            elif operation == "get_file_symbols":
                result = self._get_file_symbols(file_path)
            elif operation == "get_all_symbols":
                result = self._get_all_symbols()
            else:
                result = f"Unknown operation: {operation}"
            
            return ContextPiece(
                source=f"code_graph.{operation}",
                content=result,
                file_path=file_path,
                relevance_score=0.8,
                metadata={
                    "operation": operation,
                    "symbol_name": symbol_name,
                    "file_path": file_path,
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
    
    def _find_definition(self, symbol_name: str, file_path: Optional[str] = None) -> str:
        """查找符号定义位置"""
        if not symbol_name:
            return "Error: symbol_name is required"
        
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
                })
        
        if not matches:
            return f"No definition found for symbol: {symbol_name}"
        
        result_lines = [f"Found {len(matches)} definition(s) for '{symbol_name}':"]
        for match in matches:
            result_lines.append(f"  - [{match['type']}] {match['node_id']} in {match['file']}")
        
        return "\n".join(result_lines)
    
    def _find_callers(self, symbol_name: str, file_path: Optional[str] = None) -> str:
        """查找调用某符号的位置"""
        if not symbol_name:
            return "Error: symbol_name is required"
        
        target_nodes = [
            node_id for node_id, data in self.graph.nodes(data=True)
            if data.get("name") == symbol_name or node_id.endswith(f":{symbol_name}")
        ]
        
        if not target_nodes:
            return f"Symbol not found in graph: {symbol_name}"
        
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
                        "file": pred_data.get("file", "unknown"),
                        "type": pred_data.get("type", "unknown"),
                    })
        
        if not callers:
            return f"No callers found for: {symbol_name}"
        
        result_lines = [f"Found {len(callers)} caller(s) of '{symbol_name}':"]
        for caller in callers:
            result_lines.append(f"  - {caller['caller']} ({caller['type']}) in {caller['file']}")
        
        return "\n".join(result_lines)
    
    def _find_callees(self, symbol_name: str, file_path: Optional[str] = None) -> str:
        """查找某符号调用的其他符号"""
        if not symbol_name:
            return "Error: symbol_name is required"
        
        source_nodes = [
            node_id for node_id, data in self.graph.nodes(data=True)
            if data.get("name") == symbol_name or node_id.endswith(f":{symbol_name}")
        ]
        
        if not source_nodes:
            return f"Symbol not found in graph: {symbol_name}"
        
        callees = []
        for source in source_nodes:
            for succ in self.graph.successors(source):
                edge_data = self.graph.get_edge_data(source, succ)
                if edge_data and edge_data.get("type") == "calls":
                    succ_data = self.graph.nodes[succ]
                    callees.append({
                        "callee": succ,
                        "file": succ_data.get("file", "unknown"),
                        "type": succ_data.get("type", "unknown"),
                        "name": succ_data.get("name", "unknown"),
                    })
        
        if not callees:
            return f"No callees found for: {symbol_name}"
        
        result_lines = [f"'{symbol_name}' calls {len(callees)} function(s):"]
        for callee in callees:
            result_lines.append(f"  - {callee['name']} ({callee['type']}) in {callee['file']}")
        
        return "\n".join(result_lines)
    
    def _get_class_hierarchy(self, class_name: str) -> str:
        """获取类的继承层次"""
        if not class_name:
            return "Error: class_name is required"
        
        class_nodes = [
            (node_id, data) for node_id, data in self.graph.nodes(data=True)
            if data.get("type") == "class" and 
            (data.get("name") == class_name or node_id.endswith(f":{class_name}"))
        ]
        
        if not class_nodes:
            return f"Class not found: {class_name}"
        
        result_lines = [f"Class hierarchy for '{class_name}':"]
        
        for node_id, data in class_nodes:
            result_lines.append(f"\nClass: {node_id}")
            result_lines.append(f"  File: {data.get('file', 'unknown')}")
            
            parents = []
            for pred in self.graph.predecessors(node_id):
                edge_data = self.graph.get_edge_data(pred, node_id)
                if edge_data and edge_data.get("type") == "inherits":
                    parents.append(pred)
            
            if parents:
                result_lines.append(f"  Extends: {', '.join(parents)}")
            
            children = []
            for succ in self.graph.successors(node_id):
                edge_data = self.graph.get_edge_data(node_id, succ)
                if edge_data and edge_data.get("type") == "inherits":
                    children.append(succ)
            
            if children:
                result_lines.append(f"  Subclasses: {', '.join(children)}")
            
            methods = []
            for succ in self.graph.successors(node_id):
                edge_data = self.graph.get_edge_data(node_id, succ)
                if edge_data and edge_data.get("type") == "contains":
                    succ_data = self.graph.nodes[succ]
                    if succ_data.get("type") == "function":
                        methods.append(succ_data.get("name", succ))
            
            if methods:
                result_lines.append(f"  Methods: {', '.join(methods[:10])}")
                if len(methods) > 10:
                    result_lines.append(f"    ... and {len(methods) - 10} more")
        
        return "\n".join(result_lines)
    
    def _get_file_symbols(self, file_path: str) -> str:
        """获取文件中定义的所有符号"""
        if not file_path:
            return "Error: file_path is required"
        
        symbols = []
        for node_id, data in self.graph.nodes(data=True):
            node_file = data.get("file", "")
            if file_path in node_file or node_file.endswith(file_path):
                symbols.append({
                    "name": data.get("name", node_id),
                    "type": data.get("type", "unknown"),
                    "node_id": node_id,
                })
        
        if not symbols:
            return f"No symbols found in file: {file_path}"
        
        classes = [s for s in symbols if s["type"] == "class"]
        functions = [s for s in symbols if s["type"] == "function"]
        others = [s for s in symbols if s["type"] not in ("class", "function", "file")]
        
        result_lines = [f"Symbols in '{file_path}':"]
        
        if classes:
            result_lines.append(f"\nClasses ({len(classes)}):")
            for c in classes:
                result_lines.append(f"  - {c['name']}")
        
        if functions:
            result_lines.append(f"\nFunctions ({len(functions)}):")
            for f in functions[:20]:
                result_lines.append(f"  - {f['name']}")
            if len(functions) > 20:
                result_lines.append(f"  ... and {len(functions) - 20} more")
        
        if others:
            result_lines.append(f"\nOther ({len(others)}):")
            for o in others[:10]:
                result_lines.append(f"  - [{o['type']}] {o['name']}")
        
        return "\n".join(result_lines)
    
    def _get_all_symbols(self, limit: int = 50) -> str:
        """获取图谱中所有主要符号的概览"""
        classes = []
        functions = []
        files = []
        
        for node_id, data in self.graph.nodes(data=True):
            node_type = data.get("type", "")
            if node_type == "class":
                classes.append(data.get("name", node_id))
            elif node_type == "function":
                functions.append(data.get("name", node_id))
            elif node_type == "file":
                files.append(node_id)
        
        result_lines = [
            f"Code Graph Overview:",
            f"  Total nodes: {self.graph.number_of_nodes()}",
            f"  Total edges: {self.graph.number_of_edges()}",
            f"  Files: {len(files)}",
            f"  Classes: {len(classes)}",
            f"  Functions: {len(functions)}",
        ]
        
        if classes:
            result_lines.append(f"\nKey Classes:")
            for c in classes[:15]:
                result_lines.append(f"  - {c}")
            if len(classes) > 15:
                result_lines.append(f"  ... and {len(classes) - 15} more")
        
        if functions:
            result_lines.append(f"\nKey Functions:")
            for f in functions[:15]:
                result_lines.append(f"  - {f}")
            if len(functions) > 15:
                result_lines.append(f"  ... and {len(functions) - 15} more")
        
        return "\n".join(result_lines)
    
    def is_loaded(self) -> bool:
        """检查图谱是否已加载"""
        return self.graph.number_of_nodes() > 0
