import os
import json
import fnmatch

def load_config(config_path: str) -> dict:
    """
    加载配置文件
    """

    print(f"Loading configuration from: {config_path}")

    with open(config_path, 'r') as f:
        return json.load(f)

def is_binary(file_path: str) -> bool:
    """简单检查文件是否为二进制"""
    try:
        with open(file_path, 'rb') as f:
            # 读取文件的一小部分来判断
            chunk = f.read(1024)
            return b'\x00' in chunk
    except IOError:
        return True

def find_relevant_files(repo_path: str, config: dict) -> list[str]:
    """
    遍历仓库，根据配置过滤文件。
    """
    relevant_files = []
    include_patterns = config.get("include_patterns", [])
    ignore_patterns = config.get("ignore_patterns", [])

    for root, dirs, files in os.walk(repo_path):
        # 过滤掉需要忽略的目录
        # 注意：这里需要修改dirs列表来阻止os.walk进入这些目录
        dirs[:] = [d for d in dirs if not any(
            fnmatch.fnmatch(os.path.join(root, d), pattern) for pattern in ignore_patterns
        )]
        
        for filename in files:
            file_path = os.path.join(root, filename)
            
            # 1. 检查是否匹配忽略规则
            if any(fnmatch.fnmatch(file_path, pattern) for pattern in ignore_patterns):
                continue

            # 2. 检查是否匹配包含规则 (如果include_patterns存在)
            if include_patterns and not any(fnmatch.fnmatch(filename, pattern) for pattern in include_patterns):
                continue

            # 3. 检查是否为二进制文件
            if is_binary(file_path):
                continue
            
            relevant_files.append(file_path)
            
    print(f"Found {len(relevant_files)} relevant files.")
    return relevant_files

def generate_file_tree(repo_path: str, config_path: str) -> str:
    """
    为相关文件生成一个文本格式的目录树。
    """
    print("Generating file tree...")
    config = load_config(config_path)
    # 我们只关心筛选后的文件
    relevant_files = find_relevant_files(repo_path, config)
    
    # 将绝对路径转换为相对于仓库根目录的路径
    relative_files = [os.path.relpath(p, repo_path) for p in relevant_files]
    
    tree = {}
    for path in sorted(relative_files):
        parts = path.split(os.sep)
        current_level = tree
        for part in parts:
            if part not in current_level:
                current_level[part] = {}
            current_level = current_level[part]
            
    def build_tree_string(d, indent=''):
        s = ''
        items = sorted(d.items())
        for i, (key, value) in enumerate(items):
            connector = '└── ' if i == len(items) - 1 else '├── '
            s += indent + connector + key + '\n'
            if value:
                new_indent = indent + ('    ' if i == len(items) - 1 else '│   ')
                s += build_tree_string(value, new_indent)
        return s

    tree_string = f".\n{build_tree_string(tree)}"
    print("File tree generated.")
    return tree_string
