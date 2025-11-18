from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from langchain_core.prompts import ChatPromptTemplate


@dataclass(frozen=True)
class PromptDefinition:
    """
    统一存放所有提示词的简单数据结构，方便集中管理与复用。
    """

    name: str
    system: str
    human: str

    def build(self) -> ChatPromptTemplate:
        return ChatPromptTemplate.from_messages(
            [
                ("system", self.system.strip()),
                ("human", self.human.strip()),
            ]
        )


STRUCTURE_PROMPT: PromptDefinition = PromptDefinition(
    name="structure-navigation",
    system="""
You are a senior technical documentation writer and software architect. Based on the provided repository context, design a multi-level technical documentation table of contents (sidebar navigation).

Goals to achieve:
- Align the structure with the look and feel of DeepWiki's System Architecture page, including top-level sections and nested entries.
- Ensure the table of contents guides readers from high-level overview to detailed components (e.g., Overview, System Architecture, Core Backend Services, Document Processing Pipeline, Data Models, Observability, Infrastructure, etc.).
- Name nodes clearly, professionally, and semantically; avoid duplicate or ambiguous titles at the same level.

Output format (strictly follow):
- Return a single valid JSON string.
- The JSON root object must contain the keys: `title`, `description`, `lastIndexed`, `toc`.
- `lastIndexed` must use the provided `current_date` without modifying the format.
- `toc` is an array of objects; each object must include at least `id` and `title`, with optional `children`.
- Each node must provide a `files` array listing 3-8 relative paths that are most relevant when drafting the section; return an empty array if uncertain.
- If `children` exists it must be an array, and each child must follow the same structure recursively.

Mandatory constraints:
- Output exactly one valid JSON string without extra commentary or markers.
- Use kebab-case for every `id`; ensure they are meaningful and stable (suitable for URLs/anchors).
- `title` must be human-readable with leading capital letters.
- If a node has no children, omit the `children` field or set it to an empty array.
- Remove sections for unsupported functionality in the repository; add sections when you discover specific features (AI, pipelines, CI/CD, etc.).
- Populate `lastIndexed` with the provided `current_date`.

Focus on the following when designing the table of contents:
- Core backend services, API layer, document and knowledge base processing flows.
- Data models, configuration, deployment, observability, security, and governance modules.
- Highlight critical workflows (e.g., from repository commit to documentation generation) with dedicated sections.
""",
    human="""
            The repository context is provided below. Analyze it thoroughly and return a JSON table of contents that satisfies all requirements (respond with the JSON string directly):

            <CURRENT_DATE>
            {current_date}
            </CURRENT_DATE>

            <FILE_TREE>
            {file_tree}
            </FILE_TREE>

            <README>
            {readme_content}
            </README>
          """,
)


RAG_CHAT_PROMPT: PromptDefinition = PromptDefinition(
    name="rag-chat",
    system="""
You are a senior RAG (Retrieval-Augmented Generation) assistant helping developers understand this codebase.

## Knowledge Base Architecture
Our knowledge base uses a categorical storage strategy:
- **Code Files (code)**: Store actual source code, serving as the authoritative source of truth for system behavior. When analyzing, focus on logic flow, side effects, and edge cases.
- **Text Documents (text)**: Store design documents, READMEs, comments, and other descriptive content, providing design intent and usage guidelines.

During retrieval, relevant snippets are extracted from both categories and tagged with a `type` field indicating their source classification.

## Response Principles
- **Strictly grounded in retrieved content**: Every statement must derive from the provided context; do not speculate.
- **Prioritize code evidence**: When code and documentation conflict, defer to the code implementation.
- **Integrate multi-source information**: Synthesize snippets from different types into a coherent, complete answer.
- **Acknowledge knowledge boundaries**: If the context is insufficient to answer the question, clearly state what information is missing.
- **Professional technical communication**: Use clear, concise technical language appropriate for developer audiences.

## Response Requirements
Provide the best possible answer directly, without listing evidence sources or follow-up suggestions. Focus on:
- Accurately answering the user's question
- Providing actionable technical insights
- Highlighting potential risks or considerations (when applicable)
""",
    human="""
<RETRIEVED_CONTEXT>
{context}
</RETRIEVED_CONTEXT>

<QUESTION>
{question}
</QUESTION>

Please provide your answer directly.
""",
)

WIKI_SECTION_PROMPT: PromptDefinition = PromptDefinition(
    name="wiki-section-writer",
    system="""
你是一名资深技术写作者兼系统架构师，需要基于提供的文件上下文为既定的 wiki 章节撰写清晰、结构化的内容，并补充能够概括章节核心流程或组件关系的 Mermaid 图。

创作原则：
- 语气保持专业、客观，优先强调架构职责、依赖关系与关键配置。
- 结构层次清晰，概览在前，细节分节展开，必要时使用列表或引用提高可读性。
- Mermaid 图需覆盖章节最重要的流程或组件关系，做到节点命名明确，避免冗余装饰。
- 遇到缺失信息时，可结合章节标题和已有上下文做适度推断，但要保持技术合理性。
""",
    human="""
文档标题：{doc_title}
文档描述：{doc_description}
当前章节：{breadcrumb}
章节 ID：{section_id}

请基于下列文件内容生成该章节，严格遵循输出 JSON 的结构约束。

<OUTPUT_SCHEMA_HINT>
以 JSON 形式返回，字段要求：
{{
  "intro": "章节概览，聚焦于业务价值与关键组件",
  "sections": [
    {{"heading": "小节标题", "body": "成段描述，可使用列表与引用"}}
  ],
  "mermaid": "仅包含 Mermaid 代码，不要包裹 ```"
}}
</OUTPUT_SCHEMA_HINT>

==== 关联文件内容开始 ====
{context}
==== 关联文件内容结束 ====

请只返回 JSON 字符串，不要添加额外解释。
""",
)

PROMPT_REGISTRY: Dict[str, PromptDefinition] = {
    STRUCTURE_PROMPT.name: STRUCTURE_PROMPT,
    RAG_CHAT_PROMPT.name: RAG_CHAT_PROMPT,
    WIKI_SECTION_PROMPT.name: WIKI_SECTION_PROMPT,
}


def get_structure_prompt() -> ChatPromptTemplate:
    """
    获取多层级 wiki 目录生成提示词。
    """

    return STRUCTURE_PROMPT.build()


def get_rag_chat_prompt() -> ChatPromptTemplate:
    """
    获取用于 RAG 问答的提示词模板。
    """

    return RAG_CHAT_PROMPT.build()


def get_wiki_section_prompt() -> ChatPromptTemplate:
    """
    获取用于 wiki 章节内容生成的提示词模板。
    """

    return WIKI_SECTION_PROMPT.build()


__all__ = [
    "PromptDefinition",
    "STRUCTURE_PROMPT",
    "RAG_CHAT_PROMPT",
    "WIKI_SECTION_PROMPT",
    "PROMPT_REGISTRY",
    "get_structure_prompt",
    "get_rag_chat_prompt",
    "get_wiki_section_prompt",
]

