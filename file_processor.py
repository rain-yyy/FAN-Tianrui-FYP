import os
import json
import fnmatch
from pathlib import Path

DEFAULT_CODE_EXTENSIONS = {
    "py", "pyi", "ipynb",
    "js", "ts", "tsx", "jsx",
    "java", "kt", "kts", "go",
    "rs", "cpp", "cc", "cxx", "c", "h", "hpp",
    "swift", "rb", "php",
    "cs", "scala",
    "sh", "bash", "zsh",
    "ps1",
    "sql",
    "r", "jl",
}

DEFAULT_TEXT_EXTENSIONS = {
    "md", "mdx", "markdown",
    "rst", "txt", "log",
    "tex",
    "yaml", "yml", "toml", "ini", "cfg",
    "json", "jsonc",
    "csv", "tsv",
    "env", "sample", "example",
    "adoc",
}

SPECIAL_CODE_FILENAMES = {
    "dockerfile",
    "makefile",
    "cmakelists.txt",
    "build.gradle",
    "gradlew",
    "gradlew.bat",
    "package.json",
    "pnpm-workspace.yaml",
    "requirements.txt",
}

SPECIAL_TEXT_FILENAMES = {
    "license",
    "license.txt",
    "changelog",
    "changelog.md",
    "readme",
    "readme.md",
}

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
    relevant_files: list[str] = []
    filters = config.get("file_filters", {})
    excluded_dirs = [
        pattern.strip().lstrip("./")
        for pattern in filters.get("excluded_dirs", [])
        if pattern and pattern.strip()
    ]
    excluded_files = [
        pattern.strip().lstrip("./")
        for pattern in filters.get("excluded_files", [])
        if pattern and pattern.strip()
    ]
    include_patterns = [p for p in config.get("include_patterns", []) if p]

    repo_path = os.path.abspath(repo_path)

    def _matches_any(path_fragment: str, patterns: list[str]) -> bool:
        normalized = path_fragment.replace("\\", "/")
        for pattern in patterns:
            if fnmatch.fnmatch(normalized, pattern):
                return True
        return False

    for root, dirs, files in os.walk(repo_path):
        rel_root = os.path.relpath(root, repo_path).replace("\\", "/")
        if rel_root == ".":
            rel_root = ""

        dirs[:] = [
            d
            for d in dirs
            if not _matches_any(
                os.path.join(rel_root, d).replace("\\", "/").lstrip("./"),
                excluded_dirs,
            )
            and not _matches_any(d, excluded_dirs)
        ]

        for filename in files:
            file_path = os.path.join(root, filename)
            rel_file_path = os.path.relpath(file_path, repo_path).replace("\\", "/")

            if _matches_any(rel_file_path, excluded_files) or _matches_any(
                filename, excluded_files
            ):
                continue

            if include_patterns and not any(
                fnmatch.fnmatch(filename, pattern) for pattern in include_patterns
            ):
                continue

            if is_binary(file_path):
                continue

            relevant_files.append(file_path)

    print(f"Found {len(relevant_files)} relevant files.")
    return relevant_files


def split_code_and_text_files(
    file_paths: list[str], config: dict
) -> tuple[list[str], list[str]]:
    categories = config.get("file_categories", {})

    code_exts = {
        ext.lower().lstrip(".")
        for ext in categories.get("code_extensions", DEFAULT_CODE_EXTENSIONS)
    }
    text_exts = {
        ext.lower().lstrip(".")
        for ext in categories.get("text_extensions", DEFAULT_TEXT_EXTENSIONS)
    }

    code_files: list[str] = []
    text_files: list[str] = []

    for path in file_paths:
        suffix = Path(path).suffix.lower().lstrip(".")
        filename = Path(path).name.lower()

        if suffix in code_exts or filename in SPECIAL_CODE_FILENAMES:
            code_files.append(path)
        elif suffix in text_exts or filename in SPECIAL_TEXT_FILENAMES:
            text_files.append(path)
        else:
            # 默认将剩余的文本类文件归入文本向量库，避免遗漏核心描述信息
            text_files.append(path)

    return code_files, text_files

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
