"""
Agent 状态机定义

实现 Agent 的核心工作流：
会话装载 → 问题分类/规划 → 锚点检索 → 结构化扩展 → 评估门控 → 合成答案

核心改进：
- 锚点优先检索（Anchor-first retrieval）
- 证据卡片模型（Evidence card model）
- 置信度门控（Confidence gating）
- 上下文压缩（Context compaction）
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Callable

from src.agent.state import (
    AgentState, 
    ContextPiece, 
    ToolCall, 
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
from src.agent.prompts import (
    get_planner_prompt,
    get_tool_router_prompt,
    get_evaluator_prompt,
    get_synthesizer_prompt,
    get_session_compressor_prompt,
)
from src.agent.tools import (
    RAGSearchTool,
    CodeGraphTool,
    FileReadTool,
    RepoMapTool,
)
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG

logger = logging.getLogger("app.agent.graph")


# Intent to tool strategy mapping
# 优化：所有意图都优先使用结构化工具（repo_map / code_graph），rag_search 作为补充
INTENT_TOOL_STRATEGIES: Dict[QueryIntent, List[str]] = {
    QueryIntent.LOCATION: ["code_graph", "file_read"],
    QueryIntent.MECHANISM: ["code_graph", "file_read", "rag_search"],
    QueryIntent.CALL_CHAIN: ["code_graph", "file_read"],
    QueryIntent.IMPACT_ANALYSIS: ["code_graph", "rag_search"],
    QueryIntent.DEBUGGING: ["code_graph", "file_read", "rag_search"],
    QueryIntent.ARCHITECTURE: ["repo_map", "code_graph"],
    QueryIntent.CHANGE_GUIDANCE: ["code_graph", "file_read", "rag_search"],
    QueryIntent.CONCEPT: ["repo_map", "code_graph", "rag_search"],
    QueryIntent.USAGE: ["code_graph", "rag_search", "file_read"],
    QueryIntent.IMPLEMENTATION: ["code_graph", "file_read", "rag_search"],
}

# 不强制要求 Definition 锚点的意图类型
SOFT_ANCHOR_INTENTS = {QueryIntent.CONCEPT, QueryIntent.ARCHITECTURE, QueryIntent.USAGE}


class AgentGraphRunner:
    """
    Agent 图执行器
    
    管理 Agent 的完整生命周期，包括：
    - 会话装载与上下文压缩
    - 规划与实体提取
    - 锚点检索与结构化扩展
    - 置信度门控的迭代控制
    - 证据卡片合成
    """
    
    def __init__(
        self,
        vector_store_path: str,
        graph_path: Optional[str] = None,
        repo_root: Optional[str] = None,
        max_iterations: int = 5,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.vector_store_path = vector_store_path
        self.graph_path = graph_path
        self.repo_root = repo_root
        self.max_iterations = max_iterations
        self.on_event = on_event
        
        self.rag_tool = RAGSearchTool(vector_store_path)
        self.graph_tool = CodeGraphTool(graph_path) if graph_path else None
        self.file_tool = FileReadTool(repo_root) if repo_root else None
        self.repo_map_tool = RepoMapTool(repo_root) if repo_root else None
        
        provider, model = get_model_config(CONFIG, "rag_answer")
        self.llm = get_ai_client(provider, model=model)

    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """向外部回调发送执行事件（用于流式前端反馈）。"""
        if not self.on_event:
            return
        try:
            self.on_event(event_type, data)
        except Exception as e:
            logger.warning(f"Failed to emit event '{event_type}': {e}")
    
    def run(
        self,
        question: str,
        repo_url: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        执行完整的 Agent 流程
        """
        state = AgentState(
            original_question=question,
            repo_url=repo_url,
            vector_store_path=self.vector_store_path,
            graph_path=self.graph_path,
            repo_root=self.repo_root,
            conversation_history=conversation_history or [],
            max_iterations=self.max_iterations,
        )
        
        try:
            # Phase 1: 会话装载与压缩
            state = self._compress_session(state)
            
            # Phase 2: 规划（AI判断是否需要工具）
            state = self._node_planner(state)
            
            # 快速路径：如果不需要工具，直接返回
            if state.skip_tools and state.final_answer:
                logger.info("[FastPath] Direct answer without tools")
                return {
                    "answer": state.final_answer,
                    "mermaid": None,
                    "sources": [],
                    "trajectory": [],
                    "confidence": 1.0,
                    "confidence_level": "confirmed",
                    "iterations": 0,
                    "anchors_count": 0,
                    "evidence_count": 0,
                    "caveats": [],
                    "error": None,
                }
            
            # Phase 3: 迭代式检索与评估（受控循环）
            while not state.is_ready and state.iteration_count < state.max_iterations:
                state = self._node_tool_executor(state)
                state = self._node_evaluator(state)
                state.iteration_count += 1
                
                # 硬门控检查
                if state.check_stop_conditions():
                    logger.info("[HardGate] Stop conditions met, proceeding to synthesis")
                    state.is_ready = True
                    break
            
            # Phase 4: 证据卡片转换
            state.convert_context_to_evidence()
            
            # Phase 5: 答案合成
            state = self._node_synthesizer(state)
            
            # Phase 6: 记忆回写
            state = self._writeback_memory(state)
            
        except Exception as e:
            logger.exception("Agent execution failed")
            state.error = str(e)
            state.final_answer = f"抱歉，在分析过程中遇到了错误：{str(e)}"
        
        return {
            "answer": state.final_answer or "",
            "mermaid": state.mermaid_diagram,
            "sources": state.sources,
            "trajectory": state.get_trajectory(),
            "confidence": state.confidence_score,
            "confidence_level": state.confidence_level.value,
            "iterations": state.iteration_count,
            "anchors_count": len(state.anchors),
            "evidence_count": len(state.evidence_cards),
            "caveats": state.caveats,
            "error": state.error,
        }
    
    def _compress_session(self, state: AgentState) -> AgentState:
        """
        压缩会话历史，提取关键实体和摘要
        """
        if not state.conversation_history or len(state.conversation_history) <= 4:
            state.session_memory.recent_turns = state.conversation_history[-4:]
            return state
        
        logger.info("[SessionCompressor] Compressing conversation history")
        
        try:
            prompt = get_session_compressor_prompt()
            history_text = self._format_history(state.conversation_history)
            
            messages = prompt.format_messages(
                conversation_history=history_text,
                question=state.original_question
            )
            
            response = self.llm.chat(messages, temperature=0.1, max_tokens=500)
            result = self._parse_json_response(response)
            
            if result:
                state.session_memory.session_summary = result.get("session_summary", "")
                state.session_memory.key_entities = result.get("key_entities", [])
                state.session_memory.user_preferences = result.get("user_preferences", {})
            
            state.session_memory.recent_turns = state.conversation_history[-4:]
            
        except Exception as e:
            logger.warning(f"[SessionCompressor] Failed: {e}")
            state.session_memory.recent_turns = state.conversation_history[-4:]
        
        return state
    
    def _node_planner(self, state: AgentState) -> AgentState:
        """
        规划节点：分析问题意图、判断是否需要工具、提取实体、制定探索计划
        """
        logger.info(f"[Planner] Analyzing question: {state.original_question[:100]}...")
        self._emit_event("planning", {"status": "analyzing", "question": state.original_question[:200]})
        
        prompt = get_planner_prompt()
        history_text = state.get_compressed_history()
        repo_facts = json.dumps(state.repo_facts_memory.to_dict(), ensure_ascii=False)
        
        messages = prompt.format_messages(
            question=state.original_question,
            conversation_history=history_text or "无对话历史",
            repo_facts=repo_facts or "{}"
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.2)
            plan_data = self._parse_json_response(response)
            
            if plan_data:
                # 检查是否需要工具
                requires_tools = plan_data.get("requires_tools", True)
                direct_answer = plan_data.get("direct_answer")
                
                if not requires_tools and direct_answer:
                    # 快速路径：不需要工具，直接使用 LLM 生成的答案
                    logger.info("[Planner] Fast path - no tools needed")
                    state.skip_tools = True
                    state.final_answer = direct_answer
                    state.query_intent = self._parse_intent(plan_data.get("intent", "general"))
                    self._emit_event(
                        "planning",
                        {
                            "status": "direct_answer",
                            "intent": state.query_intent.value,
                        },
                    )
                    return state
                
                # 需要工具的正常流程
                intent_str = plan_data.get("intent", "implementation")
                intent = self._parse_intent(intent_str)
                
                planner_output = PlannerOutput(
                    intent=intent,
                    entities=plan_data.get("entities", []),
                    constraints=plan_data.get("constraints", []),
                    expected_evidence_types=self._parse_evidence_types(plan_data.get("expected_evidence_types", [])),
                    stop_conditions=plan_data.get("stop_conditions", []),
                    rewritten_queries=plan_data.get("rewritten_queries", [state.original_question]),
                    exploration_plan=plan_data.get("exploration_plan", []),
                    initial_tools=plan_data.get("initial_tools", []),
                )
                
                state.apply_planner_output(planner_output)
                
                logger.info(f"[Planner] Intent: {state.query_intent.value}, Entities: {state.entities[:3]}, Plan steps: {len(state.exploration_plan)}")
                self._emit_event(
                    "planning",
                    {
                        "status": "planned",
                        "intent": state.query_intent.value,
                        "entities": state.entities[:5],
                        "plan_steps": len(state.exploration_plan),
                        "stop_conditions": state.stop_conditions[:3],
                    },
                )
            else:
                state.query_intent = QueryIntent.IMPLEMENTATION
                state.rewritten_queries = [state.original_question]
                state.missing_pieces = ["Start with repo_map for overview"]
                
        except Exception as e:
            logger.warning(f"[Planner] Failed to parse plan: {e}")
            state.query_intent = QueryIntent.IMPLEMENTATION
            state.missing_pieces = ["Use repo_map to understand structure"]
            self._emit_event("planning", {"status": "fallback", "reason": str(e)})
        
        return state
    
    def _node_tool_executor(self, state: AgentState) -> AgentState:
        """
        工具执行节点：根据缺失信息和策略选择并执行工具
        """
        logger.info(f"[ToolExecutor] Iteration {state.iteration_count + 1}, missing: {state.missing_pieces[:2]}")
        
        prompt = get_tool_router_prompt()
        
        messages = prompt.format_messages(
            question=state.original_question,
            query_intent=state.query_intent.value if state.query_intent else "implementation",
            anchors_summary=state.get_anchors_summary(),
            context_summary=state.get_context_summary(max_length=4000),
            missing_pieces="\n".join(state.missing_pieces) if state.missing_pieces else "需要收集初始上下文",
            tool_history=self._format_tool_history(state.tool_calls_history),
            exploration_plan="\n".join(state.exploration_plan) if state.exploration_plan else "自适应探索"
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.1)
            tool_selection = self._parse_json_response(response)
            
            if tool_selection:
                selected_tools = self._build_tool_plan(state, tool_selection)
                logger.info(
                    "[ToolExecutor] Selected tools: %s",
                    ", ".join(f"{item['tool']}" for item in selected_tools),
                )
                self._emit_event(
                    "tool_call",
                    {
                        "status": "start",
                        "iteration": state.iteration_count + 1,
                        "parallel": len(selected_tools) > 1,
                        "tools": selected_tools,
                    },
                )
                self._execute_tool_batch(state, selected_tools)
            else:
                fallback_tools = self._get_fallback_tools(state)
                self._emit_event(
                    "tool_call",
                    {
                        "status": "fallback",
                        "iteration": state.iteration_count + 1,
                        "parallel": False,
                        "tools": fallback_tools,
                    },
                )
                self._execute_tool_batch(state, fallback_tools)
                    
        except Exception as e:
            logger.error(f"[ToolExecutor] Execution failed: {e}")
            state.add_tool_call(ToolCall(
                tool=ToolType.RAG_SEARCH,
                arguments={},
                error=str(e),
                success=False,
                timestamp=datetime.now().isoformat()
            ))
            self._emit_event("tool_call", {"status": "error", "error": str(e)})
        
        return state

    def _build_tool_plan(self, state: AgentState, tool_selection: Dict[str, Any]) -> List[Dict[str, Any]]:
        """构建本轮工具执行计划，支持并行多工具和策略性互补。"""
        raw_tools = tool_selection.get("tools")
        selected_tools: List[Dict[str, Any]] = []

        if isinstance(raw_tools, list) and raw_tools:
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("tool", "rag_search"))
                args = item.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}
                selected_tools.append({"tool": tool_name, "arguments": args})
        else:
            tool_name = str(tool_selection.get("tool", "rag_search"))
            args = tool_selection.get("arguments", {})
            if not isinstance(args, dict):
                args = {}
            selected_tools.append({"tool": tool_name, "arguments": args})

        # 策略性互补：基于 intent 和当前状态
        called_tools = {call.tool.value for call in state.tool_calls_history}
        current_tools = {t["tool"] for t in selected_tools}
        
        # 首轮：强制使用结构化工具
        if state.iteration_count == 0:
            # 确保有仓库概览（repo_map）
            has_repo_map = "repo_map" in called_tools or "repo_map" in current_tools
            if not has_repo_map and self.repo_map_tool:
                if state.query_intent in [QueryIntent.ARCHITECTURE, QueryIntent.CONCEPT, QueryIntent.IMPACT_ANALYSIS]:
                    selected_tools.insert(0, {"tool": "repo_map", "arguments": {"include_signatures": True, "max_depth": 3}})
                else:
                    selected_tools.append({"tool": "repo_map", "arguments": {"include_signatures": True, "max_depth": 2}})
            
            # 确保有代码图谱查询（code_graph）
            has_code_graph = "code_graph" in called_tools or "code_graph" in current_tools
            if not has_code_graph and self.graph_tool:
                if state.entities:
                    selected_tools.append({
                        "tool": "code_graph",
                        "arguments": {"operation": "find_definition", "symbol_name": state.entities[0]}
                    })
                else:
                    selected_tools.append({
                        "tool": "code_graph",
                        "arguments": {"operation": "get_all_symbols"}
                    })
        
        # 如果只有 rag_search，补充结构化工具
        if len(selected_tools) == 1 and selected_tools[0]["tool"] == "rag_search":
            if "code_graph" not in called_tools and self.graph_tool:
                if state.entities:
                    selected_tools.append({
                        "tool": "code_graph",
                        "arguments": {"operation": "find_definition", "symbol_name": state.entities[0]}
                    })
                elif self.repo_map_tool and "repo_map" not in called_tools:
                    selected_tools.append({
                        "tool": "repo_map",
                        "arguments": {"include_signatures": True}
                    })
        
        # 去重和限制
        dedup: Dict[str, Dict[str, Any]] = {}
        for item in selected_tools:
            key = f"{item['tool']}::{json.dumps(item['arguments'], ensure_ascii=False, sort_keys=True)}"
            dedup[key] = item
        selected_tools = list(dedup.values())[:3]
        return selected_tools
    
    def _get_fallback_tools(self, state: AgentState) -> List[Dict[str, Any]]:
        """获取回退工具列表"""
        tools = []
        
        # 优先使用结构化工具
        if state.iteration_count == 0 and self.repo_map_tool:
            tools.append({"tool": "repo_map", "arguments": {"include_signatures": True}})
        
        if state.entities and self.graph_tool:
            tools.append({"tool": "code_graph", "arguments": {"operation": "find_definition", "symbol_name": state.entities[0]}})
        else:
            tools.append({"tool": "rag_search", "arguments": {"query": state.original_question, "top_k": 5}})
        
        return tools

    def _execute_tool_batch(self, state: AgentState, selected_tools: List[Dict[str, Any]]) -> None:
        """并行执行工具批次并写入状态。"""
        started_at = time.time()
        batch_results: List[Dict[str, Any]] = []

        if len(selected_tools) <= 1:
            for item in selected_tools:
                tool_name = item["tool"]
                tool_args = item["arguments"]
                result = self._execute_tool(tool_name, tool_args)
                self._process_tool_result(state, tool_name, tool_args, result, batch_results)
        else:
            futures = {}
            with ThreadPoolExecutor(max_workers=min(3, len(selected_tools))) as executor:
                for item in selected_tools:
                    tool_name = item["tool"]
                    tool_args = item["arguments"]
                    futures[executor.submit(self._execute_tool, tool_name, tool_args)] = (tool_name, tool_args)

                for future in as_completed(futures):
                    tool_name, tool_args = futures[future]
                    result = future.result()
                    self._process_tool_result(state, tool_name, tool_args, result, batch_results)

        elapsed_ms = int((time.time() - started_at) * 1000)
        self._emit_event(
            "tool_call",
            {
                "status": "done",
                "iteration": state.iteration_count + 1,
                "elapsed_ms": elapsed_ms,
                "results": batch_results,
            },
        )
    
    def _process_tool_result(
        self, 
        state: AgentState, 
        tool_name: str, 
        tool_args: Dict[str, Any], 
        result: Optional[ContextPiece],
        batch_results: List[Dict[str, Any]]
    ) -> None:
        """处理工具执行结果，包括锚点识别"""
        success = bool(result is not None and float(getattr(result, "relevance_score", 0.0)) > 0.0)
        
        tool_call = ToolCall(
            tool=ToolType(tool_name) if tool_name in [t.value for t in ToolType] else ToolType.RAG_SEARCH,
            arguments=tool_args,
            result=result.content[:1000] if result else "No result",
            success=success,
            timestamp=datetime.now().isoformat(),
        )
        state.add_tool_call(tool_call)
        
        if result and float(getattr(result, "relevance_score", 0.0)) > 0.0:
            state.add_context(result)
            
            # 尝试从结果中提取锚点
            self._extract_anchors(state, tool_name, tool_args, result)
        
        batch_results.append({
            "tool": tool_name,
            "success": success,
            "relevance": float(getattr(result, "relevance_score", 0.0)) if result else 0.0,
        })
    
    def _extract_anchors(
        self, 
        state: AgentState, 
        tool_name: str, 
        tool_args: Dict[str, Any], 
        result: ContextPiece
    ) -> None:
        """从工具结果中提取锚点"""
        if tool_name == "code_graph":
            operation = tool_args.get("operation", "")
            symbol_name = tool_args.get("symbol_name", "")
            
            if operation == "find_definition" and "definition" in result.content.lower():
                anchor = Anchor(
                    anchor_type=AnchorType.DEFINITION,
                    symbol_name=symbol_name,
                    file_path=result.file_path or self._extract_file_from_content(result.content),
                    confidence=result.relevance_score,
                    metadata={"operation": operation}
                )
                state.add_anchor(anchor)
            elif operation in ["find_callers", "find_callees"]:
                anchor = Anchor(
                    anchor_type=AnchorType.REFERENCE,
                    symbol_name=symbol_name,
                    file_path=result.file_path or "unknown",
                    confidence=result.relevance_score * 0.8,
                    metadata={"operation": operation}
                )
                state.add_anchor(anchor)
        
        elif tool_name == "file_read" and result.file_path:
            anchor = Anchor(
                anchor_type=AnchorType.DEFINITION,
                symbol_name=tool_args.get("file_path", "").split("/")[-1],
                file_path=result.file_path,
                line_number=result.line_range[0] if result.line_range else None,
                confidence=0.9,
                metadata={"direct_read": True}
            )
            state.add_anchor(anchor)
    
    def _extract_file_from_content(self, content: str) -> str:
        """从内容中提取文件路径"""
        import re
        match = re.search(r'in\s+([^\s:]+\.(py|ts|tsx|js|jsx))', content)
        if match:
            return match.group(1)
        return "unknown"
    
    def _node_evaluator(self, state: AgentState) -> AgentState:
        """
        评估节点：评估上下文充分性，执行置信度门控
        """
        logger.info(f"[Evaluator] Evaluating context sufficiency (iteration {state.iteration_count + 1})")
        self._emit_event("evaluation", {"status": "start", "iteration": state.iteration_count + 1})
        
        prompt = get_evaluator_prompt()
        
        # 先做硬门控检查
        hard_gate_result = self._hard_gate_check(state)
        
        messages = prompt.format_messages(
            question=state.original_question,
            query_intent=state.query_intent.value if state.query_intent else "implementation",
            stop_conditions="\n".join(state.stop_conditions) if state.stop_conditions else "无明确停止条件",
            anchors_summary=state.get_anchors_summary(),
            evidence_summary=state.get_evidence_summary(max_length=4000),
            tool_history=self._format_tool_history(state.tool_calls_history),
            iteration_count=state.iteration_count + 1,
            max_iterations=state.max_iterations
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.1)
            eval_data = self._parse_json_response(response)
            
            if eval_data:
                # 合并硬门控和 LLM 评估结果
                llm_is_sufficient = bool(eval_data.get("is_sufficient", False))
                llm_confidence = float(eval_data.get("confidence_score", 0.5))
                
                # 硬门控优先
                final_confidence = min(llm_confidence, hard_gate_result["max_confidence"])
                
                eval_result = EvaluationResult(
                    is_ready=llm_is_sufficient and hard_gate_result["passed"],
                    confidence_score=final_confidence,
                    confidence_level=self._parse_confidence_level(eval_data.get("confidence_level", "unknown")),
                    has_primary_anchor=eval_data.get("has_primary_anchor", state.has_primary_anchor),
                    has_closed_path=eval_data.get("has_closed_path", False),
                    has_conflicts=eval_data.get("has_conflicts", False),
                    missing_pieces=[str(x) for x in eval_data.get("missing_pieces", [])],
                    reflection_notes=[eval_data.get("reflection_note", "")] if eval_data.get("reflection_note") else [],
                    suggested_next_step=eval_data.get("suggested_next_step"),
                )
                
                state.apply_evaluation(eval_result)
                
                if eval_result.has_conflicts:
                    state.conflict_details = eval_data.get("conflict_details", [])
                
                logger.info(
                    f"[Evaluator] Sufficient: {state.is_ready}, "
                    f"Confidence: {state.confidence_score:.2f} ({state.confidence_level.value}), "
                    f"Anchors: {len(state.anchors)}, "
                    f"Missing: {len(state.missing_pieces)}"
                )
                self._emit_event(
                    "evaluation",
                    {
                        "status": "done",
                        "iteration": state.iteration_count + 1,
                        "is_sufficient": state.is_ready,
                        "confidence": state.confidence_score,
                        "confidence_level": state.confidence_level.value,
                        "has_primary_anchor": state.has_primary_anchor,
                        "missing_count": len(state.missing_pieces),
                        "missing": state.missing_pieces[:3],
                    },
                )
            else:
                # Fallback 评估
                state = self._fallback_evaluation(state, hard_gate_result)
                    
        except Exception as e:
            logger.warning(f"[Evaluator] Evaluation failed: {e}")
            state = self._fallback_evaluation(state, hard_gate_result)
            self._emit_event("evaluation", {"status": "error", "error": str(e)})
        
        return state
    
    def _hard_gate_check(self, state: AgentState) -> Dict[str, Any]:
        """执行硬门控检查（针对不同意图类型调整策略）"""
        result = {
            "passed": True,
            "max_confidence": 1.0,
            "reasons": []
        }
        
        is_soft_anchor_intent = state.query_intent in SOFT_ANCHOR_INTENTS
        
        # 检查 1：是否有锚点
        definition_anchors = [a for a in state.anchors if a.anchor_type == AnchorType.DEFINITION]
        if not definition_anchors:
            if is_soft_anchor_intent:
                result["max_confidence"] = min(result["max_confidence"], 0.8)
            else:
                if state.iteration_count < 2:
                    result["passed"] = False
                    result["reasons"].append("No definition anchor found")
                result["max_confidence"] = min(result["max_confidence"], 0.7)
        
        # 检查 2：证据类型覆盖
        evidence_types = {e.evidence_type for e in state.evidence_cards}
        has_structural = EvidenceType.DEFINITION in evidence_types or EvidenceType.DIRECT_CALL in evidence_types
        has_semantic = EvidenceType.SEMANTIC_MATCH in evidence_types or EvidenceType.DOCUMENTATION in evidence_types
        
        if not has_structural:
            if is_soft_anchor_intent and has_semantic:
                result["max_confidence"] = min(result["max_confidence"], 0.75)
            else:
                result["max_confidence"] = min(result["max_confidence"], 0.6)
                result["reasons"].append("Missing structural evidence")
        
        # 检查 3：对于调用链问题，需要多个锚点
        if state.query_intent in [QueryIntent.CALL_CHAIN, QueryIntent.MECHANISM]:
            if len(state.anchors) < 2 and state.iteration_count < 2:
                result["passed"] = False
                result["reasons"].append("Call chain requires multiple anchors")
        
        # 检查 4：上下文数量检查（至少有一些上下文）
        if len(state.context_scratchpad) >= 3:
            result["max_confidence"] = max(result["max_confidence"], 0.6)
        
        return result
    
    def _fallback_evaluation(self, state: AgentState, hard_gate_result: Dict[str, Any]) -> AgentState:
        """回退评估逻辑"""
        if state.iteration_count >= 2 and len(state.context_scratchpad) >= 3:
            state.is_ready = True
            state.confidence_score = min(0.6, hard_gate_result["max_confidence"])
            state.confidence_level = ConfidenceLevel.LIKELY
        elif state.iteration_count >= 3:
            state.is_ready = True
            state.confidence_score = 0.5
            state.confidence_level = ConfidenceLevel.UNKNOWN
        return state
    
    def _node_synthesizer(self, state: AgentState) -> AgentState:
        """
        合成节点：生成最终答案，包含 Mermaid 图表和来源引用
        """
        logger.info("[Synthesizer] Generating final answer...")
        self._emit_event("synthesis", {"status": "start"})
        
        prompt = get_synthesizer_prompt()
        
        messages = prompt.format_messages(
            question=state.original_question,
            query_intent=state.query_intent.value if state.query_intent else "implementation",
            confidence_level=state.confidence_level.value,
            evidence_summary=state.get_evidence_summary(max_length=6000),
            anchors_summary=state.get_anchors_summary(),
            trajectory=self._format_trajectory(state.get_trajectory()),
            conversation_history=state.get_compressed_history() or "无对话历史"
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.3)
            synth_data = self._parse_json_response(response)
            
            if synth_data:
                state.final_answer = synth_data.get("answer", response)
                state.mermaid_diagram = synth_data.get("mermaid")
                state.sources = synth_data.get("sources", [])
                state.caveats = synth_data.get("caveats", [])
                
                # 根据置信度添加警告
                if state.confidence_level == ConfidenceLevel.LIKELY:
                    state.caveats.append("部分结论基于间接证据，可能需要进一步验证")
                elif state.confidence_level == ConfidenceLevel.UNKNOWN:
                    state.caveats.append("证据不足，以上分析仅供参考")
                
                if state.caveats and state.confidence_score < 0.7:
                    state.final_answer += "\n\n**注意事项：**\n" + "\n".join(f"- {c}" for c in state.caveats)
            else:
                state.final_answer = response
            
            # 补充来源
            for evidence in state.evidence_cards:
                citation = evidence.get_citation()
                if citation and citation not in state.sources:
                    state.sources.append(citation)
                    
        except Exception as e:
            logger.error(f"[Synthesizer] Failed: {e}")
            if state.context_scratchpad:
                state.final_answer = self._fallback_answer(state)
            else:
                state.final_answer = "抱歉，无法生成答案。请尝试重新提问或提供更多上下文。"
        
        logger.info(f"[Synthesizer] Answer generated: {len(state.final_answer)} chars")
        self._emit_event(
            "synthesis",
            {
                "status": "done",
                "answer_length": len(state.final_answer or ""),
                "sources_count": len(state.sources),
                "confidence_level": state.confidence_level.value,
            },
        )
        return state
    
    def _writeback_memory(self, state: AgentState) -> AgentState:
        """回写稳定事实到长期记忆"""
        # 只有高置信度的结论才写入
        if state.confidence_level != ConfidenceLevel.CONFIRMED:
            return state
        
        # 提取模块职责（从锚点）
        for anchor in state.anchors:
            if anchor.anchor_type == AnchorType.DEFINITION and anchor.confidence > 0.8:
                module = anchor.file_path.split("/")[0] if "/" in anchor.file_path else anchor.file_path
                if module not in state.repo_facts_memory.module_responsibilities:
                    state.repo_facts_memory.module_responsibilities[module] = f"Contains {anchor.symbol_name}"
        
        return state
    
    def _execute_tool(self, tool_name: str, args: Dict[str, Any]) -> Optional[ContextPiece]:
        """执行指定工具"""
        try:
            if tool_name == "rag_search":
                return self.rag_tool.execute(
                    query=args.get("query", ""),
                    top_k=args.get("top_k", 5)
                )
            elif tool_name == "code_graph" and self.graph_tool:
                return self.graph_tool.execute(
                    operation=args.get("operation", "get_all_symbols"),
                    symbol_name=args.get("symbol_name"),
                    file_path=args.get("file_path")
                )
            elif tool_name == "file_read" and self.file_tool:
                return self.file_tool.execute(
                    file_path=args.get("file_path", ""),
                    start_line=args.get("start_line"),
                    end_line=args.get("end_line")
                )
            elif tool_name == "repo_map" and self.repo_map_tool:
                return self.repo_map_tool.execute(
                    include_signatures=args.get("include_signatures", True),
                    max_depth=args.get("max_depth", 3)
                )
            else:
                return self.rag_tool.execute(query=args.get("query", ""))
                
        except Exception as e:
            logger.error(f"Tool {tool_name} execution failed: {e}")
            return ContextPiece(
                source=tool_name,
                content=f"Tool execution failed: {str(e)}",
                relevance_score=0.0,
                metadata={"error": str(e)}
            )
    
    def _parse_json_response(self, response: str) -> Optional[Dict[str, Any]]:
        """解析 LLM 返回的 JSON"""
        if not response:
            return None
        
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(response[start:end])
                except json.JSONDecodeError:
                    pass
        
        return None
    
    def _parse_intent(self, intent_str: str) -> QueryIntent:
        """解析意图字符串"""
        intent_map = {
            "location": QueryIntent.LOCATION,
            "mechanism": QueryIntent.MECHANISM,
            "call_chain": QueryIntent.CALL_CHAIN,
            "impact_analysis": QueryIntent.IMPACT_ANALYSIS,
            "debugging": QueryIntent.DEBUGGING,
            "architecture": QueryIntent.ARCHITECTURE,
            "change_guidance": QueryIntent.CHANGE_GUIDANCE,
            "concept": QueryIntent.CONCEPT,
            "usage": QueryIntent.USAGE,
            "general": QueryIntent.GENERAL,
            "implementation": QueryIntent.IMPLEMENTATION,
        }
        return intent_map.get(intent_str.lower(), QueryIntent.IMPLEMENTATION)
    
    def _parse_evidence_types(self, types: List[str]) -> List[EvidenceType]:
        """解析证据类型列表"""
        type_map = {
            "definition": EvidenceType.DEFINITION,
            "direct_call": EvidenceType.DIRECT_CALL,
            "route_config": EvidenceType.ROUTE_CONFIG,
            "test_assertion": EvidenceType.TEST_ASSERTION,
            "documentation": EvidenceType.DOCUMENTATION,
            "semantic_match": EvidenceType.SEMANTIC_MATCH,
        }
        return [type_map.get(t.lower(), EvidenceType.SEMANTIC_MATCH) for t in types if t.lower() in type_map]
    
    def _parse_confidence_level(self, level_str: str) -> ConfidenceLevel:
        """解析置信度级别"""
        level_map = {
            "confirmed": ConfidenceLevel.CONFIRMED,
            "likely": ConfidenceLevel.LIKELY,
            "unknown": ConfidenceLevel.UNKNOWN,
        }
        return level_map.get(level_str.lower(), ConfidenceLevel.UNKNOWN)
    
    def _format_history(self, history: List[Dict[str, str]]) -> str:
        """格式化对话历史"""
        if not history:
            return ""
        
        parts = []
        for msg in history[-6:]:
            role = "用户" if msg.get("role") == "user" else "助手"
            parts.append(f"{role}: {msg.get('content', '')}")
        return "\n".join(parts)
    
    def _format_tool_history(self, history: List[ToolCall]) -> str:
        """格式化工具调用历史"""
        if not history:
            return "无历史调用"
        
        parts = []
        for call in history[-5:]:
            status = "✓" if call.success else "✗"
            parts.append(f"{status} {call.tool.value}({call.arguments}) -> {call.result[:200] if call.result else 'N/A'}...")
        return "\n".join(parts)
    
    def _format_trajectory(self, trajectory: List[Dict[str, Any]]) -> str:
        """格式化推理轨迹"""
        if not trajectory:
            return "无探索轨迹"
        
        parts = []
        for i, step in enumerate(trajectory, 1):
            parts.append(f"Step {i}: {step.get('tool', 'unknown')} - {step.get('arguments', {})}")
        return "\n".join(parts)
    
    def _fallback_answer(self, state: AgentState) -> str:
        """生成回退答案"""
        answer_parts = [f"关于 '{state.original_question}' 的分析：\n"]
        
        for piece in state.context_scratchpad[:5]:
            if piece.relevance_score > 0.3:
                answer_parts.append(f"**来源: {piece.source}**")
                if piece.file_path:
                    answer_parts.append(f"文件: {piece.file_path}")
                answer_parts.append(piece.content[:500])
                answer_parts.append("---")
        
        if not answer_parts[1:]:
            answer_parts.append("未能找到足够的相关信息来回答此问题。")
        
        return "\n\n".join(answer_parts)


def create_agent_graph(
    vector_store_path: str,
    graph_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    max_iterations: int = 5,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> AgentGraphRunner:
    """创建 Agent 图执行器的工厂函数"""
    return AgentGraphRunner(
        vector_store_path=vector_store_path,
        graph_path=graph_path,
        repo_root=repo_root,
        max_iterations=max_iterations,
        on_event=on_event,
    )


def run_agent(
    question: str,
    repo_url: str,
    vector_store_path: str,
    graph_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    max_iterations: int = 5
) -> Dict[str, Any]:
    """运行 Agent 的便捷函数"""
    runner = create_agent_graph(
        vector_store_path=vector_store_path,
        graph_path=graph_path,
        repo_root=repo_root,
        max_iterations=max_iterations
    )
    
    return runner.run(
        question=question,
        repo_url=repo_url,
        conversation_history=conversation_history
    )
