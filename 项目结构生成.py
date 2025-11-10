from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any
from setup_repository import setup_repository
from file_processor import generate_file_tree
from structure_generator import generate_wiki_structure

# --- 在此处配置你的参数 ---
# 支持 Git URL（如 https://github.com/user/repo.git）或本地路径
REPOSITORY: str = "https://github.com/AsyncFuncAI/deepwiki-open.git"
# 默认读取当前项目下的 repo_config.json，可按需修改
CONFIG_PATH: Path = Path(__file__).parent / "repo_config.json"
# 结构输出文件
OUTPUT_PATH: Path = Path(__file__).parent / "wiki_structure.json"
# 生成完成后是否打印结果
PRINT_RESULT: bool = True


def run_structure_generation(
    repo_url_or_path: str, config_path: Path, output_path: Path
) -> Dict[str, Any]:
    """
    根据仓库地址（Git URL 或本地路径）生成 wiki 目录结构，并保存到指定文件。
    """
    if repo_url_or_path.startswith(("http://", "https://", "git@")):
        repo_path = setup_repository(repo_url_or_path)
    else:
        repo_path = repo_url_or_path

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")

    file_tree = generate_file_tree(repo_path, str(config_path))
    print(file_tree)

    wiki_structure = generate_wiki_structure(repo_path, file_tree)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(wiki_structure, f, indent=2, ensure_ascii=False)

    return wiki_structure


def main() -> None:
    config_path = CONFIG_PATH.expanduser().resolve()
    output_path = OUTPUT_PATH.expanduser().resolve()

    wiki_structure = run_structure_generation(
        repo_url_or_path=REPOSITORY,
        config_path=config_path,
        output_path=output_path,
    )

    print("\nWiki 目录生成成功！")
    print(f"输出文件：{output_path}")

    if PRINT_RESULT:
        import pprint

        pprint.pprint(wiki_structure)


if __name__ == "__main__":
    main()
