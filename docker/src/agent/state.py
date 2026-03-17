"""
Agent 状态定义模块

定义 Agent 状态机所需的状态结构，包含：
- 原始问题与对话历史
- 锚点与证据卡片模型
- 上下文便签本（累积收集的信息）
- 缺失信息追踪
- 工具调用历史
- 反思循环控制与置信度门控
- 会话记忆分层
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
    """
    查询意图分类（扩展版）
    
    对齐 agentplan.md 的问题分类：
    - location: 定位型（"X在哪里？"）
    - mechanism: 机制型（"X如何工作？"）
    - call_chain: 调用链/数据流（"请求如何流转？"）
    - impact_analysis: 影响分析（"修改X会影响什么？"）
    - debugging: 调试型（"为什么X出错？"）
    - architecture: 架构型（"项目架构是什么？"）
    - change_guidance: 修改指导（"如何修改X？"）
    - concept: 概念理解（"什么是X？"）
    - usage: 使用指南（"如何使用X？"）
    - general: 通用问答（不需要代码检索即可回答）
    """
    LOCATION = "location"
    MECHANISM = "mechanism"
    CALL_CHAIN = "call_chain"
    IMPACT_ANALYSIS = "impact_analysis"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    CHANGE_GUIDANCE = "change_guidance"
    CONCEPT = "concept"
    USAGE = "usage"
    GENERAL = "general"
    # 兼容旧版本
    IMPLEMENTATION = "implementation"


class AnchorType(str, Enum):
    """
    锚点类型
    
    锚点是检索的起始点，而非直接的答案素材。
    """
    DEFINITION = "definition"           # 符号定义位置
    ENTRYPOINT = "entrypoint"           # 执行入口点
    ROUTE_BINDING = "route_binding"     # 路由/API绑定点
    CONFIG_BINDING = "config_binding"   # 配置绑定点
    ERROR_SITE = "error_site"           # 错误发生位置
    PUBLIC_INTERFACE = "public_interface"  # 公共接口/导出
    TEST_FILE = "test_file"             # 测试文件
    REFERENCE = "reference"             # 引用点


class EvidenceType(str, Enum):
    """
    证据类型（按权威性排序）
    
    用于证据卡片的分类和优先级排序。
    """
    DEFINITION = "definition"           # 定义（最权威）
    DIRECT_CALL = "direct_call"         # 直接调用关系
    ROUTE_CONFIG = "route_config"       # 路由/框架配置
    TEST_ASSERTION = "test_assertion"   # 测试断言
    DOCUMENTATION = "documentation"     # 文档/注释
    SEMANTIC_MATCH = "semantic_match"   # 语义匹配（最低）


class ConfidenceLevel(str, Enum):
    """
    置信度级别
    
    用于标记结论的可靠程度。
    """
    CONFIRMED = "confirmed"   # 有代码证据支撑
    LIKELY = "likely"         # 有间接证据，但未完全验证
    UNKNOWN = "unknown"       # 无法确定


@dataclass
class Anchor:
    """
    锚点
    
    检索的起始点，用于后续结构化扩展。
    """
    anchor_type: AnchorType
    symbol_name: str
    file_path: str
    line_number: Optional[int] = None
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "anchor_type": self.anchor_type.value,
            "symbol_name": self.symbol_name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@dataclass
class EvidenceCard:
    """
    证据卡片
    
    统一的证据模型，所有进入答案合成阶段的上下文都要先变成证据卡片。
    """
    file_path: str
    symbol: Optional[str]
    span: tuple  # (start_line, end_line)
    evidence_type: EvidenceType
    content: str
    confidence: ConfidenceLevel
    why_it_matters: str
    source_tool: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "symbol": self.symbol,
            "span": self.span,
            "evidence_type": self.evidence_type.value,
            "content": self.content[:500] if len(self.content) > 500 else self.content,
            "confidence": self.confidence.value,
            "why_it_matters": self.why_it_matters,
            "source_tool": self.source_tool,
        }
    
    def get_citation(self) -> str:
        """生成引用格式"""
        if self.span and self.span[0] and self.span[1]:
            return f"{self.file_path}:{self.span[0]}-{self.span[1]}"
        return self.file_path


@dataclass
class ContextPiece:
    """
    上下文片段，存储从各种工具收集的信息。
    
    这是原始检索结果，会被转换为 EvidenceCard。
    """
    source: str
    content: str
    file_path: Optional[str] = None
    line_range: Optional[tuple] = None
    relevance_score: float = 0.0
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
    
    def to_evidence_card(
        self,
        evidence_type: EvidenceType = EvidenceType.SEMANTIC_MATCH,
        confidence: ConfidenceLevel = ConfidenceLevel.LIKELY,
        why_it_matters: str = "",
        symbol: Optional[str] = None,
    ) -> EvidenceCard:
        """转换为证据卡片"""
        return EvidenceCard(
            file_path=self.file_path or "unknown",
            symbol=symbol or self.metadata.get("symbol"),
            span=self.line_range or (None, None),
            evidence_type=evidence_type,
            content=self.content,
            confidence=confidence,
            why_it_matters=why_it_matters or f"Retrieved via {self.source}",
            source_tool=self.source,
            metadata=self.metadata,
        )


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
class SessionMemory:
    """
    会话记忆
    
    短期记忆，用于当前会话的上下文压缩。
    """
    recent_turns: List[Dict[str, str]] = field(default_factory=list)
    session_summary: str = ""
    key_entities: List[str] = field(default_factory=list)
    user_preferences: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "recent_turns_count": len(self.recent_turns),
            "session_summary": self.session_summary[:200] if self.session_summary else "",
            "key_entities": self.key_entities[:10],
            "user_preferences": self.user_preferences,
        }


@dataclass
class RepoFactsMemory:
    """
    仓库事实记忆
    
    长期记忆，存储已验证的仓库稳定事实。
    """
    module_responsibilities: Dict[str, str] = field(default_factory=dict)
    key_entrypoints: List[str] = field(default_factory=list)
    core_constraints: List[str] = field(default_factory=list)
    tech_stack: Dict[str, str] = field(default_factory=dict)
    failed_approaches: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_responsibilities": self.module_responsibilities,
            "key_entrypoints": self.key_entrypoints,
            "core_constraints": self.core_constraints,
            "tech_stack": self.tech_stack,
            "failed_approaches": self.failed_approaches[-5:],
        }


@dataclass
class EvaluationResult:
    """
    评估结果
    
    Evaluator 的结构化输出，用于置信度门控。
    """
    is_ready: bool
    confidence_score: float
    confidence_level: ConfidenceLevel
    has_primary_anchor: bool
    has_closed_path: bool
    has_conflicts: bool
    missing_pieces: List[str]
    reflection_notes: List[str]
    suggested_next_step: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_ready": self.is_ready,
            "confidence_score": self.confidence_score,
            "confidence_level": self.confidence_level.value,
            "has_primary_anchor": self.has_primary_anchor,
            "has_closed_path": self.has_closed_path,
            "has_conflicts": self.has_conflicts,
            "missing_pieces": self.missing_pieces,
            "reflection_notes": self.reflection_notes,
            "suggested_next_step": self.suggested_next_step,
        }


@dataclass 
class PlannerOutput:
    """
    Planner 的结构化输出
    """
    intent: QueryIntent
    entities: List[str]
    constraints: List[str]
    expected_evidence_types: List[EvidenceType]
    stop_conditions: List[str]
    rewritten_queries: List[str]
    exploration_plan: List[str]
    initial_tools: List[Dict[str, Any]]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "entities": self.entities,
            "constraints": self.constraints,
            "expected_evidence_types": [e.value for e in self.expected_evidence_types],
            "stop_conditions": self.stop_conditions,
            "rewritten_queries": self.rewritten_queries,
            "exploration_plan": self.exploration_plan,
            "initial_tools": self.initial_tools,
        }


@dataclass
class AgentState:
    """
    Agent 状态机的核心状态定义。
    
    这个状态在各个节点间传递，实现：
    - 规划 -> 锚点检索 -> 结构化扩展 -> 反思 -> 合成答案
    """
    # ========== 输入 ==========
    original_question: str
    repo_url: str
    vector_store_path: str
    graph_path: Optional[str] = None
    repo_root: Optional[str] = None
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    
    # ========== 会话记忆 ==========
    session_memory: SessionMemory = field(default_factory=SessionMemory)
    repo_facts_memory: RepoFactsMemory = field(default_factory=RepoFactsMemory)
    
    # ========== 规划阶段 ==========
    query_intent: Optional[QueryIntent] = None
    entities: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    expected_evidence_types: List[EvidenceType] = field(default_factory=list)
    stop_conditions: List[str] = field(default_factory=list)
    rewritten_queries: List[str] = field(default_factory=list)
    exploration_plan: List[str] = field(default_factory=list)
    
    # ========== 锚点与证据 ==========
    anchors: List[Anchor] = field(default_factory=list)
    evidence_cards: List[EvidenceCard] = field(default_factory=list)
    
    # ========== 上下文收集（原始） ==========
    context_scratchpad: List[ContextPiece] = field(default_factory=list)
    missing_pieces: List[str] = field(default_factory=list)
    
    # ========== 工具调用追踪 ==========
    tool_calls_history: List[ToolCall] = field(default_factory=list)
    current_tool_call: Optional[ToolCall] = None
    
    # ========== 反思循环控制 ==========
    iteration_count: int = 0
    max_iterations: int = 5
    is_ready: bool = False
    skip_tools: bool = False  # 如果为True，跳过工具调用直接返回答案
    confidence_score: float = 0.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    reflection_notes: List[str] = field(default_factory=list)
    
    # ========== 置信度门控状态 ==========
    has_primary_anchor: bool = False
    has_closed_path: bool = False
    has_conflicts: bool = False
    conflict_details: List[str] = field(default_factory=list)
    
    # ========== 最终输出 ==========
    final_answer: Optional[str] = None
    mermaid_diagram: Optional[str] = None
    sources: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)
    
    # ========== 错误处理 ==========
    error: Optional[str] = None

    def add_context(self, piece: ContextPiece) -> None:
        """添加上下文片段"""
        self.context_scratchpad.append(piece)
    
    def add_anchor(self, anchor: Anchor) -> None:
        """添加锚点"""
        self.anchors.append(anchor)
        if anchor.confidence > 0.7:
            self.has_primary_anchor = True
    
    def add_evidence(self, evidence: EvidenceCard) -> None:
        """添加证据卡片"""
        self.evidence_cards.append(evidence)

    def add_tool_call(self, tool_call: ToolCall) -> None:
        """记录工具调用"""
        self.tool_calls_history.append(tool_call)
    
    def apply_planner_output(self, output: PlannerOutput) -> None:
        """应用 Planner 输出"""
        self.query_intent = output.intent
        self.entities = output.entities
        self.constraints = output.constraints
        self.expected_evidence_types = output.expected_evidence_types
        self.stop_conditions = output.stop_conditions
        self.rewritten_queries = output.rewritten_queries
        self.exploration_plan = output.exploration_plan
        if output.initial_tools:
            for tool_info in output.initial_tools[:2]:
                self.missing_pieces.append(
                    f"Use {tool_info.get('tool', 'rag_search')}: {tool_info.get('reason', 'gather initial context')}"
                )
    
    def apply_evaluation(self, result: EvaluationResult) -> None:
        """应用评估结果"""
        self.is_ready = result.is_ready
        self.confidence_score = result.confidence_score
        self.confidence_level = result.confidence_level
        self.has_primary_anchor = result.has_primary_anchor
        self.has_closed_path = result.has_closed_path
        self.has_conflicts = result.has_conflicts
        self.missing_pieces = result.missing_pieces
        self.reflection_notes.extend(result.reflection_notes)
    
    def convert_context_to_evidence(self) -> None:
        """将所有上下文片段转换为证据卡片"""
        for piece in self.context_scratchpad:
            if piece.relevance_score > 0.3:
                evidence_type = self._infer_evidence_type(piece)
                confidence = self._infer_confidence(piece)
                evidence = piece.to_evidence_card(
                    evidence_type=evidence_type,
                    confidence=confidence,
                    why_it_matters=f"Relevance: {piece.relevance_score:.2f}",
                )
                self.evidence_cards.append(evidence)
    
    def _infer_evidence_type(self, piece: ContextPiece) -> EvidenceType:
        """推断证据类型"""
        source = piece.source.lower()
        if "definition" in source or "find_definition" in source:
            return EvidenceType.DEFINITION
        elif "caller" in source or "callee" in source or "call" in source:
            return EvidenceType.DIRECT_CALL
        elif "file_read" in source:
            return EvidenceType.DEFINITION
        elif "rag" in source:
            return EvidenceType.SEMANTIC_MATCH
        elif "graph" in source:
            return EvidenceType.DIRECT_CALL
        return EvidenceType.SEMANTIC_MATCH
    
    def _infer_confidence(self, piece: ContextPiece) -> ConfidenceLevel:
        """推断置信度"""
        if piece.relevance_score > 0.8:
            return ConfidenceLevel.CONFIRMED
        elif piece.relevance_score > 0.5:
            return ConfidenceLevel.LIKELY
        return ConfidenceLevel.UNKNOWN

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
    
    def get_evidence_summary(self, max_length: int = 8000) -> str:
        """
        获取证据卡片摘要，用于答案合成。
        """
        if not self.evidence_cards:
            return "尚未收集到任何证据。"
        
        summaries = []
        total_length = 0
        
        # 按证据类型权重排序
        type_weights = {
            EvidenceType.DEFINITION: 5,
            EvidenceType.DIRECT_CALL: 4,
            EvidenceType.ROUTE_CONFIG: 3,
            EvidenceType.TEST_ASSERTION: 2,
            EvidenceType.DOCUMENTATION: 1,
            EvidenceType.SEMANTIC_MATCH: 0,
        }
        
        sorted_evidence = sorted(
            self.evidence_cards,
            key=lambda x: type_weights.get(x.evidence_type, 0),
            reverse=True
        )
        
        for evidence in sorted_evidence:
            entry = f"[{evidence.evidence_type.value}] [{evidence.confidence.value}]\n"
            entry += f"文件: {evidence.get_citation()}\n"
            if evidence.symbol:
                entry += f"符号: {evidence.symbol}\n"
            entry += f"说明: {evidence.why_it_matters}\n"
            entry += f"内容:\n{evidence.content}"
            
            if total_length + len(entry) > max_length:
                break
            
            summaries.append(entry)
            total_length += len(entry)
        
        return "\n\n---\n\n".join(summaries)
    
    def get_anchors_summary(self) -> str:
        """获取锚点摘要"""
        if not self.anchors:
            return "尚未发现锚点。"
        
        lines = [f"已发现 {len(self.anchors)} 个锚点:"]
        for anchor in self.anchors:
            loc = f"{anchor.file_path}"
            if anchor.line_number:
                loc += f":{anchor.line_number}"
            lines.append(f"  - [{anchor.anchor_type.value}] {anchor.symbol_name} @ {loc}")
        return "\n".join(lines)

    def get_trajectory(self) -> List[Dict[str, Any]]:
        """
        获取 Agent 推理轨迹，用于前端展示。
        """
        trajectory = []
        for call in self.tool_calls_history:
            trajectory.append(call.to_dict())
        return trajectory
    
    def get_compressed_history(self, max_turns: int = 4) -> str:
        """
        获取压缩后的对话历史
        """
        if self.session_memory.session_summary:
            summary = f"[会话摘要] {self.session_memory.session_summary}\n\n"
        else:
            summary = ""
        
        recent = self.session_memory.recent_turns[-max_turns:] if self.session_memory.recent_turns else self.conversation_history[-max_turns:]
        
        if not recent:
            return summary + "无对话历史"
        
        parts = []
        for msg in recent:
            role = "用户" if msg.get("role") == "user" else "助手"
            content = msg.get("content", "")
            if len(content) > 300:
                content = content[:300] + "..."
            parts.append(f"{role}: {content}")
        
        return summary + "\n".join(parts)
    
    def check_stop_conditions(self) -> bool:
        """
        检查是否满足停止条件
        
        基于硬门控的非 LLM 判断。
        """
        # 至少有一个主锚点
        if not self.has_primary_anchor and self.iteration_count < 2:
            return False
        
        # 对于调用链/架构问题，需要闭合路径
        if self.query_intent in [QueryIntent.CALL_CHAIN, QueryIntent.ARCHITECTURE]:
            if not self.has_closed_path and self.iteration_count < 3:
                return False
        
        # 如果有冲突证据，需要继续收集
        if self.has_conflicts and self.iteration_count < 3:
            return False
        
        # 证据卡片数量门控
        definition_count = sum(1 for e in self.evidence_cards if e.evidence_type == EvidenceType.DEFINITION)
        if definition_count == 0 and self.iteration_count < 2:
            return False
        
        # 置信度门控
        if self.confidence_score < 0.6 and self.iteration_count < 3:
            return False
        
        return True

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典，用于序列化"""
        return {
            "original_question": self.original_question,
            "repo_url": self.repo_url,
            "query_intent": self.query_intent.value if self.query_intent else None,
            "entities": self.entities,
            "iteration_count": self.iteration_count,
            "is_ready": self.is_ready,
            "confidence_score": self.confidence_score,
            "confidence_level": self.confidence_level.value,
            "has_primary_anchor": self.has_primary_anchor,
            "has_closed_path": self.has_closed_path,
            "has_conflicts": self.has_conflicts,
            "anchors_count": len(self.anchors),
            "evidence_cards_count": len(self.evidence_cards),
            "context_pieces_count": len(self.context_scratchpad),
            "tool_calls_count": len(self.tool_calls_history),
            "missing_pieces": self.missing_pieces,
            "reflection_notes": self.reflection_notes,
            "final_answer": self.final_answer,
            "mermaid_diagram": self.mermaid_diagram,
            "sources": self.sources,
            "caveats": self.caveats,
            "error": self.error,
        }
