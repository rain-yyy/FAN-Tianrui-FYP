"""
Agent 状态定义模块

定义 LangGraph 状态机所需的状态结构，包含：
- 原始问题与对话历史
- 上下文便签本（累积收集的信息）
- 缺失信息追踪
- 工具调用历史
- 反思循环控制
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Literal
from enum import Enum


class ToolType(str, Enum):
    """可用工具类型"""
    RAG_SEARCH = "rag_search"
    CODE_GRAPH = "code_graph"
    FILE_READ = "file_read"
    REPO_MAP = "repo_map"


class QueryIntent(str, Enum):
    """查询意图分类"""
    CONCEPT = "concept"           # 概念理解（什么是X？）
    IMPLEMENTATION = "implementation"  # 实现细节（X如何工作？）
    ARCHITECTURE = "architecture"      # 架构关系（X和Y如何关联？）
    DEBUGGING = "debugging"           # 调试问题（为什么X出错？）
    USAGE = "usage"                   # 使用指南（如何使用X？）


@dataclass
class ContextPiece:
    """
    上下文片段，存储从各种工具收集的信息。
    """
    source: str                    # 来源（工具名称）
    content: str                   # 内容
    file_path: Optional[str] = None   # 关联文件路径
    line_range: Optional[tuple] = None  # 行范围 (start, end)
    relevance_score: float = 0.0   # 相关性分数
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "content": self.content,
            "file_path": self.file_path,
            "line_range": self.line_range,
            "relevance_score": self.relevance_score,
            "metadata": self.metadata,
        }


@dataclass
class ToolCall:
    """
    工具调用记录，用于追踪 Agent 的推理轨迹。
    """
    tool: ToolType
    arguments: Dict[str, Any]
    result: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool.value,
            "arguments": self.arguments,
            "result": self.result[:500] if self.result and len(self.result) > 500 else self.result,
            "success": self.success,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentState:
    """
    Agent 状态机的核心状态定义。
    
    这个状态在 LangGraph 的各个节点间传递，实现：
    - 规划 -> 检索 -> 反思 -> 再检索 的闭环
    """
    # ========== 输入 ==========
    original_question: str
    repo_url: str
    vector_store_path: str
    graph_path: Optional[str] = None
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    
    # ========== 规划阶段 ==========
    query_intent: Optional[QueryIntent] = None
    rewritten_queries: List[str] = field(default_factory=list)
    exploration_plan: List[str] = field(default_factory=list)
    
    # ========== 上下文收集 ==========
    context_scratchpad: List[ContextPiece] = field(default_factory=list)
    missing_pieces: List[str] = field(default_factory=list)
    
    # ========== 工具调用追踪 ==========
    tool_calls_history: List[ToolCall] = field(default_factory=list)
    current_tool_call: Optional[ToolCall] = None
    
    # ========== 反思循环控制 ==========
    iteration_count: int = 0
    max_iterations: int = 5
    is_ready: bool = False
    confidence_score: float = 0.0
    reflection_notes: List[str] = field(default_factory=list)
    
    # ========== 最终输出 ==========
    final_answer: Optional[str] = None
    mermaid_diagram: Optional[str] = None
    sources: List[str] = field(default_factory=list)
    
    # ========== 错误处理 ==========
    error: Optional[str] = None

    def add_context(self, piece: ContextPiece) -> None:
        """添加上下文片段"""
        self.context_scratchpad.append(piece)

    def add_tool_call(self, tool_call: ToolCall) -> None:
        """记录工具调用"""
        self.tool_calls_history.append(tool_call)

    def get_context_summary(self, max_length: int = 8000) -> str:
        """
        获取当前收集的上下文摘要，用于传递给 LLM。
        """
        if not self.context_scratchpad:
            return "尚未收集到任何上下文信息。"
        
        summaries = []
        total_length = 0
        
        for piece in sorted(self.context_scratchpad, 
                           key=lambda x: x.relevance_score, 
                           reverse=True):
            entry = f"[来源: {piece.source}]"
            if piece.file_path:
                entry += f" [文件: {piece.file_path}]"
            if piece.line_range:
                entry += f" [行 {piece.line_range[0]}-{piece.line_range[1]}]"
            entry += f"\n{piece.content}"
            
            if total_length + len(entry) > max_length:
                break
            
            summaries.append(entry)
            total_length += len(entry)
        
        return "\n\n---\n\n".join(summaries)

    def get_trajectory(self) -> List[Dict[str, Any]]:
        """
        获取 Agent 推理轨迹，用于前端展示。
        """
        trajectory = []
        for call in self.tool_calls_history:
            trajectory.append(call.to_dict())
        return trajectory

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典，用于序列化"""
        return {
            "original_question": self.original_question,
            "repo_url": self.repo_url,
            "query_intent": self.query_intent.value if self.query_intent else None,
            "iteration_count": self.iteration_count,
            "is_ready": self.is_ready,
            "confidence_score": self.confidence_score,
            "context_pieces_count": len(self.context_scratchpad),
            "tool_calls_count": len(self.tool_calls_history),
            "missing_pieces": self.missing_pieces,
            "reflection_notes": self.reflection_notes,
            "final_answer": self.final_answer,
            "mermaid_diagram": self.mermaid_diagram,
            "sources": self.sources,
            "error": self.error,
        }
