from __future__ import annotations

import ast
import json
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import dotenv
import networkx as nx
from src.config import CONFIG, should_save_wiki_structure_raw_responses
from src.clients import get_llm, StrOutputParser
from src.prompts import STRUCTURE_PROMPT
from src.ingestion.code_graph import CodeGraphBuilder, rank_important_symbols
from src.ingestion.community_engine import CommunityEngine
from src.ingestion.file_processor import get_files_to_process

# 初始化日志
logger = logging.getLogger("app.wiki.struct_gen")

dotenv.load_dotenv()


def _build_key_symbols_context(graph: Optional[nx.DiGraph], top_n: int = 60) -> str:
    """
    用 code graph 的 PageRank 排名结果，渲染成用于提示词的"关键符号"摘要。
    """
    if graph is None:
        return ""

    try:
        ranked = rank_important_symbols(graph, top_n=top_n)
    except Exception as exc:  # noqa: BLE001 - 图退化（如无边）时 PageRank 可能报错，按现有策略吞掉
        logger.error(f"Key symbols ranking failed: {exc}")
        return ""

    if not ranked:
        return ""

    lines = sorted(
        f"{item['file_path']}:{item['start_line']}  {item['type']} {item['qualified_name']}"
        for item in ranked
    )

    return "Key Symbols (ranked by importance):\n" + "\n".join(lines)


