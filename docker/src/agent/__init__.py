"""
深度代码理解 Agent 模块

借鉴 Sourcegraph Cody 的 Agentic Context Fetching 和 DeepWiki 的结构化图谱理念，
实现具备反思循环（Reflection Loop）和多策略检索深度的智能代码理解 Agent 架构。

核心组件:
- AgentState: Agent 状态定义，用于 LangGraph 状态机
- AgentGraphRunner: Agent 图执行器，管理完整生命周期
- AgentRunner: 高级执行器，支持异步和流式输出
- Tools: RAG、代码图谱、文件读取等工具集

工作流程:
1. Query Planner: 分析问题意图，制定探索计划
2. Tool Executor: 迭代式调用工具收集上下文
3. Evaluator: 反思评估信息充分性
4. Synthesizer: 生成带 Mermaid 图表和溯源的答案
"""

from src.agent.state import AgentState, ToolCall, ContextPiece, ToolType, QueryIntent
from src.agent.graph import create_agent_graph, run_agent, AgentGraphRunner
from src.agent.runner import AgentRunner, AgentEvent, AgentSession
from src.agent.prompts import (
    QUERY_PLANNER_PROMPT,
    TOOL_ROUTER_PROMPT,
    EVALUATOR_PROMPT,
    ANSWER_SYNTHESIZER_PROMPT,
)

__all__ = [
    # State
    "AgentState",
    "ToolCall", 
    "ContextPiece",
    "ToolType",
    "QueryIntent",
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
]
