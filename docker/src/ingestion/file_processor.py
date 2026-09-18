import os
import subprocess
from pathlib import Path

ALLOWED_SUFFIXES: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".cpp", ".c", ".h",
    ".rb",
    ".json", ".yaml", ".yml", ".toml", ".md",
})

CODE_SUFFIXES: frozenset[str] = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".go", ".rs", ".cpp", ".c", ".h",
    ".rb",
})

TEXT_SUFFIXES: frozenset[str] = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".md",
})


def _git_tracked_paths(repo_path: str) -> list[str]:
    # 返回 repo_path 下 git 认为"有效"的文件（相对路径），忽略规则完全交给仓库自身的 .gitignore。
    result = subprocess.run(
        # --cached 已跟踪文件，--others --exclude-standard 补上未被忽略的新文件
        ["git", "-C", repo_path, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True,
    )
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def find_relevant_files(repo_path: str) -> list[str]:
    # 在 git 已过滤出的有效文件基础上，再按后缀白名单筛一遍。
    repo_path = os.path.abspath(repo_path)

    relevant_files: list[str] = []
    for rel_path in _git_tracked_paths(repo_path):
        if Path(rel_path).suffix.lower() not in ALLOWED_SUFFIXES:
            continue
        abs_path = os.path.join(repo_path, rel_path)
        if os.path.isfile(abs_path):  # 跳过已 staged 删除但尚未提交的路径
            relevant_files.append(abs_path)

    print(f"Found {len(relevant_files)} relevant files.")
    return relevant_files


def split_code_and_text_files(file_paths: list[str]) -> tuple[list[str], list[str]]:
    code_files: list[str] = []
    text_files: list[str] = []

    for path in file_paths:
        suffix = Path(path).suffix.lower()
        if suffix in CODE_SUFFIXES:
            code_files.append(path)
        else:
            text_files.append(path)

    return code_files, text_files


def generate_file_tree(repo_path: str) -> str:
    # 把相关文件的相对路径拼成嵌套 dict，再递归渲染成 "├──/└──" 风格的文本目录树。
    print("Generating file tree...")
    relevant_files = find_relevant_files(repo_path)

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
    # 把相关文件的相对路径列表拼成一棵嵌套 dict，再递归渲染成 "├──/└──" 风格的文本目录树。


def get_files_to_process(repo_path: str) -> list[str]:
    return find_relevant_files(repo_path)
