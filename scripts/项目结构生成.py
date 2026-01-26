from __future__ import annotations
import json
from pathlib import Path
from typing import Dict, Any
from scripts.setup_repository import setup_repository
from src.config import CONFIG_PATH, load_config, PROJECT_ROOT
from src.ingestion.file_processor import generate_file_tree
from src.wiki.struct_gen import generate_wiki_structure
from src.wiki.content_gen import WikiContentGenerator
from src.ai_client_factory import get_ai_client


# 支持 Git URL（如 https://github.com/user/repo.git）或本地路径
REPOSITORY: str = "https://github.com/AsyncFuncAI/deepwiki-open.git"
# 结构输出文件
OUTPUT_PATH: Path = PROJECT_ROOT / "wiki_structure.json"
# 生成完成后是否打印结果
PRINT_RESULT: bool = True
# AI 生成的 wiki 章节 Markdown 输出目录
WIKI_CONTENT_OUTPUT: Path = PROJECT_ROOT / "wiki_pages"
# 每个章节的 JSON 输出目录，便于调试
WIKI_SECTION_JSON_OUTPUT: Path = PROJECT_ROOT / "wiki_section_json"
# DeepSeek 模型，可按需调整
DEEPSEEK_MODEL: str = "deepseek-chat"


def run_structure_generation(
    repo_url_or_path: str, config_path: Path, output_path: Path
) -> tuple[str, Dict[str, Any]]:
    """
    根据仓库地址（Git URL 或本地路径）生成 wiki 目录结构，并保存到指定文件。
    """
    if repo_url_or_path.startswith(("http://", "https://", "git@")):
        repo_path = setup_repository(repo_url_or_path)
    else:
        repo_path = repo_url_or_path

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")

    config = load_config(str(config_path))

    file_tree = generate_file_tree(repo_path, str(config_path))

    wiki_structure = generate_wiki_structure(repo_path, file_tree)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(wiki_structure, f, indent=2, ensure_ascii=False)

    return repo_path, wiki_structure


def run_wiki_content_generation(
    repo_path: str,
    wiki_structure: Dict[str, Any],
    output_dir: Path,
    json_output_dir: Path,
) -> list[Path]:
    """
    调用 AI 客户端，根据 wiki 目录逐条生成内容与 Mermaid 图，并写入 Markdown。
    """
    client = get_ai_client("qwen")
    generator = WikiContentGenerator(
        repo_root=repo_path,
        output_dir=output_dir,
        json_output_dir=json_output_dir,
        client=client,
    )
    return generator.generate(wiki_structure)


def main() -> None:
    config_path = CONFIG_PATH.expanduser().resolve()
    output_path = OUTPUT_PATH.expanduser().resolve()

    repo_path, wiki_structure = run_structure_generation(
        repo_url_or_path=REPOSITORY,
        config_path=config_path,
        output_path=output_path,
    )

    print("\nWiki 目录生成成功！")
    print(f"输出文件：{output_path}")

    if PRINT_RESULT:
        import pprint

        pprint.pprint(wiki_structure)

    print("\n开始生成 wiki 内容与架构图...")
    generated_files = run_wiki_content_generation(
        repo_path=repo_path,
        wiki_structure=wiki_structure,
        output_dir=WIKI_CONTENT_OUTPUT,
        json_output_dir=WIKI_SECTION_JSON_OUTPUT,
    )
    print(f"内容生成完成，共生成 {len(generated_files)} 个章节。")


if __name__ == "__main__":
    main()
