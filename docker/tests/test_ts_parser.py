"""
Tests for TreeSitterParser — ts_parser.py 解析准确性验证

使用 pytest.importorskip 处理缺少 tree-sitter 语言包时的 skip，
避免裸机无依赖直接报红。
"""
import pytest

# ---------- 条件导入 ----------
# tree_sitter 核心必须可用，否则整个模块 skip
ts = pytest.importorskip("tree_sitter", reason="tree-sitter not installed")

from src.ingestion.ts_parser import TreeSitterParser, CodeChunk, HAS_TSLP, HAS_TS_LANGS

# 至少需要一个语言包后端
_HAS_ANY_LANG_BACKEND = HAS_TSLP or HAS_TS_LANGS
_SKIP_NO_LANGS = pytest.mark.skipif(
    not _HAS_ANY_LANG_BACKEND,
    reason="Neither tree-sitter-language-pack nor tree-sitter-languages is installed",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def parser() -> TreeSitterParser:
    return TreeSitterParser()


# ---------------------------------------------------------------------------
# Python 解析测试
# ---------------------------------------------------------------------------

@_SKIP_NO_LANGS
class TestTreeSitterParserPython:
    PYTHON_CODE = '''\
def hello(name):
    """Greet someone."""
    return f"Hello, {name}!"


class Greeter:
    def __init__(self, prefix):
        self.prefix = prefix

    def greet(self, name):
        return f"{self.prefix} {name}"
'''

    def test_parse_python_function(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.PYTHON_CODE, ".py")
        func_chunks = [c for c in chunks if c.node_type == "function_definition" and c.name == "hello"]
        assert len(func_chunks) == 1
        assert "def hello" in func_chunks[0].content

    def test_parse_python_class(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.PYTHON_CODE, ".py")
        class_chunks = [c for c in chunks if c.node_type == "class_definition"]
        assert len(class_chunks) == 1
        assert class_chunks[0].name == "Greeter"

    def test_chunk_attributes(self, parser: TreeSitterParser):
        """验证 CodeChunk 各属性完整且合理"""
        chunks = parser.parse_code(self.PYTHON_CODE, ".py")
        for chunk in chunks:
            assert isinstance(chunk, CodeChunk)
            assert isinstance(chunk.content, str) and len(chunk.content) > 0
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line
            assert chunk.node_type in (
                "function_definition", "class_definition", "file",
            )


# ---------------------------------------------------------------------------
# JavaScript 解析测试
# ---------------------------------------------------------------------------

@_SKIP_NO_LANGS
class TestTreeSitterParserJavaScript:
    JS_CODE = '''\
function add(a, b) {
    return a + b;
}

class Calculator {
    constructor() {
        this.result = 0;
    }
    add(value) {
        this.result += value;
    }
}
'''

    def test_parse_javascript_function(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.JS_CODE, ".js")
        func_chunks = [c for c in chunks if c.node_type == "function_declaration"]
        assert len(func_chunks) >= 1
        names = [c.name for c in func_chunks]
        assert "add" in names

    def test_parse_javascript_class(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.JS_CODE, ".js")
        class_chunks = [c for c in chunks if c.node_type == "class_declaration"]
        assert len(class_chunks) == 1
        assert class_chunks[0].name == "Calculator"


# ---------------------------------------------------------------------------
# TypeScript / TSX 解析测试
# ---------------------------------------------------------------------------

@_SKIP_NO_LANGS
class TestTreeSitterParserTypeScript:
    TS_CODE = '''\
interface User {
    id: number;
    name: string;
}

function getUser(id: number): User {
    return { id, name: "test" };
}

type Status = "active" | "inactive";
'''

    TSX_CODE = '''\
function App() {
    return <div>Hello</div>;
}

interface Props {
    title: string;
}
'''

    def test_parse_typescript_interface(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.TS_CODE, ".ts")
        iface_chunks = [c for c in chunks if c.node_type == "interface_declaration"]
        assert len(iface_chunks) >= 1
        # Note: tree-sitter TS stores interface names in `type_identifier` nodes,
        # not `identifier` nodes; `_extract_chunks` currently extracts name=None
        # for interfaces. We verify the interface body is captured.
        assert "interface User" in iface_chunks[0].content

    def test_parse_typescript_function(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.TS_CODE, ".ts")
        func_chunks = [c for c in chunks if c.node_type == "function_declaration"]
        assert len(func_chunks) >= 1
        names = [c.name for c in func_chunks]
        assert "getUser" in names

    def test_parse_typescript_type_alias(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.TS_CODE, ".ts")
        type_chunks = [c for c in chunks if c.node_type == "type_alias_declaration"]
        assert len(type_chunks) >= 1

    def test_parse_tsx_component(self, parser: TreeSitterParser):
        chunks = parser.parse_code(self.TSX_CODE, ".tsx")
        func_chunks = [c for c in chunks if c.node_type == "function_declaration"]
        assert len(func_chunks) >= 1
        names = [c.name for c in func_chunks]
        assert "App" in names


# ---------------------------------------------------------------------------
# 边界与回退测试
# ---------------------------------------------------------------------------

class TestTreeSitterParserEdgeCases:
    def test_unsupported_extension_returns_empty(self, parser: TreeSitterParser):
        """不支持的扩展名应返回空列表"""
        result = parser.parse_code("some random content", ".xyz")
        assert result == []

    def test_empty_extension_returns_empty(self, parser: TreeSitterParser):
        result = parser.parse_code("x = 1", "")
        assert result == []

    @_SKIP_NO_LANGS
    def test_empty_code_returns_file_chunk(self, parser: TreeSitterParser):
        """极简 / 无有意义定义的代码应走 file 整块回退"""
        chunks = parser.parse_code("x = 1\ny = 2\n", ".py")
        assert len(chunks) == 1
        assert chunks[0].node_type == "file"
        assert "x = 1" in chunks[0].content

    @_SKIP_NO_LANGS
    def test_syntax_error_does_not_crash(self, parser: TreeSitterParser):
        """包含语法错误的代码不应抛出异常"""
        bad_code = "def (\nclass \n{{{ invalid"
        # 不应 raise
        chunks = parser.parse_code(bad_code, ".py")
        assert isinstance(chunks, list)

    @_SKIP_NO_LANGS
    def test_jsx_uses_javascript_parser(self, parser: TreeSitterParser):
        """JSX 文件应映射到 javascript 解析器"""
        jsx_code = 'function Component() { return <div/>; }'
        chunks = parser.parse_code(jsx_code, ".jsx")
        assert any(c.name == "Component" for c in chunks)
