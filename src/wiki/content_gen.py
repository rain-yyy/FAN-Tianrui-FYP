from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from langchain_core.prompts import ChatPromptTemplate

from src.ai_client_base import BaseAIClient
from src.ai_client_factory import get_ai_client
from src.prompts import get_wiki_section_prompt


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


class WikiContentGenerator:
    """
    将 wiki 目录树与仓库文件上下文交给 AI 模型，生成每个节点对应的内容与 Mermaid 架构图。
    """

    def __init__(
        self,
        *,
        repo_root: str | Path,
        output_dir: str | Path,
        json_output_dir: str | Path | None = None,
        client: BaseAIClient | None = None,
        prompt_template: ChatPromptTemplate | None = None,
        max_file_chars: int = 4000,
        max_section_chars: int = 16000,
    ) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.json_output_dir = (
            Path(json_output_dir).expanduser().resolve()
            if json_output_dir
            else self.output_dir
        )
        self.json_output_dir.mkdir(parents=True, exist_ok=True)

        self.client = client or get_ai_client("qwen")
        self.prompt_template = prompt_template or get_wiki_section_prompt()
        self.max_file_chars = max_file_chars
        self.max_section_chars = max_section_chars

    def generate(self, structure: Dict[str, Any]) -> List[Path]:
        """
        根据 wiki 目录结构依次生成内容，返回所有成功写入的 Markdown 文件路径。
        """
        toc = structure.get("toc") or []
        sections = list(self._flatten_sections(toc))

        generated_files: List[Path] = []
        for section in sections:
            try:
                file_path = self._generate_section(structure, section)
            except Exception as exc:
                print(f"[WARN] 生成章节 '{section.id}' 失败：{exc}")
                continue
            if file_path:
                generated_files.append(file_path)
        return generated_files

    def _generate_section(
        self,
        structure: Dict[str, Any],
        section: WikiSection,
    ) -> Path | None:
        """
        为单个章节构建上下文、调用 LLM，并将结果写入 Markdown。
        """
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

        raw_response = self.client.chat(messages, max_tokens=1800)
        parsed = self._parse_llm_response(raw_response)
        self._write_section_json(section, parsed)
        return self._write_markdown(section, parsed)

    def _collect_file_context(self, files: Iterable[str]) -> str:
        """
        读取文件内容片段，限制单文件与单章节总字数，避免提示词过长。
        """
        snippets: List[str] = []
        accumulated = 0

        for rel_path in files:
            if accumulated >= self.max_section_chars:
                break
            snippet = self._read_single_file(rel_path)
            if not snippet:
                continue
            snippets.append(f"### {rel_path}\n{snippet}")
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
            print(f"[WARN] 跳过越界文件：{rel_path}")
            return ""

        if not abs_path.exists() or not abs_path.is_file():
            print(f"[WARN] 跳过不存在的文件：{rel_path}")
            return ""

        try:
            content = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"[WARN] 文件编码无法解析，已跳过：{rel_path}")
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

    def _write_section_json(self, section: WikiSection, data: Dict[str, Any]) -> Path:
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

        filename = self._safe_filename(section.id) + ".json"
        target_path = self.json_output_dir / filename
        target_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[DEBUG] 已写入章节 JSON：{section.id} -> {target_path}")
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

    def _write_markdown(self, section: WikiSection, data: Dict[str, Any]) -> Path:
        """
        将生成结果转换为 Markdown 文件，包含章节正文与 Mermaid 代码块。
        """
        filename = self._safe_filename(section.id) + ".md"
        target_path = self.output_dir / filename

        intro = data.get("intro") or data.get("summary") or ""
        mermaid = data.get("mermaid", "").strip()
        sections = data.get("sections") or []

        lines: List[str] = [
            f"# {section.title}",
            "",
            f"> 导航：{section.display_path()}",
            "",
        ]
        if intro:
            lines.append(intro.strip())
            lines.append("")

        for block in sections:
            heading = block.get("heading") or block.get("title")
            body = block.get("body") or block.get("content")
            if heading:
                lines.append(f"## {heading.strip()}")
            if body:
                lines.append(body.strip())
            lines.append("")

        if mermaid:
            lines.append("```mermaid")
            lines.append(mermaid)
            lines.append("```")
            lines.append("")

        target_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print(f"[INFO] 已生成章节：{section.id} -> {target_path}")
        return target_path

    @staticmethod
    def _safe_filename(name: str) -> str:
        """
        将章节 ID 转换为文件系统安全的文件名。
        """
        sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip().lower())
        return sanitized or "section"

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

