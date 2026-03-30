import sys
sys.path.insert(0, '.')
from src.ingestion.ts_parser import TreeSitterParser
p = TreeSitterParser()
# Test TS interface
chunks = p.parse_code("interface User { id: number; name: string; }", ".ts")
for c in chunks:
    print(f"type={c.node_type} name={c.name}")

# Check AST
parser = p.get_parser('typescript')
tree = parser.parse(b"interface User { id: number; name: string; }")
def show(node, depth=0):
    indent = "  " * depth
    name_str = repr(node.type)
    print(f"{indent}{name_str} [{node.start_byte}:{node.end_byte}]")
    for child in node.children:
        show(child, depth+1)
show(tree.root_node)