def generate_wiki_structure(
    repo_path: str, file_tree: str, communities_info: Optional[str] = None,
    valid_file_list: Optional[str] = None,
    communities_persist_path: Optional[str] = None,
    code_graph_persist_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成分层 Wiki 目录并解析 JSON 响应。
    """
    logger.info("Generating wiki structure with AI...")

    # 1. 加载 README 内容
    readme_path = os.path.join(repo_path, "README.md")
    readme_content = ""
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            readme_content = f.read()
    else:
        logger.warning("README.md not found. Context will be limited.")

    # 2. 初始化 LCEL chain
    chain = STRUCTURE_PROMPT.build() | get_llm("wiki_structure", temperature=0.1) | StrOutputParser()
    current_date = datetime.utcnow().date().isoformat()

    # 2.1 准备有效文件列表（用于约束 LLM 输出，防止其虚构不存在的文件路径）
    filtered_file_paths = get_files_to_process(repo_path)
    if valid_file_list is None:
        relative_paths = sorted(os.path.relpath(p, repo_path) for p in filtered_file_paths)
        valid_file_list = "\n".join(relative_paths)

    # 2.2 构建代码图（社区信息与关键符号排名共用同一份图，二者互不依赖对方是否成功）
    graph = None
    try:
        logger.info("Building code graph...")

        builder = CodeGraphBuilder()
        graph = builder.build_graph(repo_path, filtered_file_paths)

        if code_graph_persist_path:
            try:
                builder.save_graph(code_graph_persist_path)
                logger.info("Code graph saved to %s", code_graph_persist_path)
            except OSError as cg_exc:
                logger.warning("Failed to persist code graph: %s", cg_exc)
    except Exception as e:
        logger.error(f"Failed to build code graph: {e}")

    # 2.3 准备社区信息
    if communities_info is None:
        if graph is None:
            communities_info = "No community information available."
        else:
            try:
                logger.info("Building communities...")

                engine = CommunityEngine(graph)
                communities = engine.run_leiden()
                summaries = engine.generate_summaries()

                # 格式化社区信息为字符串
                comm_list = []
                for cid, summary in summaries.items():
                    nodes = communities.get(cid, [])
                    # 只列出前几个核心文件
                    core_files = [n for n in nodes if ":" not in n][:5]
                    comm_list.append(f"Community {cid}:\n- Summary: {summary}\n- Key Files: {', '.join(core_files)}")

                communities_info = "\n\n".join(comm_list)
                logger.info(f"Community info built: {len(communities_info)} chars")
                if communities_persist_path:
                    try:
                        engine.save_results(communities_persist_path)
                        logger.info("GraphRAG communities saved to %s", communities_persist_path)
                    except OSError as persist_exc:
                        logger.warning("Failed to persist GraphRAG communities: %s", persist_exc)
            except Exception as e:
                logger.error(f"Failed to build communities: {e}")
                communities_info = "No community information available."

    # 2.4 准备关键符号语境（不依赖 communities_info 是否由调用方预先提供）
    logger.info("Building key symbols context...")
    key_symbols = _build_key_symbols_context(graph)
    logger.info(f"Key symbols context built: {len(key_symbols)} characters")

    # 4. 调用 AI
    logger.info("Invoking AI model...")
    ai_message_content = chain.invoke({
        "file_tree": file_tree,
        "readme_content": readme_content,
        "current_date": current_date,
        "key_symbols": key_symbols or "",
        "communities": communities_info or "",
        "valid_file_list": valid_file_list or "",
    })

    logger.info("AI response received.")

    # 4.1 保存原始响应到文件（辅助调试）；默认关闭，避免长期运行进程磁盘无限增长
    if should_save_wiki_structure_raw_responses():
        debug_dir = os.path.join(os.getcwd(), "wiki_structure_raw")
        os.makedirs(debug_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_path = os.path.join(debug_dir, f"{timestamp}.txt")
        try:
            with open(debug_path, "w", encoding="utf-8") as f:
                f.write(ai_message_content)
            logger.info(f"Raw AI response saved to: {debug_path}")
        except Exception as e:
            logger.warning(f"Failed to save raw AI response: {e}")

    # 5. 解析 JSON 输出
    logger.info("Parsing AI response...")
    return parse_wiki_structure_json(ai_message_content, fallback_date=current_date)


def _extract_balanced_braces(s: str) -> str:
    """
    从字符串中提取第一个大括号平衡的子串（{...}），
    忽略字符串内部的括号，用于将 Python 风格 dict 传给 ast.literal_eval。
    """
    depth = 0
    in_str = False
    str_char = ''
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            if c == '\\':
                i += 2
                continue
            if c == str_char:
                in_str = False
        else:
            if c in ('"', "'"):
                in_str = True
                str_char = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return s[:i + 1]
        i += 1
    return s


# 哨兵：区分「解析失败」与「解析成功但结果恰好是 None/False/0」等假值。
_PARSE_FAILED = object()


def _try_raw_decode(text: str) -> Any:
    """标准 JSON raw_decode，允许尾随内容；失败返回 _PARSE_FAILED（不抛异常）。"""
    try:
        data, _ = json.JSONDecoder().raw_decode(text)
        return data
    except (json.JSONDecodeError, ValueError):
        return _PARSE_FAILED


def _try_literal_eval(text: str) -> Any:
    """ast.literal_eval（Python 字面量，如单引号 dict）；失败返回 _PARSE_FAILED。"""
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return _PARSE_FAILED


def _parse_llm_json(candidate: str) -> Any:
    """
    多策略解析 LLM 输出的 JSON / 类 JSON 字符串：

    1. json.JSONDecoder().raw_decode —— 标准 JSON，忽略尾随文字
    2. ast.literal_eval —— 处理 Python 风格单引号 dict
    3. 单引号 → 双引号替换后再 raw_decode —— 兜底
    """
    data = _try_raw_decode(candidate)
    if data is not _PARSE_FAILED:
        return data

    literal = _try_literal_eval(_extract_balanced_braces(candidate))
    if isinstance(literal, dict):
        # 将 Python 对象序列化再反序列化，确保可以被后续校验代码处理
        return json.loads(json.dumps(literal, ensure_ascii=False))

    try:
        fixed = re.sub(
            r"'([^'\\]*(?:\\.[^'\\]*)*)'",
            lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
            candidate,
        )
        data = _try_raw_decode(fixed)
        if data is not _PARSE_FAILED:
            return data
    except re.error:
        pass

    raise json.JSONDecodeError("All JSON parsing strategies failed", candidate, 0)


def parse_wiki_structure_json(raw_json: str, *, fallback_date: str) -> Dict[str, Any]:
    """
    解析 LLM 返回的 JSON 字符串，并规范化 toc 节点结构。

    LLM 可能返回多种格式（按优先级依次尝试）：
    0. 整体是合法 JSON（直接解析）或双重编码字符串（解一层再解一层）
    1. 有效 JSON + 尾随文字 → raw_decode 忽略尾随
    2. Python 风格单引号 dict → ast.literal_eval
    3. 单引号替换为双引号后再解析
    """
    cleaned_json = _strip_code_fence(raw_json)
    data: Any = None

    # ── 策略 0：整体 raw_decode / ast.literal_eval ──────────────────────────────────
    # 覆盖以下情况：
    # (a) 模型直接返回合法 JSON 对象（可能带尾随内容）
    # (b) 模型将 JSON 双重编码为字符串字面量（如 `"{\\"title\\"...}"}`）
    #     → 解外层字符串 → 再 raw_decode 解内层 JSON 对象
    try:
        stripped = cleaned_json.lstrip()
        outer = _try_raw_decode(stripped)
        if outer is _PARSE_FAILED and (stripped.startswith('"') or stripped.startswith("'")):
            # 兼容带有非法转义字符（如 \'）的双引号包裹字符串
            outer = _try_literal_eval(stripped)

        if isinstance(outer, dict):
            data = outer
        elif isinstance(outer, str):
            inner_stripped = outer.lstrip()
            inner = _try_raw_decode(inner_stripped)
            if inner is _PARSE_FAILED:
                # 兼容内层也是用 ast 解析的情况
                inner = _try_literal_eval(inner_stripped)

            if isinstance(inner, dict):
                data = inner
    except Exception:
        pass

    # ── 策略 1–3：定位第一个 '{' 后多策略解析 ──────────────────────────────
    if data is None:
        brace_idx = cleaned_json.find('{')
        if brace_idx == -1:
            raise ValueError("Invalid JSON response: 未找到 JSON 对象起始符 '{'。")
        candidate = cleaned_json[brace_idx:]
        try:
            data = _parse_llm_json(candidate)
        except json.JSONDecodeError as exc:
            logger.error(
                "parse_wiki_structure_json: 所有解析策略均失败。"
                "原始文本前 500 字符: %r",
                cleaned_json[:500],
            )
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

    logger.info("Parsing complete.")
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