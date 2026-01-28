import tree_sitter
from tree_sitter import Parser, Node
from typing import List, Dict, Any, Optional
from pathlib import Path

# 尝试导入更现代且兼容 tree-sitter 0.22+ 的语言包
try:
    import tree_sitter_language_pack as tslp
    HAS_TSLP = True
except ImportError:
    HAS_TSLP = False

# 尝试导入旧版的 tree-sitter-languages
try:
    import tree_sitter_languages
    HAS_TS_LANGS = True
except ImportError:
    HAS_TS_LANGS = False

class CodeChunk:
    def __init__(self, content: str, start_line: int, end_line: int, node_type: str, name: Optional[str] = None):
        self.content = content
        self.start_line = start_line
        self.end_line = end_line
        self.node_type = node_type
        self.name = name

class TreeSitterParser:
    """
    使用 Tree-sitter 对代码进行 AST 解析和切片。
    """
    
    # 映射文件扩展名到 tree-sitter 语言名称
    EXTENSION_TO_LANGUAGE = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".go": "go",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".rb": "ruby",
        ".rs": "rust",
    }

    def __init__(self):
        self.parsers = {}

    def get_parser(self, language_name: str) -> Parser:
        if language_name not in self.parsers:
            lang = None
            
            # 优先使用 tree-sitter-language-pack (兼容性更好)
            if HAS_TSLP:
                try:
                    lang = tslp.get_language(language_name)
                except Exception:
                    pass
            
            # 如果失败，尝试使用 tree-sitter-languages
            if lang is None and HAS_TS_LANGS:
                try:
                    lang = tree_sitter_languages.get_language(language_name)
                except Exception:
                    # 如果 tree-sitter-languages 报错 "__init__() takes exactly 1 argument (2 given)"
                    # 这是因为它与新版 tree-sitter 0.22+ 不兼容
                    pass
            
            if lang is None:
                raise ValueError(f"Could not load tree-sitter language: {language_name}")

            # 兼容 tree-sitter 0.22+ 和旧版本
            try:
                # 新版 API: Parser(language)
                parser = Parser(lang)
            except TypeError:
                # 旧版 API
                parser = Parser()
                parser.set_language(lang)
                
            self.parsers[language_name] = parser
            
        return self.parsers[language_name]

    def parse_code(self, code: str, extension: str) -> List[CodeChunk]:
        language_name = self.EXTENSION_TO_LANGUAGE.get(extension.lower())
        if not language_name:
            return []

        parser = self.get_parser(language_name)
        tree = parser.parse(bytes(code, "utf8"))
        
        chunks = []
        self._extract_chunks(tree.root_node, code, chunks, language_name)
        
        # 如果没有提取到任何块（例如文件太简单），则返回整个文件作为一个块
        if not chunks:
            chunks.append(CodeChunk(
                content=code,
                start_line=1,
                end_line=len(code.splitlines()),
                node_type="file"
            ))
            
        return chunks

    def _extract_chunks(self, node: Node, code: str, chunks: List[CodeChunk], language_name: str):
        """
        递归提取有意义的代码块（函数、类等）。
        """
        # 定义不同语言中有意义的节点类型
        interesting_types = {
            "python": ["function_definition", "class_definition"],
            "javascript": ["function_declaration", "class_declaration", "method_definition", "arrow_function"],
            "typescript": ["function_declaration", "class_declaration", "method_definition", "interface_declaration", "type_alias_declaration"],
            "tsx": ["function_declaration", "class_declaration", "method_definition", "interface_declaration", "type_alias_declaration"],
        }
        
        types = interesting_types.get(language_name, ["function_definition", "class_definition"])
        
        if node.type in types:
            start_byte = node.start_byte
            end_byte = node.end_byte
            content = code[start_byte:end_byte]
            
            # 提取名称（如果存在）
            name = None
            for child in node.children:
                if child.type == "identifier":
                    name = code[child.start_byte:child.end_byte]
                    break
            
            chunks.append(CodeChunk(
                content=content,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                node_type=node.type,
                name=name
            ))
            
            # 对于类，我们可能还想继续递归处理其内部的方法，但为了避免碎片化，
            # 这里的策略是：如果是一个大单元，我们就作为一个整体，或者可以根据长度决定是否细分。
            # 目前先提取顶层定义。
            return

        for child in node.children:
            self._extract_chunks(child, code, chunks, language_name)
