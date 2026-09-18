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
    # 与 file_processor.py 的 CODE_SUFFIXES 保持完全一致
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
        ".h": "c",      # C 头文件用 C 语法解析
        ".rb": "ruby",
        ".rs": "rust",
    }

    # 各语言中视为"有意义切块单元"的 AST 节点类型
    # 与 code_graph.py 的 _GENERIC_CLASS_TYPES / _GENERIC_FUNC_TYPES 对齐
    _INTERESTING_TYPES: Dict[str, List[str]] = {
        "python": [
            "function_definition", "async_function_definition", "class_definition",
        ],
        "javascript": [
            "function_declaration", "class_declaration", "method_definition", "arrow_function",
        ],
        "typescript": [
            "function_declaration", "class_declaration", "method_definition",
            "interface_declaration", "type_alias_declaration",
        ],
        "tsx": [
            "function_declaration", "class_declaration", "method_definition",
            "interface_declaration", "type_alias_declaration",
        ],
        # Go: 无 class 概念，方法通过 receiver 与类型关联
        "go": ["function_declaration", "method_declaration"],
        # Java: 以 class/interface/enum 为顶层切块单元
        "java": [
            "class_declaration", "method_declaration", "constructor_declaration",
            "interface_declaration", "enum_declaration",
        ],
        # Ruby: def / class / module 是主要语义单元
        "ruby": ["method", "singleton_method", "class", "module"],
        # Rust: function_item 为当前语法树标准; fn_item 兼容旧版绑定
        "rust": ["function_item", "fn_item", "impl_item"],
        # C: 无 class，只有函数定义
        "c": ["function_definition"],
        # C++: 函数定义 + class/struct 说明符
        "cpp": ["function_definition", "class_specifier", "struct_specifier"],
    }
    _DEFAULT_TYPES: List[str] = ["function_definition", "class_definition"]

    def __init__(self):
        self.parsers = {}

    def get_parser(self, language_name: str) -> Parser:
        if language_name not in self.parsers:
            lang = None

            # 优先使用 tree-sitter-language-pack（兼容 tree-sitter 0.22+）
            if HAS_TSLP:
                try:
                    lang = tslp.get_language(language_name)
                except Exception:
                    pass

            # 回退到 tree-sitter-languages（旧版）
            if lang is None and HAS_TS_LANGS:
                try:
                    lang = tree_sitter_languages.get_language(language_name)
                except Exception:
                    pass

            if lang is None:
                raise ValueError(f"Could not load tree-sitter language: {language_name}")

            # 兼容 tree-sitter 0.22+ 新 API 和旧版 API
            try:
                parser = Parser(lang)
            except TypeError:
                parser = Parser()
                parser.set_language(lang)

            self.parsers[language_name] = parser

        return self.parsers[language_name]

    def parse_code(self, code: str, extension: str) -> List[CodeChunk]:
        language_name = self.EXTENSION_TO_LANGUAGE.get(extension.lower())
        if not language_name:
            return []

        parser = self.get_parser(language_name)

        # Tree-sitter 的 start_byte/end_byte 是 UTF-8 字节偏移，必须对 bytes 做切片
        # 直接对 Python str 做字节索引切片会在多字节字符（中文、emoji 等）时产生乱码
        code_bytes = code.encode("utf-8")
        tree = parser.parse(code_bytes)

        # 一次性查表，避免在每层递归中重复构建字典
        types = self._INTERESTING_TYPES.get(language_name, self._DEFAULT_TYPES)

        chunks: List[CodeChunk] = []
        self._extract_chunks(tree.root_node, code_bytes, chunks, types)

        # 若 AST 未匹配到任何有意义节点（如纯配置/脚本），回退为整文件一个 chunk
        if not chunks:
            chunks.append(CodeChunk(
                content=code,
                start_line=1,
                end_line=len(code.splitlines()),
                node_type="file",
            ))

        return chunks

    def _extract_chunks(
        self,
        node: Node,
        code_bytes: bytes,
        chunks: List[CodeChunk],
        types: List[str],
    ) -> None:
        """
        递归提取有意义的代码块（函数、类等）。

        使用 code_bytes 做字节安全切片，避免多字节字符偏移错误。
        types 由调用方一次性计算后传入，不在每层递归中重建。
        """
        if node.type in types:
            content = code_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

            # 提取节点名称（取第一个 identifier 子节点）
            name: Optional[str] = None
            for child in node.children:
                if child.type == "identifier":
                    name = code_bytes[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                    break

            chunks.append(CodeChunk(
                content=content,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                node_type=node.type,
                name=name,
            ))
            # 不继续递归：以当前节点为完整 chunk 单元，避免产生嵌套重复块
            return

        for child in node.children:
            self._extract_chunks(child, code_bytes, chunks, types)
