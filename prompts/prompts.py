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


PROMPT_REGISTRY: Dict[str, PromptDefinition] = {
    STRUCTURE_PROMPT.name: STRUCTURE_PROMPT,
    RAG_CHAT_PROMPT.name: RAG_CHAT_PROMPT,
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


__all__ = [
    "PromptDefinition",
    "STRUCTURE_PROMPT",
    "RAG_CHAT_PROMPT",
    "PROMPT_REGISTRY",
    "get_structure_prompt",
    "get_rag_chat_prompt",
]

