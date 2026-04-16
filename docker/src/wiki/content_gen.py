from __future__ import annotations

import json
import re
import logging
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Sequence, Optional, Tuple

from langchain_core.prompts import ChatPromptTemplate

from src.clients.ai_client_base import BaseAIClient
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG, get_wiki_content_concurrency
from src.prompts import get_wiki_section_prompt

# 初始化日志
logger = logging.getLogger("app.wiki.content_gen")

@dataclass(slots=True)
class WikiSection:
    """
    表示 wiki 目录树中的一个节点，同时保存其祖先标题路径，便于提示词构造。
    """

    id: str
    title: str
    files: List[str]
    breadcrumbs: List[str] = field(default_factory=list)

    def display_path(self) -> str:
        return " / ".join(self.breadcrumbs + [self.title])


from src.ingestion.code_graph import CodeGraphBuilder
import networkx as nx

class WikiContentGenerator:
    """
    将 wiki 目录树与仓库文件上下文交给 AI 模型，生成每个节点对应的内容与 Mermaid 架构图。
    支持受控并发（线程池），每个工作线程独立获取 AI 客户端实例。
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        json_output_dir: str | Path,
        output_dir: str | Path | None = None,  # 保留以兼容旧代码，但不再使用
        client: BaseAIClient | None = None,
        client_factory: Callable[[], BaseAIClient] | None = None,
        prompt_template: ChatPromptTemplate | None = None,
        max_file_chars: int = 4000,
        max_section_chars: int = 16000,
        max_concurrency: int | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        task_id: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.json_output_dir = Path(json_output_dir).expanduser().resolve()
        self.json_output_dir.mkdir(parents=True, exist_ok=True)

        # 客户端工厂：并发时每个 worker 独立创建实例
        if client_factory is not None:
            self._client_factory = client_factory
        elif client is not None:
            # 单线程回退：直接使用传入的 client
            self._client_factory = lambda: client
        else:
            provider, model = get_model_config(CONFIG, "wiki_content")
            self._client_factory = lambda: get_ai_client(provider, model=model)

        # 保留 self.client 用于不需要并发的内部方法
        self.client = client if client is not None else self._client_factory()

        self.prompt_template = prompt_template or get_wiki_section_prompt()
        self.max_file_chars = max_file_chars
        self.max_section_chars = max_section_chars
        self.max_concurrency = max_concurrency or get_wiki_content_concurrency()
        self.progress_callback = progress_callback
        self.task_id = task_id or "unknown"
        self.graph: Optional[nx.DiGraph] = None

    def set_graph(self, graph: nx.DiGraph):
        self.graph = graph

    def generate(self, structure: Dict[str, Any]) -> List[Path]:
        """
        根据 wiki 目录结构并发生成内容，返回所有成功写入的 JSON 文件路径。
        """
        toc = structure.get("toc") or []
        sections = list(self._flatten_sections(toc))
        total = len(sections)
        if total == 0:
            logger.info(f"[task={self.task_id}] 无章节需要生成")
            return []

        # Phase 5: 预处理文件名映射，在主线程中一次性完成，避免并发冲突
        filename_map = self._build_filename_map(sections)

        concurrency = min(self.max_concurrency, total)
        logger.info(
            f"[task={self.task_id}] 开始并发生成 wiki 正文: "
            f"总章节数={total}, 并发上限={concurrency}"
        )

        # 线程安全的进度计数器
        lock = threading.Lock()
        counters = {"completed": 0, "failed": 0, "active": 0}
        generated_files: List[Path] = []

        def _worker(section: WikiSection) -> Tuple[WikiSection, Optional[Path]]:
            """单个章节的生成 worker，每个 worker 独立获取 AI 客户端"""
            worker_client = self._client_factory()

            with lock:
                counters["active"] += 1
                active = counters["active"]
                completed = counters["completed"]
            logger.info(
                f"[task={self.task_id}] 章节开始: section_id={section.id!r}, "
                f"title={section.title!r}, "
                f"active={active}, completed={completed}/{total}"
            )
            t0 = time.monotonic()
            try:
                file_path = self._generate_section(
                    structure, section, client=worker_client,
                    filename_override=filename_map.get(section.id),
                )
                elapsed = time.monotonic() - t0
                with lock:
                    counters["active"] -= 1
                    counters["completed"] += 1
                    completed_now = counters["completed"]
                logger.info(
                    f"[task={self.task_id}] 章节完成: section_id={section.id!r}, "
                    f"耗时={elapsed:.1f}s, "
                    f"文件={file_path.name if file_path else 'N/A'}, "
                    f"完成={completed_now}/{total}"
                )
                # 推进进度: 50 -> 85 按章节完成比例线性推进
                if self.progress_callback:
                    progress = 50.0 + (completed_now / total) * 35.0
                    self.progress_callback(
                        progress,
                        f"Generating Wiki content ({completed_now}/{total})..."
                    )
                return section, file_path
            except Exception as exc:
                elapsed = time.monotonic() - t0
                with lock:
                    counters["active"] -= 1
                    counters["failed"] += 1
                    failed_now = counters["failed"]
                logger.warning(
                    f"[task={self.task_id}] 章节失败: section_id={section.id!r}, "
                    f"耗时={elapsed:.1f}s, 异常={exc!r}, "
                    f"累计失败={failed_now}"
                )
                return section, None

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_worker, sec): sec for sec in sections}
            for future in as_completed(futures):
                _, file_path = future.result()
                if file_path:
                    generated_files.append(file_path)

        logger.info(
            f"[task={self.task_id}] 正文生成完毕: "
            f"成功={len(generated_files)}, 失败={counters['failed']}, 总数={total}"
        )
        return generated_files

    def _generate_section(
        self,
        structure: Dict[str, Any],
        section: WikiSection,
        *,
        client: BaseAIClient | None = None,
        filename_override: str | None = None,
    ) -> Path | None:
        """
        为单个章节构建上下文、调用 LLM，并将结果写入 JSON。
        """
        used_client = client or self.client
        context = self._collect_file_context(section.files)
        if not context:
            context = "未能找到关联文件，请基于章节标题进行合理推断。"

        doc_title = structure.get("title", "Wiki")
        doc_description = structure.get("description", "")
        breadcrumb = section.display_path()

        messages = self._build_messages(
            doc_title=doc_title,
            doc_description=doc_description,
            breadcrumb=breadcrumb,
            section_id=section.id,
            context=context,
        )

        raw_response = used_client.chat(messages, max_tokens=1800)
        parsed = self._parse_llm_response(raw_response)
        return self._write_section_json(section, parsed, filename_override=filename_override)

    def _collect_file_context(self, files: Iterable[str]) -> str:
        """
        读取文件内容片段，并结合图谱提取依赖关系。
        """
        snippets: List[str] = []
        accumulated = 0
        
        rel_files = list(files)

        for rel_path in rel_files:
            if accumulated >= self.max_section_chars:
                break
            snippet = self._read_single_file(rel_path)
            if not snippet:
                continue
            
            # 提取图谱中的依赖信息
            dependency_info = ""
            if self.graph and rel_path in self.graph:
                deps = [neighbor for neighbor in self.graph.neighbors(rel_path) 
                        if self.graph.edges[rel_path, neighbor].get("type") == "calls"]
                if deps:
                    dependency_info = f"\n[逻辑依赖]: 该文件调用了 {', '.join(deps[:5])}"

            snippets.append(f"### {rel_path}{dependency_info}\n{snippet}")
            accumulated += len(snippet)

        return "\n\n".join(snippets)

    def _read_single_file(self, rel_path: str) -> str:
        """
        读取单个文件的文本内容，并裁剪超长文本。
        """
        safe_rel = rel_path.strip().lstrip("./")
        abs_path = (self.repo_root / safe_rel).resolve()

        # 防止目录越界：确保目标路径位于 repo 根目录之内。
        try:
            abs_path.relative_to(self.repo_root)
        except ValueError:
            logger.warning(f"跳过越界文件：{rel_path}")
            return ""

        if not abs_path.exists() or not abs_path.is_file():
            logger.warning(f"跳过不存在的文件：{rel_path}")
            return ""

        try:
            content = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning(f"文件编码无法解析，已跳过：{rel_path}")
            return ""

        snippet = content.strip()
        if len(snippet) > self.max_file_chars:
            snippet = snippet[: self.max_file_chars] + "\n...（其余内容已截断）"

        return snippet

    def _build_messages(
        self,
        *,
        doc_title: str,
        doc_description: str,
        breadcrumb: str,
        section_id: str,
        context: str,
    ) -> List[Dict[str, str]]:
        """
        使用 prompts 模块中定义的模板生成标准对话消息。
        """
        prompt_messages = self.prompt_template.format_messages(
            doc_title=doc_title,
            doc_description=doc_description,
            breadcrumb=breadcrumb,
            section_id=section_id,
            context=context,
        )

        formatted: List[Dict[str, str]] = []
        for msg in prompt_messages:
            role = getattr(msg, "type", getattr(msg, "role", "user"))
            if role == "human":
                role = "user"
            content = getattr(msg, "content", "")
            formatted.append({"role": role, "content": content})
        return formatted

    def _write_section_json(self, section: WikiSection, data: Dict[str, Any], *, filename_override: str | None = None) -> Path:
        """
        将 LLM 输出和章节元数据写入独立 JSON，便于离线调试提示词。
        """
        payload = {
            "section_id": section.id,
            "title": section.title,
            "breadcrumb": section.display_path(),
            "files": section.files,
            "content": data,
        }

        filename = (filename_override or self._safe_filename(section.id)) + ".json"
        target_path = self.json_output_dir / filename
        target_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"已写入章节 JSON：{section.id} -> {target_path}")
        return target_path

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """
        尝试解析模型返回的 JSON；若失败则回退为单段文本与空 Mermaid。
        """
        candidate = self._extract_json_block(response)
        if candidate:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass

        # 如果模型未遵循 JSON 要求，则将原始文本视作简介
        return {"intro": response.strip(), "sections": [], "mermaid": ""}

    @staticmethod
    def _extract_json_block(text: str) -> str | None:
        """
        从模型回复中提取首个 JSON 对象字符串。
        """
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        candidate = text[start : end + 1]
        # 简单校验括号是否匹配，避免截断
        if candidate.count("{") == candidate.count("}"):
            return candidate
        return None

    @staticmethod
    def _safe_filename(name: str) -> str:
        """
        将章节 ID 转换为文件系统安全的文件名。
        """
        sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip().lower())
        # 去除首尾连字符
        sanitized = sanitized.strip("-")
        return sanitized or "section"

    @staticmethod
    def _normalize_for_collision(name: str) -> str:
        """
        将文件名进一步规范化用于冲突检测：
        - Unicode NFKD 标准化
        - 大小写折叠
        - 连续分隔符合并
        """
        normalized = unicodedata.normalize("NFKD", name).lower()
        normalized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        return normalized or "section"

    def _build_filename_map(self, sections: List[WikiSection]) -> Dict[str, str]:
        """
        在主线程中一次性为所有章节分配不冲突的文件名。
        返回 {section.id: safe_filename_without_extension} 映射。
        """
        # 第一轮：生成基础名称
        base_names: List[Tuple[str, str]] = []  # (section_id, base_name)
        for sec in sections:
            base_names.append((sec.id, self._safe_filename(sec.id)))

        # 第二轮：检测规范化后的冲突并追加后缀
        seen: Dict[str, int] = {}  # normalized_name -> 已分配的次数
        result: Dict[str, str] = {}

        for section_id, base_name in base_names:
            norm = self._normalize_for_collision(base_name)
            count = seen.get(norm, 0)
            seen[norm] = count + 1
            if count == 0:
                result[section_id] = base_name
            else:
                # 追加稳定后缀
                result[section_id] = f"{base_name}-{count + 1}"

        return result

    def _flatten_sections(self, toc: Sequence[Dict[str, Any]], ancestors: List[str] | None = None) -> Iterable[WikiSection]:
        """
        深度优先遍历 toc，产出包含面包屑信息的章节对象。
        """
        ancestors = ancestors or []
        for node in toc:
            section = WikiSection(
                id=node.get("id", "section"),
                title=node.get("title", "Untitled"),
                files=[str(path) for path in (node.get("files") or [])],
                breadcrumbs=list(ancestors),
            )
            yield section
            children = node.get("children") or []
            if children:
                yield from self._flatten_sections(children, ancestors=ancestors + [section.title])


__all__ = ["WikiContentGenerator", "WikiSection"]

