import networkx as nx
from tree_sitter import Parser, Node
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
import json

from src.ingestion.ts_parser import TreeSitterParser

class CodeGraphBuilder:
    """
    使用 Tree-sitter 和 NetworkX 构建代码知识图谱。
    """
    
    # 映射文件扩展名到 tree-sitter 语言名称
    EXTENSION_TO_LANGUAGE = TreeSitterParser.EXTENSION_TO_LANGUAGE

    def __init__(self):
        self.graph = nx.DiGraph()
        self.ts_parser = TreeSitterParser()

    def get_parser(self, language_name: str) -> Parser:
        return self.ts_parser.get_parser(language_name)

    def build_graph(self, repo_root: str, file_paths: List[str]):
        """
        构建完整的代码库图谱。
        """
        repo_root_path = Path(repo_root)
        
        # 第一阶段：提取所有实体（节点）
        for file_path in file_paths:
            rel_path = str(Path(file_path).relative_to(repo_root_path))
            extension = Path(file_path).suffix.lower()
            lang_name = self.EXTENSION_TO_LANGUAGE.get(extension)
            
            if not lang_name:
                continue
                
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            self._extract_nodes(content, rel_path, lang_name)
            
        # 第二阶段：提取实体间的关系（边）
        for file_path in file_paths:
            rel_path = str(Path(file_path).relative_to(repo_root_path))
            extension = Path(file_path).suffix.lower()
            lang_name = self.EXTENSION_TO_LANGUAGE.get(extension)
            
            if not lang_name:
                continue
                
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            self._extract_edges(content, rel_path, lang_name)

        return self.graph

    def _extract_nodes(self, code: str, rel_path: str, lang_name: str):
        """
        提取文件、类、函数节点。
        """
        # 添加文件节点
        self.graph.add_node(rel_path, type="file", label=rel_path)
        
        parser = self.get_parser(lang_name)
        tree = parser.parse(bytes(code, "utf8"))
        
        def traverse(node: Node):
            node_type = node.type
            # 简单的名称提取逻辑
            name = None
            if node_type in ["function_definition", "class_definition", "function_declaration", "class_declaration"]:
                for child in node.children:
                    if child.type == "identifier":
                        name = code[child.start_byte:child.end_byte]
                        break
                
                if name:
                    node_id = f"{rel_path}:{name}"
                    self.graph.add_node(node_id, 
                                       type="class" if "class" in node_type else "function",
                                       name=name,
                                       file=rel_path,
                                       label=name)
                    # 建立文件与实体的隶属关系
                    self.graph.add_edge(rel_path, node_id, type="contains")
            
            for child in node.children:
                traverse(child)
                
        traverse(tree.root_node)

    def _extract_edges(self, code: str, rel_path: str, lang_name: str):
        """
        提取调用和引用关系。
        注：这是一个简化版，不包含完整的语义解析（如 LSP）。
        """
        parser = self.get_parser(lang_name)
        tree = parser.parse(bytes(code, "utf8"))
        
        # 记录当前所在的上下文（函数或类）
        self.current_context = rel_path
        
        def traverse(node: Node):
            old_context = self.current_context
            
            # 更新上下文
            if node.type in ["function_definition", "class_definition", "function_declaration", "class_declaration"]:
                name = None
                for child in node.children:
                    if child.type == "identifier":
                        name = code[child.start_byte:child.end_byte]
                        break
                if name:
                    self.current_context = f"{rel_path}:{name}"

            # 提取调用 (call)
            if node.type == "call" or node.type == "call_expression":
                # 寻找被调用的标识符
                call_name = None
                # 在 Python 中，call 节点通常有一个 'function' 子节点
                # 在 JS 中，call_expression 有一个 'function' 或直接的标识符
                for child in node.children:
                    if child.type in ["identifier", "attribute", "member_expression"]:
                        # 简化处理：只取最后的标识符
                        full_call = code[child.start_byte:child.end_byte]
                        call_name = full_call.split('.')[-1]
                        break
                
                if call_name:
                    # 尝试寻找匹配的函数节点（这里会有歧义，同名函数可能属于不同文件）
                    # 这是一个启发式搜索：寻找同名的函数节点
                    potential_targets = [n for n, d in self.graph.nodes(data=True) 
                                        if d.get("type") == "function" and d.get("name") == call_name]
                    
                    for target in potential_targets:
                        # 避免自环
                        if self.current_context != target:
                            self.graph.add_edge(self.current_context, target, type="calls")

            # 提取导入 (import) - 简化处理
            if node.type in ["import_statement", "import_from_statement", "import_declaration"]:
                # 寻找导入的文件或模块
                # 这里需要复杂的路径解析，暂时只记录存在导入关系
                pass

            for child in node.children:
                traverse(child)
            
            self.current_context = old_context
                
        traverse(tree.root_node)

    def save_graph(self, output_path: str):
        data = nx.node_link_data(self.graph)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_graph(self, input_path: str):
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.graph = nx.node_link_graph(data)
