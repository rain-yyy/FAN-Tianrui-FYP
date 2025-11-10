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
你是资深技术文档作者与软件架构师。请根据给定的仓库上下文，规划一个多层级的技术文档目录（侧边栏导航）。

需要达到的目标：
- 目录层级与 DeepWiki 的 System Architecture 页面风格保持一致，包括顶层章节和缩进节点。
- 生成的目录能够帮助读者从宏观到微观理解系统（例如：Overview、System Architecture、Core Backend Services、Document Processing Pipeline、Data Models、Observability、Infrastructure 等）。
- 节点命名要清晰、专业、语义化；同一层级避免重复或模糊的标题。

输出格式（务必严格遵守）：
- 只输出单个合法 JSON 字符串。
- JSON 根对象必须包含键：`title`、`description`、`lastIndexed`、`toc`。
- `lastIndexed` 使用传入的 `current_date`，格式保持原样。
- `toc` 为数组，数组元素为对象，至少包含 `id` 与 `title` 字段，可选 `children`。
- 每个节点须提供 `files`（数组，元素为相对路径字符串）说明后续撰写该章节时最需要参考的源码/配置文件，建议 3~8 个；若暂时无法确定，可返回空数组。
- `children` 若存在必须是数组，数组内的节点递归遵循相同结构。

必须遵守以下约束：
- 只输出一个合法 JSON，不允许包含额外说明或标记。
- `id` 使用 kebab-case，语义清晰且稳定（可作为 URL/锚点）。
- `title` 为人类可读标题，首字母大写。
- 如存在 `children`，需为数组；没有子节点时可以省略该字段或设为空数组。
- 若仓库不包含某类功能，可以移除对应章节；如发现特定特性（AI、Pipelines、CI/CD 等），请增补对应章节。
- 根据传入的 `current_date` 写入 `lastIndexed`。

在设计目录时，重点关注：
- 核心后端服务、API 层、文档与知识库处理流程。
- 数据模型、配置、部署、可观测性、安全与治理等模块。
- 重要流程（例如仓库提交到文档生成）应当有独立章节。
""",
    human="""
            仓库上下文如下，请全面分析并输出符合要求的 JSON 目录（直接返回 JSON 字符串）：

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


PROMPT_REGISTRY: Dict[str, PromptDefinition] = {
    STRUCTURE_PROMPT.name: STRUCTURE_PROMPT,
}


def get_structure_prompt() -> ChatPromptTemplate:
    """
    获取多层级 wiki 目录生成提示词。
    """

    return STRUCTURE_PROMPT.build()


__all__ = [
    "PromptDefinition",
    "STRUCTURE_PROMPT",
    "PROMPT_REGISTRY",
    "get_structure_prompt",
]

