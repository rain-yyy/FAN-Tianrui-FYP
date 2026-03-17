"""
深度代码理解 Agent 模块

借鉴 Sourcegraph Cody 的 Agentic Context Fetching 和 DeepWiki 的结构化图谱理念，
实现具备反思循环（Reflection Loop）和多策略检索深度的智能代码理解 Agent 架构。

核心组件:
- AgentState: Agent 状态定义，包含锚点、证据卡片、置信度门控
- AgentGraphRunner: Agent 图执行器，管理完整生命周期
- AgentRunner: 高级执行器，支持异步和流式输出
- Tools: RAG、代码图谱、文件读取等工具集

工作流程:
1. Session Compressor: 压缩会话历史，提取关键实体
2. Query Planner: 分析问题意图，提取实体，制定探索计划
3. Tool Executor: 锚点优先检索 + 结构化扩展
4. Evaluator: 置信度门控的充分性评估
5. Synthesizer: 生成带证据卡片、Mermaid 图表和溯源的答案
"""

from src.agent.state import (
    AgentState,
    ToolCall,
    ContextPiece,
    ToolType,
    QueryIntent,
    Anchor,
    AnchorType,
    EvidenceCard,
    EvidenceType,
    ConfidenceLevel,
    EvaluationResult,
    PlannerOutput,
    SessionMemory,
    RepoFactsMemory,
)
from src.agent.graph import create_agent_graph, run_agent, AgentGraphRunner
from src.agent.runner import AgentRunner, AgentEvent, AgentSession
from src.agent.prompts import (
    QUERY_PLANNER_PROMPT,
    TOOL_ROUTER_PROMPT,
    EVALUATOR_PROMPT,
    ANSWER_SYNTHESIZER_PROMPT,
    SESSION_COMPRESSOR_PROMPT,
)

__all__ = [
    # State - Core
    "AgentState",
    "ToolCall", 
    "ContextPiece",
    "ToolType",
    "QueryIntent",
    # State - Anchors & Evidence
    "Anchor",
    "AnchorType",
    "EvidenceCard",
    "EvidenceType",
    "ConfidenceLevel",
    # State - Structured Outputs
    "EvaluationResult",
    "PlannerOutput",
    # State - Memory
    "SessionMemory",
    "RepoFactsMemory",
    # Graph
    "create_agent_graph",
    "run_agent",
    "AgentGraphRunner",
    # Runner
    "AgentRunner",
    "AgentEvent",
    "AgentSession",
    # Prompts
    "QUERY_PLANNER_PROMPT",
    "TOOL_ROUTER_PROMPT",
    "EVALUATOR_PROMPT",
    "ANSWER_SYNTHESIZER_PROMPT",
    "SESSION_COMPRESSOR_PROMPT",
]
