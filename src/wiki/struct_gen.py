from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import dotenv
from src.config import PROJECT_ROOT
from langchain_openai import ChatOpenAI
from src.prompts import get_structure_prompt

dotenv.load_dotenv()
openai_key = os.getenv("OPENAI_API_KEY")

REPO_MAPPER_DIR = PROJECT_ROOT / "RepoMapper"


def _build_repo_map_context(repo_path: str, target_subdir: str = "src") -> str:
    """
    调用 RepoMapper，生成用于提示词的代码骨架摘要。
    """
    repo_mapper_dir = REPO_MAPPER_DIR
    if not repo_mapper_dir.exists():
        print(f"Repo map directory missing: {repo_mapper_dir}")
        return ""

    repo_mapper_path = str(repo_mapper_dir)
    if repo_mapper_path not in sys.path:
        sys.path.insert(0, repo_mapper_path)

    try:
        from RepoMapper.repomap import find_src_files
        from RepoMapper.repomap_class import RepoMap
    except ImportError as exc:
        print(f"Repo map dependencies missing, skip context: {exc}")
        return ""

    repo_root = Path(repo_path).resolve()
    search_root = repo_root / target_subdir
    if not search_root.exists():
        search_root = repo_root

    try:
        candidate_files = find_src_files(str(search_root))
    except Exception as exc:  # noqa: BLE001 - 依赖库内部异常需吞掉
        print(f"Repo map file discovery failed: {exc}")
        return ""

    if not candidate_files:
        print("Repo map: no files discovered, skip context.")
        return ""

    resolved_files = [str(Path(path).resolve()) for path in candidate_files]

    try:
        repo_map = RepoMap(
            map_tokens=8192,
            root=str(repo_root),
            verbose=False,
        )
        map_content, _ = repo_map.get_repo_map(
            chat_files=[],
            other_files=resolved_files,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Repo map generation failed: {exc}")
        return ""

    if not map_content:
        print("Repo map returned empty content.")
        return ""

    return map_content.strip()


def generate_wiki_structure(
    repo_path: str, file_tree: str, repo_map: Optional[str] = None
) -> Dict[str, Any]:
    """
    调用 gpt 4o mini 生成分层 Wiki 目录并解析 JSON 响应。
    """
    print("Generating wiki structure with AI...")

    # 1. 加载 README 内容
    readme_path = os.path.join(repo_path, "README.md")
    readme_content = ""
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            readme_content = f.read()
    else:
        print("Warning: README.md not found. Context will be limited.")

    # 2. 初始化模型和提示
    llm = ChatOpenAI(model="gpt-4o-mini-2024-07-18", temperature=0.1)
    prompt = get_structure_prompt()
    current_date = datetime.utcnow().date().isoformat()

    # 2.1 准备 RepoMap 语境
    if repo_map is None:
        print("Building repo map context...")
        repo_map = _build_repo_map_context(repo_path)
        print(repo_map)

    # 3. 构建执行链
    chain = prompt | llm

    # 4. 调用 AI
    print("Invoking AI model...")
    response = chain.invoke(
        {
            "file_tree": file_tree,
            "readme_content": readme_content,
            "current_date": current_date,
            "repo_map": repo_map or "",
        }
    )

    ai_message_content = getattr(response, "content", response)
    if isinstance(ai_message_content, list):
        ai_message_content = "".join(
            part for part in ai_message_content if isinstance(part, str)
        )
    if not isinstance(ai_message_content, str):
        raise ValueError("Unexpected AI response type; expected string content.")

    print("AI response received.")

    # 4.1 将原始响应保存到文件，方便调试
    debug_dir = os.path.join(os.getcwd(), "debug_outputs")
    os.makedirs(debug_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_path = os.path.join(debug_dir, f"wiki_structure_raw_{timestamp}.json")
    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(ai_message_content)
    print(f"Raw AI response saved to: {debug_path}")

    # 5. 解析 JSON 输出
    print("Parsing AI response...")
    return parse_wiki_structure_json(ai_message_content, fallback_date=current_date)


def parse_wiki_structure_json(raw_json: str, *, fallback_date: str) -> Dict[str, Any]:
    """
    解析 LLM 返回的 JSON 字符串，并规范化 toc 节点结构。
    """
    cleaned_json = _strip_code_fence(raw_json)

    try:
        data = json.loads(cleaned_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON response: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Invalid JSON response: 根元素必须是对象。")

    title = _require_str(data, "title")
    description = _require_str(data, "description")

    toc_raw = data.get("toc", [])
    if not isinstance(toc_raw, list):
        raise ValueError("Invalid JSON response: 'toc' 必须是数组。")

    normalized_toc = [_normalize_toc_node(node) for node in toc_raw]

    last_indexed = data.get("lastIndexed") or fallback_date
    if not isinstance(last_indexed, str):
        raise ValueError("Invalid JSON response: 'lastIndexed' 必须是字符串。")

    print("Parsing complete.")
    return {
        "title": title,
        "description": description,
        "lastIndexed": last_indexed,
        "toc": normalized_toc,
    }


def _strip_code_fence(text: str) -> str:
    """
    去掉 ```json``` 等代码块包裹，返回纯文本。
    """
    stripped = text.strip()
    fence_match = re.match(r"^```[\w+-]*\s*", stripped)
    if fence_match:
        stripped = stripped[fence_match.end() :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()


def _normalize_toc_node(node: Any) -> Dict[str, Any]:
    if not isinstance(node, dict):
        raise ValueError("Invalid JSON response: toc 节点必须是对象。")

    node_id = _require_str(node, "id")
    title = _require_str(node, "title")

    normalized: Dict[str, Any] = {
        "id": node_id.strip(),
        "title": title.strip(),
    }

    children_raw = node.get("children")
    if children_raw:
        if not isinstance(children_raw, list):
            raise ValueError("Invalid JSON response: 'children' 必须是数组。")
        children = [_normalize_toc_node(child) for child in children_raw]
        if children:
            normalized["children"] = children

    files_raw = node.get("files", [])
    if files_raw:
        if not isinstance(files_raw, list):
            raise ValueError("Invalid JSON response: 'files' 必须是数组。")
        files: list[str] = []
        for item in files_raw:
            if not isinstance(item, str):
                raise ValueError("Invalid JSON response: 'files' 数组元素必须是字符串。")
            cleaned = item.strip()
            if cleaned:
                files.append(cleaned)
        normalized["files"] = files
    elif isinstance(files_raw, list):
        normalized["files"] = []

    if "files" not in normalized:
        normalized["files"] = []

    return normalized


def _require_str(obj: Dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid JSON response: '{key}' 必须是非空字符串。")
    return value

