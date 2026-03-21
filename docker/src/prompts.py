from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Any
import json

# All LLM-facing product outputs (RAG, wiki, HyDE, structure) are fixed to English.
OUTPUT_LANGUAGE_EN = """
## Output language (strict)
- The product is **English-only**: write **every** user-visible sentence in **English**—no Chinese, Japanese, or other languages for explanations, titles, summaries, or bullet points.
- If the user writes in another language, **still respond in English**; keep code identifiers and string literals exactly as in the source.
"""

@dataclass(frozen=True)
class PromptDefinition:

    name: str
    system: str
    human: str

    def build(self):
        from langchain_core.prompts import ChatPromptTemplate
        return ChatPromptTemplate.from_messages(
            [
                ("system", self.system.strip()),
                ("human", self.human.strip()),
            ]
        )

    def format_messages(self, **kwargs) -> List[Dict[str, str]]:
        """
        格式化提示词为模型调用的消息列表。
        """

        # TODO 保存在本地，用于测试文件路径错误的问题（已修复）
        with open("structure_prompt.json", "w", encoding="utf-8") as f:
            json.dump(kwargs, f, indent=2, ensure_ascii=False)

        return [
            {"role": "system", "content": self.system.strip().format(**kwargs)},
            {"role": "user", "content": self.human.strip().format(**kwargs)},
        ]


STRUCTURE_PROMPT: PromptDefinition = PromptDefinition(
    name="structure-navigation",
    system="""
            You are a senior technical documentation writer and software architect. Based on the provided repository context, design a concise, high-value, multi-level technical documentation table of contents (sidebar navigation).

            Primary objective:
            - Produce a wiki structure that helps an engineer quickly understand what the project does, how it is organized, and how its core flows work.
            - Favor clarity, prioritization, and architectural signal over completeness for completeness' sake.
            - Compress the number of sections, NOT the breadth of evidence. Fewer sections should contain denser, broader, and more representative source coverage.
            - Name nodes clearly, professionally, and semantically; avoid duplicate or ambiguous titles at the same level.

            What "good" looks like:
            - The structure should read like an opinionated map of the repository's most important functionality.
            - It should cover the product's core user journeys, main architectural modules, key data/contracts, and the most important integrations or runtime flows.
            - It should NOT mirror the file tree or enumerate every similar feature, screen, component, helper, template, schema, doc page, or config file just because they exist.

            Output format (strictly follow):
            - Return a single valid JSON string.
            - The JSON root object must contain the keys: `title`, `description`, `lastIndexed`, `toc`.
            - `lastIndexed` must use the provided `current_date` without modifying the format.
            - `toc` is an array of objects; each object must include at least `id` and `title`, with optional `children`.
            - Each node must provide a `files` array listing all relative paths that are most relevant for drafting the section.
            - If `children` exists it must be an array, and each child must follow the same structure recursively.

            Mandatory constraints:
            - Output exactly one valid JSON string without extra commentary or markers.
            - Use kebab-case for every `id`; ensure they are meaningful and stable (suitable for URLs/anchors).
            - `title` must be human-readable with leading capital letters.
            - If a node has no children, omit the `children` field or set it to an empty array.
            - Remove sections for unsupported functionality in the repository; add sections only when they are clearly central to understanding the project.
            - Populate `lastIndexed` with the provided `current_date`.

            Prioritization rules:
            - Include only sections that a technically curious reader would reasonably care about in order to understand, extend, debug, or deploy the project.
            - Prefer concept-oriented sections over inventory-oriented sections.
            - Prefer grouping highly similar implementations into one section instead of creating one child per file or per subtype.
            - If many files differ only by content variant, UI variant, schema variant, locale, migration version, or other repetition, summarize them through one higher-level section and cite only the representative files.
            - Do NOT create sections solely because a directory exists.
            - Do NOT try to maximize coverage of all folders. Maximize usefulness.
            - Within each retained section, maximize relevant source coverage so the later writer has enough implementation evidence to produce precise and comprehensive documentation.

            Strong compression rules:
            - Target 5 to 8 top-level sections.
            - Prefer depth of 2 levels; use 3 levels only when it materially improves understanding.
            - Prefer 2 to 5 children for an important parent section.
            - Avoid singleton micro-sections unless the topic is genuinely important.
            - Reduce the number of nodes before reducing file coverage.
            - Each important node should usually reference 4 to 10 files.
            - For especially central sections (core architecture, main feature flow, data model, key integrations), 8 to 15 files is acceptable when they are all materially relevant.
            - Prefer dense sections with richer file evidence over sparse sections with only a few files.

            Explicitly ignore or collapse low-value detail unless the repository is primarily about that topic:
            - Atomic UI component catalogs, primitive component wrappers, visual effect components, and styling helpers.
            - Repetitive CRUD subpages or form sections that are structurally similar.
            - Long lists of templates, themes, locales, migrations, assets, generated files, legal pages, changelogs, marketing pages, or one-off docs.
            - Narrow utility functions, hooks, internal helper files, and small support modules.
            - Boilerplate configuration files, unless they are essential to runtime architecture or deployment.

            Prefer these section types when supported by the repository:
            - Project overview and product purpose.
            - Core architecture and request/runtime boundaries.
            - Primary business flows or pipelines.
            - Main application surfaces or core feature areas.
            - Data model / content model / domain contracts.
            - External integrations that materially affect how the system works.
            - Deployment / operations only if self-hosting, infrastructure, or runtime setup is a meaningful part of the product.

            **CRITICAL CONSTRAINT FOR `files` ARRAY**:
            - You are provided with a <VALID_FILE_LIST> containing ALL available file paths in this repository.
            - The `files` array in EVERY toc node MUST ONLY contain paths that EXACTLY match entries from <VALID_FILE_LIST>.
            - Do NOT invent, guess, or hallucinate file paths. If a path is not in <VALID_FILE_LIST>, it does NOT exist.
            - Prefer actual implementation files over prose docs. Use docs only when they are the canonical source for setup, architecture, or operations.
            - Do NOT rely on README-only sections when stronger implementation files exist.
            - Copy paths EXACTLY as they appear in <VALID_FILE_LIST>, including case sensitivity.

            Detailed Strategy for File Selection:
            - **Overview**: Prefer the smallest set of files that reveal the project's purpose, entry points, and primary runtime shape.
            - **Architecture/Services**: Include enough files to explain boundaries, orchestration, request handling, core services, persistence, and major cross-module interactions.
            - **Feature Areas**: Prefer one broad section per major feature area, but pack it with the key implementation files across UI entry points, service layer, shared state, dialogs/forms, and rendering/output paths when relevant.
            - **Data Models**: Include schema, DTO, sample data, and persistence definitions when they collectively explain the domain model.
            - **Repo Map Usage**: Use the provided Repo Map to identify which files contain the most important classes and function definitions. Cross-reference with <VALID_FILE_LIST> to ensure paths are valid.
            - **Community Logic (GraphRAG)**: Use the <COMMUNITIES> section to identify major subsystems, but merge or discard clusters that are low-value, repetitive, or implementation-noisy.
            - **Sparse README Handling**: If the README is short or missing, rely on the file tree, communities, repo map, and naming conventions to infer the highest-value documentation structure.
            - **Source Preference**: Prefer source code and architecture-defining config over prose docs whenever possible. Use docs as supplements, not substitutes, unless the docs are the canonical implementation guide for setup or operations.
            - **End-to-End Coverage**: For a core product capability, include files from multiple layers when available, such as route/page entry points, service or API layer, schemas/contracts, state management, and rendering/export/integration files.

            Focus on the following when designing the table of contents:
            - The central user-facing capabilities or core business functionality.
            - The main execution paths, data flow, and module boundaries.
            - The architectural pieces that explain how the system actually works.

            """ + OUTPUT_LANGUAGE_EN.strip() + """

            Final quality filter before you answer:
            - Ask of every candidate node: "Would most engineers care about this when trying to understand the project?"
            - If the answer is no, omit it.
            - If a topic is merely implementation detail, fold it into a broader parent section instead of giving it a dedicated node.
            - Then ask: "Does this retained node contain enough source evidence for a later writer to explain the topic accurately and comprehensively?"
            - If not, add more materially relevant files instead of adding more sections.
            """,
    human="""
            The repository context is provided below. Analyze it thoroughly and return a JSON table of contents that satisfies all requirements (respond with the JSON string directly):

            <CURRENT_DATE>
            {current_date}
            </CURRENT_DATE>

            <VALID_FILE_LIST>
            {valid_file_list}
            </VALID_FILE_LIST>

            <FILE_TREE>
            {file_tree}
            </FILE_TREE>

            <README>
            {readme_content}
            </README>

            <REPO_MAP>
            {repo_map}
            </REPO_MAP>

            <COMMUNITIES>
            {communities}
            </COMMUNITIES>
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
"""
    + OUTPUT_LANGUAGE_EN,
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


HYDE_PROMPT: PromptDefinition = PromptDefinition(
    name="hyde-generator",
    system="""
You are a technical documentation expert. Your task is to generate a hypothetical answer document that would be relevant to the given question about a software codebase.

This hypothetical document will be used to improve semantic search retrieval. Generate content that:
1. Directly addresses the technical question
2. Uses terminology and concepts likely found in actual code documentation
3. Includes relevant technical details, function names, and architectural concepts
4. Is written as if it were part of the actual codebase documentation

Keep the response focused and technical. Do not include disclaimers or meta-commentary.
"""
    + OUTPUT_LANGUAGE_EN,
    human="""
Generate a hypothetical technical documentation snippet that would answer this question:

{question}

Write the documentation directly, as if it exists in the codebase.
""",
)


RAG_CHAT_WITH_HISTORY_PROMPT: PromptDefinition = PromptDefinition(
    name="rag-chat-with-history",
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
- **Context-aware responses**: Consider the conversation history when formulating your answer. Build upon previous exchanges naturally.

## Response Requirements
Provide the best possible answer directly, without listing evidence sources or follow-up suggestions. Focus on:
- Accurately answering the user's question in the context of the ongoing conversation
- Providing actionable technical insights
- Referencing previous discussion points when relevant
- Highlighting potential risks or considerations (when applicable)
"""
    + OUTPUT_LANGUAGE_EN,
    human="""
<RETRIEVED_CONTEXT>
{context}
</RETRIEVED_CONTEXT>

<CONVERSATION_HISTORY>
{conversation_history}
</CONVERSATION_HISTORY>

<CURRENT_QUESTION>
{question}
</CURRENT_QUESTION>

Please provide your answer directly, considering the conversation context.
""",
)

WIKI_SECTION_PROMPT: PromptDefinition = PromptDefinition(
    name="wiki-section-writer",
    system="""
You are a senior technical writer and software architect. Draft a clear, structured wiki section from the supplied file context, and add a Mermaid diagram that summarizes the most important flows or component relationships for this section.

Writing principles:
- Professional, objective tone; emphasize architectural responsibilities, dependencies, and key configuration.
- Clear hierarchy: overview first, then subsections; use lists or short quotes where they improve readability.
- The Mermaid diagram should cover the section's most important flows or relationships; node names must be explicit; avoid decorative clutter.
- When information is missing, you may infer modestly from the section title and context, but keep claims technically plausible.
- **Mermaid syntax**:
  * If a node label contains special characters (e.g. @, #, $, &), wrap the label in double quotes, e.g. `A["user@example.com"]`, not `A[user@example.com]`.
  * Node IDs may only use letters, digits, and underscores.
  * Ensure valid Mermaid so parsers do not fail.
"""
    + OUTPUT_LANGUAGE_EN,
    human="""
Document title: {doc_title}
Document description: {doc_description}
Current section (breadcrumb): {breadcrumb}
Section id: {section_id}

Generate this section from the file content below. Follow the JSON output schema exactly.

<OUTPUT_SCHEMA_HINT>
Return a single JSON object with:
{{
  "intro": "Section overview: business value and key components (English prose)",
  "sections": [
    {{"heading": "Subsection title", "body": "Paragraphs; lists and short quotes allowed"}}
  ],
  "mermaid": "Raw Mermaid only, no ``` fences. Quote labels with special characters, e.g. A[\"text@domain.com\"]"
}}
</OUTPUT_SCHEMA_HINT>

==== Source file content (start) ====
{context}
==== Source file content (end) ====

Return only the JSON string, with no extra commentary.
""",
)

PROMPT_REGISTRY: Dict[str, PromptDefinition] = {
    STRUCTURE_PROMPT.name: STRUCTURE_PROMPT,
    RAG_CHAT_PROMPT.name: RAG_CHAT_PROMPT,
    WIKI_SECTION_PROMPT.name: WIKI_SECTION_PROMPT,
    HYDE_PROMPT.name: HYDE_PROMPT,
    RAG_CHAT_WITH_HISTORY_PROMPT.name: RAG_CHAT_WITH_HISTORY_PROMPT,
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


def get_hyde_prompt() -> ChatPromptTemplate:
    """
    获取用于 HyDE 假设文档生成的提示词模板。
    """

    return HYDE_PROMPT.build()


def get_rag_chat_with_history_prompt() -> ChatPromptTemplate:
    """
    获取用于带对话历史的 RAG 问答的提示词模板。
    """

    return RAG_CHAT_WITH_HISTORY_PROMPT.build()


__all__ = [
    "PromptDefinition",
    "OUTPUT_LANGUAGE_EN",
    "STRUCTURE_PROMPT",
    "RAG_CHAT_PROMPT",
    "WIKI_SECTION_PROMPT",
    "HYDE_PROMPT",
    "RAG_CHAT_WITH_HISTORY_PROMPT",
    "PROMPT_REGISTRY",
    "get_structure_prompt",
    "get_rag_chat_prompt",
    "get_wiki_section_prompt",
    "get_hyde_prompt",
    "get_rag_chat_with_history_prompt",
]

