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
import re
import time
from dataclasses import replace
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
    # 意图分类集合
    CODE_CENTRIC_INTENTS,
    DOC_CENTRIC_INTENTS,
    SOFT_ANCHOR_INTENTS,
    LIGHT_RETRIEVAL_INTENTS,
    DEEP_EXPLORATION_INTENTS,
    EXTERNAL_KNOWLEDGE_INTENTS,
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
    GrepSearchTool,
    LSPResolveTool,
    WebSearchTool,
)
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG

logger = logging.getLogger("app.agent.graph")


# Intent → default tool ordering (code-centric vs doc-centric).
INTENT_TOOL_STRATEGIES: Dict[QueryIntent, List[str]] = {
    # Code-centric — lsp_resolve 作为 code_graph 的精确补充
    QueryIntent.LOCATION: ["lsp_resolve", "code_graph", "file_read"],
    QueryIntent.MECHANISM: ["code_graph", "lsp_resolve", "file_read", "rag_search"],
    QueryIntent.CALL_CHAIN: ["lsp_resolve", "code_graph", "file_read"],
    QueryIntent.IMPACT_ANALYSIS: ["code_graph", "rag_search"],
    QueryIntent.DEBUGGING: ["code_graph", "file_read", "rag_search"],
    QueryIntent.CHANGE_GUIDANCE: ["code_graph", "file_read", "rag_search"],
    QueryIntent.IMPLEMENTATION: ["code_graph", "file_read", "rag_search"],
    # Doc / repo understanding
    QueryIntent.ARCHITECTURE: ["repo_map", "code_graph", "rag_search"],
    QueryIntent.CONCEPT: ["rag_search", "repo_map", "code_graph"],
    QueryIntent.USAGE: ["rag_search", "code_graph", "file_read"],
    QueryIntent.TOPIC_COVERAGE: ["rag_search", "grep_search", "repo_map"],
    QueryIntent.RELATIONSHIP: ["rag_search", "code_graph", "repo_map"],
    QueryIntent.EVIDENCE: ["rag_search", "grep_search", "file_read"],
    QueryIntent.SECTION_LOCATOR: ["rag_search", "grep_search", "repo_map"],
    QueryIntent.REPO_OVERVIEW: ["repo_map", "rag_search"],
    QueryIntent.FOLLOWUP_CLARIFICATION: ["rag_search", "file_read"],
    # External knowledge — web_search 优先
    QueryIntent.VERSION_CHECK: ["web_search"],
    QueryIntent.API_DOCS: ["web_search", "rag_search"],
    QueryIntent.EXTERNAL_REFERENCE: ["web_search", "rag_search"],
}

class AnswerPath:
    DIRECT = "direct"  # No tools; immediate answer
    LIGHT = "light"  # Single retrieval round
    DEEP = "deep"  # Multi-round explore + evaluate


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
        language: str = "en",
    ):
        self.vector_store_path = vector_store_path
        self.graph_path = graph_path
        self.repo_root = repo_root
        self.max_iterations = max_iterations
        self.on_event = on_event
        self.language = "en"  # Force English for all user-visible content
        
        self.rag_tool = RAGSearchTool(vector_store_path)
        self.graph_tool = CodeGraphTool(graph_path) if graph_path else None
        self.file_tool = FileReadTool(repo_root) if repo_root else None
        self.repo_map_tool = RepoMapTool(repo_root) if repo_root else None
        self.grep_tool = GrepSearchTool(repo_root) if repo_root else None
        self.lsp_tool = LSPResolveTool(repo_root) if repo_root else None
        # Web 搜索工具：从 CONFIG 读取配置
        web_cfg = CONFIG.get("web_search", {})
        self.web_tool = WebSearchTool(config=web_cfg)
        
        # 分层模型配置：快速模型用于规划/评估，强模型用于合成
        self._init_tiered_models()

    def _init_tiered_models(self) -> None:
        """
        初始化分层模型配置
        
        快速模型（flash）: planner, tool_router, evaluator, session_compressor
        强模型（强推理）: synthesizer
        """
        # Planner: 快速模型，负责意图识别和规划
        try:
            provider, model = get_model_config(CONFIG, "agent_planner")
        except Exception:
            provider, model = get_model_config(CONFIG, "hyde_generation")
        self.llm_planner = get_ai_client(provider, model=model)
        
        # Tool Router: 快速模型，负责工具选择
        try:
            provider, model = get_model_config(CONFIG, "agent_tool_router")
        except Exception:
            provider, model = get_model_config(CONFIG, "hyde_generation")
        self.llm_tool_router = get_ai_client(provider, model=model)
        
        # Evaluator: 快速模型，负责证据评估
        try:
            provider, model = get_model_config(CONFIG, "agent_evaluator")
        except Exception:
            provider, model = get_model_config(CONFIG, "hyde_generation")
        self.llm_evaluator = get_ai_client(provider, model=model)
        
        # Synthesizer: 强模型，负责最终答案合成
        try:
            provider, model = get_model_config(CONFIG, "agent_synthesizer")
        except Exception:
            provider, model = get_model_config(CONFIG, "rag_answer")
        self.llm_synthesizer = get_ai_client(provider, model=model)
        
        # Session Compressor: 快速模型，负责会话压缩
        try:
            provider, model = get_model_config(CONFIG, "agent_session_compressor")
        except Exception:
            provider, model = get_model_config(CONFIG, "hyde_generation")
        self.llm_session_compressor = get_ai_client(provider, model=model)
        
        # 兼容旧代码的默认 llm（使用强模型）
        self.llm = self.llm_synthesizer
        
        logger.info("[AgentGraph] Initialized tiered models for agent nodes")

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
            # Phase 1: 会话装载与压缩  +  词法级预分类（并行）
            lexical_intent = self._classify_intent_lexical(question)
            state = self._compress_session(state)
            
            # Phase 2: 规划（AI判断是否需要工具）
            state = self._node_planner(state, lexical_hint=lexical_intent)
            
            # 路径分流：决定走 direct / light / deep
            answer_path = self._determine_answer_path(state)
            self._emit_event("planning", {"status": "path_selected", "answer_path": answer_path})
            
            # Direct 路径：不需要工具，直接返回
            if answer_path == AnswerPath.DIRECT and state.final_answer:
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
            
            # Light path: at most one retrieval round; skip iterative evaluation
            if answer_path == AnswerPath.LIGHT:
                logger.info("[LightPath] Single-round retrieval")
                state = self._node_tool_executor(state)
                state.iteration_count = 1
                state.is_ready = True
            else:
                # Deep 路径：迭代式检索与评估（受控循环）
                # 首轮尝试并行子任务分解（exploration_plan 有多个步骤时）
                if (
                    len(state.exploration_plan) >= 2
                    and state.query_intent not in EXTERNAL_KNOWLEDGE_INTENTS
                ):
                    self._run_parallel_subtasks(state)
                    # 子任务结果已合并入 context_scratchpad；继续正常迭代评估
                
                while not state.is_ready and state.iteration_count < state.max_iterations:
                    state = self._node_tool_executor(state)
                    state = self._node_evaluator(state)
                    state.iteration_count += 1
                    
                    # 硬门控检查
                    if state.check_stop_conditions():
                        logger.info("[HardGate] Stop conditions met, proceeding to synthesis")
                        state.is_ready = True
                        break
            
            # Phase 4: 证据卡片转换 + 去重 + 重排
            state.convert_context_to_evidence()
            self._rerank_evidence(state)
            
            # Phase 5: 答案合成
            state = self._node_synthesizer(state)
            
            # Phase 6: 记忆回写
            state = self._writeback_memory(state)
            
        except Exception as e:
            logger.exception("Agent execution failed")
            state.error = str(e)
            state.final_answer = f"Sorry, an error occurred during analysis: {str(e)}"
        
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

    # ---- Sub-agent 并行子任务 ----

    def _run_parallel_subtasks(self, state: AgentState) -> None:
        """
        并行执行 exploration_plan 中的前 N 个子任务（轻量 LIGHT 路径）。

        每个子任务独立选择并调用一个工具，结果合并入 context_scratchpad
        和 subtask_results。这相当于对复杂问题做「横向并发探索」，
        为后续迭代提供更丰富的初始上下文。

        触发条件（由 run() 控制）：
        - DEEP 路径 + exploration_plan >= 2 步 + 非外部知识意图
        """
        subtasks = state.exploration_plan[:3]  # 最多并行 3 个
        logger.info("[SubtaskParallel] Running %d parallel subtasks: %s", len(subtasks), subtasks)

        self._emit_event("subtask_parallel", {
            "status": "start",
            "subtasks_count": len(subtasks),
            "subtasks": subtasks,
        })
        started_at = time.time()

        def _execute_subtask(subtask: str) -> Optional[ContextPiece]:
            """为单个子任务选工具并执行（单工具，不经 LLM router）"""
            # 基于意图做简单工具映射，避免再调一次 LLM（保持轻量）
            intent = state.query_intent
            entity_hint = state.entities[0] if state.entities else ""

            if intent in (QueryIntent.LOCATION, QueryIntent.CALL_CHAIN):
                if entity_hint and self.lsp_tool:
                    return self._timed_execute_tool(
                        "lsp_resolve",
                        {"symbol_name": entity_hint, "operation": "find_references"},
                    )
                elif entity_hint and self.graph_tool:
                    return self._timed_execute_tool(
                        "code_graph",
                        {"operation": "find_definition", "symbol_name": entity_hint},
                    )
            
            if intent in EXTERNAL_KNOWLEDGE_INTENTS:
                return self._timed_execute_tool(
                    "web_search",
                    {"query": subtask, "search_type": "general"},
                )

            # 通用回退：用子任务描述做 RAG 搜索
            return self._timed_execute_tool(
                "rag_search",
                {"query": subtask, "top_k": 20},
            )

        subtask_results: List[Dict[str, Any]] = []
        futures = {}
        with ThreadPoolExecutor(max_workers=min(3, len(subtasks))) as executor:
            for st in subtasks:
                futures[executor.submit(_execute_subtask, st)] = st
            for future in as_completed(futures):
                subtask_str = futures[future]
                try:
                    result = future.result()
                    if result and float(getattr(result, "relevance_score", 0.0)) > 0.0:
                        state.context_scratchpad.append(result)
                        state.subtask_results.append(result)
                        subtask_results.append({
                            "subtask": subtask_str[:80],
                            "tool": result.source,
                            "success": True,
                            "relevance": float(result.relevance_score),
                        })
                    else:
                        subtask_results.append({
                            "subtask": subtask_str[:80],
                            "tool": result.source if result else "unknown",
                            "success": False,
                        })
                except Exception as exc:
                    logger.warning("[SubtaskParallel] Subtask failed: %s — %s", subtask_str[:60], exc)
                    subtask_results.append({
                        "subtask": subtask_str[:80],
                        "tool": "unknown",
                        "success": False,
                    })

        elapsed_ms = int((time.time() - started_at) * 1000)
        logger.info(
            "[SubtaskParallel] Done in %dms — %d/%d succeeded",
            elapsed_ms, sum(1 for r in subtask_results if r.get("success")), len(subtasks),
        )
        self._emit_event("subtask_parallel", {
            "status": "done",
            "subtasks_count": len(subtasks),
            "subtasks": subtasks,
            "elapsed_ms": elapsed_ms,
            "results": subtask_results,
        })

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
            
            response = self.llm_session_compressor.chat(messages, temperature=0.1, max_tokens=500)
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

    # ---- 词法级预分类 & 路径分流 ----

    @staticmethod
    def _classify_intent_lexical(question: str) -> Optional[QueryIntent]:
        """
        词法级意图预分类（无需 LLM），用于跳过或辅助 Planner。
        返回 None 表示不确定，需 LLM 决策。
        """
        q = question.lower().strip()

        # Greetings / small talk (English prompts only)
        greetings = {"hi", "hello", "hey", "thanks", "thank you", "thx", "ty"}
        if q.rstrip("!.?") in greetings:
            return QueryIntent.GENERAL

        # Repo overview
        if any(kw in q for kw in ["tech stack", "what does this repo", "overview of", "what is this repo"]):
            return QueryIntent.REPO_OVERVIEW
        # Location in codebase
        if q.startswith(("where is", "where does", "where can i find")):
            return QueryIntent.LOCATION
        # Architecture
        if any(kw in q for kw in ["architecture", "overall structure", "high-level structure", "system design"]):
            return QueryIntent.ARCHITECTURE
        # Concept (short definitional questions)
        if q.startswith(("what is", "what are", "explain ")) and len(q) < 100:
            return QueryIntent.CONCEPT
        # Topic coverage across docs
        if any(kw in q for kw in ["which sections", "which files mention", "where is ... mentioned", "which parts discuss"]):
            return QueryIntent.TOPIC_COVERAGE
        # Usage
        if q.startswith(("how to use", "how do i use", "how can i use")):
            return QueryIntent.USAGE
        # Follow-up on prior assistant message
        if any(kw in q for kw in ["what you just said", "can you clarify", "can you explain more", "you mentioned", "earlier you said"]):
            return QueryIntent.FOLLOWUP_CLARIFICATION

        return None

    @staticmethod
    def _determine_answer_path(state: AgentState) -> str:
        """
        根据 Planner 输出决定回答路径：direct / light / deep。
        """
        if state.skip_tools and state.final_answer:
            return AnswerPath.DIRECT

        if state.query_intent in LIGHT_RETRIEVAL_INTENTS:
            return AnswerPath.LIGHT

        if state.query_intent in DEEP_EXPLORATION_INTENTS:
            return AnswerPath.DEEP

        # 默认：如有代码图则走 deep，否则走 light
        if state.query_intent in CODE_CENTRIC_INTENTS:
            return AnswerPath.DEEP

        return AnswerPath.LIGHT

    def _node_planner(self, state: AgentState, lexical_hint: Optional[QueryIntent] = None) -> AgentState:
        """
        规划节点：分析问题意图、判断是否需要工具、提取实体、制定探索计划。
        lexical_hint：词法预分类结果，可跳过 LLM 对明确意图的决策。
        """
        logger.info(f"[Planner] Analyzing question: {state.original_question[:100]}...")
        self._emit_event("planning", {"status": "analyzing", "question": state.original_question[:200]})
        
        # --- 如果词法预分类明确且为 GENERAL / FOLLOWUP_CLARIFICATION，直接走 direct ---
        if lexical_hint in (QueryIntent.GENERAL, QueryIntent.FOLLOWUP_CLARIFICATION):
            logger.info(f"[Planner] Lexical shortcut → {lexical_hint.value}, skip LLM planner")
            state.query_intent = lexical_hint
            state.skip_tools = True
            state.final_answer = None  # 由 synthesizer 生成
            self._emit_event("planning", {"status": "lexical_shortcut", "intent": lexical_hint.value})
            return state

        prompt = get_planner_prompt()
        history_text = state.get_compressed_history()
        repo_facts = json.dumps(state.repo_facts_memory.to_dict(), ensure_ascii=False)
        
        messages = prompt.format_messages(
            question=state.original_question,
            conversation_history=history_text or "No prior conversation.",
            repo_facts=repo_facts or "{}"
        )
        
        try:
            response = self.llm_planner.chat(messages, temperature=0.2)
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
                llm_intent = self._parse_intent(intent_str)
                # 词法 hint 优先级：如果 lexical_hint 与 LLM 一致则采用，否则以 LLM 为准
                # 但若 lexical_hint 非 None 且 LLM 回退了通用值，则信任词法结果
                if lexical_hint and llm_intent == QueryIntent.IMPLEMENTATION:
                    intent = lexical_hint
                else:
                    intent = llm_intent
                
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
                state.query_intent = lexical_hint or QueryIntent.IMPLEMENTATION
                state.rewritten_queries = [state.original_question]
                state.missing_pieces = ["Start with rag_search for overview"]
                
        except Exception as e:
            logger.warning(f"[Planner] Failed to parse plan: {e}")
            state.query_intent = lexical_hint or QueryIntent.IMPLEMENTATION
            state.missing_pieces = ["Use rag_search to understand structure"]
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
            missing_pieces="\n".join(state.missing_pieces) if state.missing_pieces else "Need to gather initial context.",
            tool_history=self._format_tool_history(state.tool_calls_history),
            exploration_plan="\n".join(state.exploration_plan) if state.exploration_plan else "Adaptive exploration"
        )
        
        try:
            response = self.llm_tool_router.chat(messages, temperature=0.1)
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
        is_doc_intent = state.query_intent in DOC_CENTRIC_INTENTS

        # 首轮互补策略（区分 doc-centric vs code-centric）
        if state.iteration_count == 0:
            has_repo_map = "repo_map" in called_tools or "repo_map" in current_tools

            if is_doc_intent:
                # Doc-centric: 确保有 rag_search；repo_map 仅在架构/概览时补充
                has_rag = "rag_search" in current_tools
                if not has_rag:
                    selected_tools.insert(0, {
                        "tool": "rag_search",
                        "arguments": {"query": state.original_question, "top_k": 20}
                    })
                if not has_repo_map and self.repo_map_tool and state.query_intent in (
                    QueryIntent.ARCHITECTURE, QueryIntent.REPO_OVERVIEW
                ):
                    selected_tools.append({
                        "tool": "repo_map",
                        "arguments": {"include_signatures": True, "max_depth": 2}
                    })
            else:
                # Code-centric: 保留原有结构化工具注入逻辑
                if not has_repo_map and self.repo_map_tool:
                    if state.query_intent in [QueryIntent.ARCHITECTURE, QueryIntent.CONCEPT, QueryIntent.IMPACT_ANALYSIS]:
                        selected_tools.insert(0, {"tool": "repo_map", "arguments": {"include_signatures": True, "max_depth": 3}})
                    else:
                        selected_tools.append({"tool": "repo_map", "arguments": {"include_signatures": True, "max_depth": 2}})

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
        
        # 如果只有 rag_search 且是 code-centric，补充结构化工具
        if not is_doc_intent and len(selected_tools) == 1 and selected_tools[0]["tool"] == "rag_search":
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
        
        # 查询富化：将过短的 rag/grep 查询扩展为包含具体上下文的完整描述
        self._enrich_tool_queries(state, selected_tools)

        self._inject_deep_followup_tools(state, selected_tools)

        # 去重和限制
        dedup: Dict[str, Dict[str, Any]] = {}
        for item in selected_tools:
            key = f"{item['tool']}::{json.dumps(item['arguments'], ensure_ascii=False, sort_keys=True)}"
            dedup[key] = item
        selected_tools = list(dedup.values())[:3]
        return selected_tools

    _CODE_FILE_SUFFIXES: tuple[str, ...] = (
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".pyi",
        ".rs", ".go", ".java", ".kt", ".swift", ".vue", ".svelte",
    )

    @classmethod
    def _is_code_repo_path(cls, path: str) -> bool:
        if not path or path == "unknown":
            return False
        raw = path.split(":", 1)[-1].strip()
        return any(raw.endswith(s) for s in cls._CODE_FILE_SUFFIXES)

    def _extract_priority_file_path(self, state: AgentState) -> Optional[str]:
        for piece in reversed(state.context_scratchpad):
            if piece.source == "grep_search":
                for m in piece.metadata.get("grep_matches") or []:
                    fp = m.get("file")
                    if fp and self._is_code_repo_path(fp):
                        return str(fp)
            if piece.source == "rag_search":
                for src in piece.metadata.get("sources") or []:
                    fp = src.split(":", 1)[-1].strip() if ":" in src else src.strip()
                    if self._is_code_repo_path(fp):
                        return fp
        for piece in reversed(state.context_scratchpad):
            if piece.file_path and self._is_code_repo_path(piece.file_path):
                return piece.file_path
        return None

    @staticmethod
    def _entities_look_like_code_symbols(entities: List[str]) -> bool:
        if not entities:
            return False
        for e in entities:
            t = (e or "").strip()
            if len(t) < 2 or " " in t or any(sep in t for sep in ("/", "\\", ".md")):
                return False
        return True

    def _heuristic_grep_pattern(self, state: AgentState) -> str:
        blob = " ".join(
            state.rewritten_queries[:2]
            if state.rewritten_queries
            else [state.original_question]
        )
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", blob)
        stop = {
            "the", "and", "for", "are", "how", "what", "when", "why", "this", "that",
            "with", "from", "into", "not", "but", "has", "have", "was", "were", "does",
            "did", "can", "use", "using", "used", "one", "any", "all", "out", "our",
            "you", "its", "way", "may", "via",
        }
        seen: set[str] = set()
        kws: List[str] = []
        for t in tokens:
            tl = t.lower()
            if tl in stop or tl in seen:
                continue
            seen.add(tl)
            kws.append(t)
            if len(kws) >= 4:
                break
        if len(kws) < 2:
            return ""
        return "|".join(re.escape(k) for k in kws)

    def _inject_deep_followup_tools(
        self,
        state: AgentState,
        selected_tools: List[Dict[str, Any]],
    ) -> None:
        """
        深度探索（mechanism 等）：在 RAG/ grep 已经给出源码路径后，主动插入 file_read；
        对「自然语言实体」补充词法 grep，避免 code_graph find_definition 全程空转。
        """
        if state.query_intent not in DEEP_EXPLORATION_INTENTS:
            return

        called = {c.tool for c in state.tool_calls_history}
        planned = {t["tool"] for t in selected_tools}

        if (
            self.file_tool
            and ToolType.FILE_READ not in called
            and "file_read" not in planned
            and state.iteration_count >= 1
        ):
            path = self._extract_priority_file_path(state)
            if path:
                selected_tools.insert(
                    0,
                    {
                        "tool": "file_read",
                        "arguments": {"file_path": path, "max_lines": 240},
                    },
                )

        if (
            state.iteration_count == 0
            and self.grep_tool
            and "grep_search" not in called
            and "grep_search" not in planned
            and not self._entities_look_like_code_symbols(state.entities)
        ):
            pat = self._heuristic_grep_pattern(state)
            if pat:
                selected_tools.append({
                    "tool": "grep_search",
                    "arguments": {
                        "pattern": pat,
                        "max_results": 24,
                        "is_regex": True,
                        "case_sensitive": False,
                    },
                })

        # Anti-stagnation：连续 ≥ 2 轮置信度 ≤ 0.5 且仍有 missing_pieces 时，
        # 注入一条尚未被 rag_search 尝试过的 missing_piece 作为精准查询，
        # 打破"相同工具相同参数"的死循环。
        if (
            state.iteration_count >= 2
            and state.confidence_score <= 0.5
            and state.missing_pieces
        ):
            tried_rag_queries = {
                str(call.arguments.get("query") or "").lower()
                for call in state.tool_calls_history
                if call.tool == ToolType.RAG_SEARCH
            }
            current_tools = {t["tool"] for t in selected_tools}
            for piece in state.missing_pieces:
                if len(piece) > 20 and piece.lower() not in tried_rag_queries:
                    if "rag_search" not in current_tools:
                        selected_tools.append({
                            "tool": "rag_search",
                            "arguments": {"query": piece, "top_k": 20},
                        })
                    break

    def _build_contextual_query(self, state: AgentState, keyword: str) -> str:
        """
        将单词/短语 keyword 扩展为包含具体缺失信息的富查询。

        优先从 missing_pieces 中找包含 keyword 的最长条目；
        其次直接使用 missing_pieces 中最长的条目；
        最后回退到把 keyword 拼入原始问题。
        """
        kw_lower = keyword.lower()
        if state.missing_pieces:
            relevant = [m for m in state.missing_pieces if kw_lower in m.lower()]
            if relevant:
                return max(relevant, key=len)[:250]
            best = max(state.missing_pieces, key=len)
            if len(best) > len(keyword):
                return best[:250]
        if kw_lower in state.original_question.lower():
            return state.original_question[:250]
        return f"{keyword} {state.original_question}"[:250]

    def _enrich_tool_queries(self, state: AgentState, selected_tools: List[Dict[str, Any]]) -> None:
        """
        扩展本轮工具计划中过短（< 25 chars）的查询。

        避免 tool_router 生成单词级 grep/rag 查询（如 "table"、"translate"）
        而无法定位具体实现。enrichment 策略：
        - rag_search：短 query → 用 missing_pieces / 原始问题补全
        - grep_search（无 grep_tool）：短 pattern → 同上，存入 args["query"]
          供 _execute_tool 中的 rag fallback 使用
        已在 tool_calls_history 中出现过的查询不重复注入。
        """
        already_tried: set = {
            str(call.arguments.get("query") or call.arguments.get("pattern") or "")
            for call in state.tool_calls_history
        }

        for item in selected_tools:
            tool_name = item["tool"]
            args = item["arguments"]

            if tool_name == "rag_search":
                q = (args.get("query") or "").strip()
                if len(q) < 25:
                    enriched = self._build_contextual_query(state, q)
                    if len(enriched) > len(q) and enriched not in already_tried:
                        args["query"] = enriched

            elif tool_name == "grep_search" and not self.grep_tool:
                # grep 无 repo_root 时会 fallback 到 rag；预置富查询供 _execute_tool 使用
                pat = (args.get("pattern") or "").strip()
                if len(pat) < 25:
                    enriched = self._build_contextual_query(state, pat)
                    if len(enriched) > len(pat) and enriched not in already_tried:
                        args["query"] = enriched

    def _get_fallback_tools(self, state: AgentState) -> List[Dict[str, Any]]:
        """获取回退工具列表（doc-intent 优先 rag_search）"""
        tools = []
        is_doc_intent = state.query_intent in DOC_CENTRIC_INTENTS

        if is_doc_intent:
            tools.append({"tool": "rag_search", "arguments": {"query": state.original_question, "top_k": 5}})
        else:
            if state.iteration_count == 0 and self.repo_map_tool:
                tools.append({"tool": "repo_map", "arguments": {"include_signatures": True}})
            if state.entities and self.graph_tool:
                tools.append({"tool": "code_graph", "arguments": {"operation": "find_definition", "symbol_name": state.entities[0]}})
            else:
                tools.append({"tool": "rag_search", "arguments": {"query": state.original_question, "top_k": 20}})
        
        return tools

    def _timed_execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> Optional[ContextPiece]:
        """执行单个工具并写入 duration_ms 到 metadata。"""
        t0 = time.perf_counter()
        result = self._execute_tool(tool_name, tool_args)
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if result is not None:
            md = dict(result.metadata or {})
            md["duration_ms"] = duration_ms
            result = replace(result, metadata=md)
        return result

    def _execute_tool_batch(self, state: AgentState, selected_tools: List[Dict[str, Any]]) -> None:
        """并行执行工具批次并写入状态。"""
        started_at = time.time()
        batch_results: List[Dict[str, Any]] = []

        if len(selected_tools) <= 1:
            for item in selected_tools:
                tool_name = item["tool"]
                tool_args = item["arguments"]
                result = self._timed_execute_tool(tool_name, tool_args)
                self._process_tool_result(state, tool_name, tool_args, result, batch_results)
        else:
            futures = {}
            with ThreadPoolExecutor(max_workers=min(3, len(selected_tools))) as executor:
                for item in selected_tools:
                    tool_name = item["tool"]
                    tool_args = item["arguments"]
                    futures[executor.submit(self._timed_execute_tool, tool_name, tool_args)] = (
                        tool_name,
                        tool_args,
                    )

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
        md = dict(result.metadata) if result and result.metadata else {}
        metric_keys = (
            "num_matches",
            "files_searched",
            "truncated",
            "duration_ms",
            "engine",
            "pattern",
            "error",
        )
        metrics = {k: md[k] for k in metric_keys if k in md}
        used_fallback = bool(md.get("used_python_fallback"))

        tool_call = ToolCall(
            tool=ToolType(tool_name) if tool_name in [t.value for t in ToolType] else ToolType.RAG_SEARCH,
            arguments=tool_args,
            result=result.content[:1000] if result else "No result",
            success=success,
            timestamp=datetime.now().isoformat(),
            duration_ms=md.get("duration_ms"),
            metrics=metrics if metrics else None,
            used_fallback=used_fallback,
        )
        state.add_tool_call(tool_call)
        
        if result and float(getattr(result, "relevance_score", 0.0)) > 0.0:
            state.add_context(result)
            
            # 尝试从结果中提取锚点
            self._extract_anchors(state, tool_name, tool_args, result)
        
        batch_entry: Dict[str, Any] = {
            "tool": tool_name,
            "success": success,
            "relevance": float(getattr(result, "relevance_score", 0.0)) if result else 0.0,
        }
        if md.get("duration_ms") is not None:
            batch_entry["duration_ms"] = md["duration_ms"]
        if metrics:
            batch_entry["metrics"] = metrics
        if used_fallback:
            batch_entry["used_fallback"] = True
        batch_results.append(batch_entry)
    
    def _extract_anchors(
        self, 
        state: AgentState, 
        tool_name: str, 
        tool_args: Dict[str, Any], 
        result: ContextPiece
    ) -> None:
        """
        从工具结果中提取锚点
        
        增强版：更积极地从各种工具结果中提取锚点，
        并处理 code_graph 返回的 anchor_candidates。
        """
        import re
        
        if tool_name == "code_graph":
            operation = tool_args.get("operation", "")
            symbol_name = tool_args.get("symbol_name", "")
            
            # 优先使用返回结果中的 anchor_candidates
            anchor_candidates = result.metadata.get("anchor_candidates", [])
            if anchor_candidates:
                for cand in anchor_candidates[:3]:  # 最多取前3个
                    atype = str(cand.get("anchor_type") or cand.get("type") or "").lower()
                    is_definition = "definition" in atype or atype == "def"
                    anchor = Anchor(
                        anchor_type=AnchorType.DEFINITION if is_definition else AnchorType.REFERENCE,
                        symbol_name=cand.get("symbol_name") or cand.get("symbol") or symbol_name,
                        file_path=cand.get("file_path") or cand.get("file") or result.file_path or "unknown",
                        line_number=cand.get("line_number", cand.get("line")),
                        confidence=float(cand.get("confidence", result.relevance_score)),
                        metadata={"operation": operation, "from_candidates": True}
                    )
                    state.add_anchor(anchor)
                return
            
            # 使用 primary_file 如果存在
            primary_file = result.metadata.get("primary_file")
            
            if operation == "find_definition":
                # 更宽松的定义检测
                has_definition = any(kw in result.content.lower() for kw in ["definition", "defined in", "class ", "def ", "function ", "const ", "export "])
                if has_definition or result.relevance_score > 0.5:
                    file_path = primary_file or result.file_path or self._extract_file_from_content(result.content)
                    line_number = self._extract_line_number(result.content)
                    anchor = Anchor(
                        anchor_type=AnchorType.DEFINITION,
                        symbol_name=symbol_name,
                        file_path=file_path,
                        line_number=line_number,
                        confidence=min(result.relevance_score + 0.1, 1.0),  # 略微提升置信度
                        metadata={"operation": operation}
                    )
                    state.add_anchor(anchor)
                    
            elif operation in ["find_callers", "find_callees"]:
                # 提取调用关系锚点
                file_path = primary_file or result.file_path or self._extract_file_from_content(result.content)
                anchor = Anchor(
                    anchor_type=AnchorType.REFERENCE,
                    symbol_name=symbol_name,
                    file_path=file_path,
                    confidence=result.relevance_score * 0.85,
                    metadata={"operation": operation}
                )
                state.add_anchor(anchor)
                
                # 标记已发现调用链路径
                if operation == "find_callees" and result.relevance_score > 0.5:
                    state.has_closed_path = True
                    
            elif operation == "get_all_symbols":
                # 从符号列表中提取关键锚点
                for entity in state.entities[:3]:
                    if entity.lower() in result.content.lower():
                        match = re.search(rf'(\S+\.(?:py|ts|tsx|js|jsx)).*{re.escape(entity)}', result.content, re.IGNORECASE)
                        if match:
                            anchor = Anchor(
                                anchor_type=AnchorType.REFERENCE,
                                symbol_name=entity,
                                file_path=match.group(1),
                                confidence=0.6,
                                metadata={"operation": operation, "matched_entity": True}
                            )
                            state.add_anchor(anchor)
        
        elif tool_name == "file_read" and result.file_path:
            # 文件读取总是生成定义锚点
            file_name = tool_args.get("file_path", "").split("/")[-1]
            anchor = Anchor(
                anchor_type=AnchorType.DEFINITION,
                symbol_name=file_name,
                file_path=result.file_path,
                line_number=result.line_range[0] if result.line_range else None,
                confidence=0.9,
                metadata={"direct_read": True}
            )
            state.add_anchor(anchor)
            
            # 尝试从文件内容中提取更具体的符号锚点
            for entity in state.entities[:2]:
                if entity.lower() in result.content.lower():
                    line_num = self._find_symbol_line(result.content, entity)
                    if line_num:
                        anchor = Anchor(
                            anchor_type=AnchorType.DEFINITION,
                            symbol_name=entity,
                            file_path=result.file_path,
                            line_number=line_num,
                            confidence=0.85,
                            metadata={"found_in_file": True}
                        )
                        state.add_anchor(anchor)
        
        elif tool_name == "repo_map":
            # 从仓库地图中提取入口点锚点
            for entity in state.entities[:3]:
                if entity.lower() in result.content.lower():
                    file_match = re.search(rf'(\S+\.(?:py|ts|tsx|js|jsx)).*{re.escape(entity)}', result.content, re.IGNORECASE)
                    if file_match:
                        anchor = Anchor(
                            anchor_type=AnchorType.ENTRYPOINT,
                            symbol_name=entity,
                            file_path=file_match.group(1),
                            confidence=0.7,
                            metadata={"from_repo_map": True}
                        )
                        state.add_anchor(anchor)
        
        elif tool_name == "rag_search":
            # 从 RAG 结果中提取锚点；源码路径提升为 DEFINITION 类，便于机制类问题通过硬门控
            sources = result.metadata.get("sources", [])
            for source in sources[:3]:
                if ":" in source:
                    parts = source.split(":", 1)
                    file_path = parts[1] if len(parts) > 1 else parts[0]
                else:
                    file_path = source
                if file_path and file_path != "unknown":
                    is_code = self._is_code_repo_path(file_path)
                    conf = max(float(result.relevance_score) * 0.82 + 0.05, 0.72) if is_code else float(result.relevance_score) * 0.7
                    anchor = Anchor(
                        anchor_type=AnchorType.DEFINITION if is_code else AnchorType.REFERENCE,
                        symbol_name=file_path.split("/")[-1].split(".")[0],
                        file_path=file_path,
                        confidence=min(conf, 0.92),
                        metadata={"from_rag": True, "code_hit": is_code},
                    )
                    state.add_anchor(anchor)

        elif tool_name == "grep_search":
            matches = result.metadata.get("grep_matches") or []

            # grep 无 repo_root 时 fallback 到 rag_search；result.source 为 "rag_search"。
            # 此时 grep_matches 为空，改用 rag-style sources 提取锚点。
            if not matches and result.source == "rag_search":
                sources = result.metadata.get("sources", [])
                for source in sources[:3]:
                    file_path = source.split(":", 1)[-1].strip() if ":" in source else source.strip()
                    if file_path and file_path != "unknown" and self._is_code_repo_path(file_path):
                        anchor = Anchor(
                            anchor_type=AnchorType.REFERENCE,
                            symbol_name=(tool_args.get("pattern") or tool_args.get("query") or "match")[:80],
                            file_path=file_path,
                            confidence=min(float(result.relevance_score) * 0.75, 0.80),
                            metadata={"from_grep_rag_fallback": True},
                        )
                        state.add_anchor(anchor)
                return

            pat = tool_args.get("pattern") or result.metadata.get("pattern") or "match"
            for m in matches[:5]:
                fp = m.get("file")
                ln = m.get("line")
                if not fp:
                    continue
                anchor = Anchor(
                    anchor_type=AnchorType.REFERENCE,
                    symbol_name=str(pat)[:80],
                    file_path=fp,
                    line_number=int(ln) if ln is not None else None,
                    confidence=min(float(result.relevance_score), 0.88),
                    metadata={"from_grep": True, "line_text": (m.get("text") or "")[:120]},
                )
                state.add_anchor(anchor)
    
    def _extract_file_from_content(self, content: str) -> str:
        """从内容中提取文件路径"""
        import re
        # 更全面的文件路径模式
        patterns = [
            r'in\s+[`"]?([^\s`"]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php))[`"]?',
            r'file[:\s]+[`"]?([^\s`"]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php))[`"]?',
            r'([a-zA-Z0-9_/]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|php))',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1)
        return "unknown"
    
    def _extract_line_number(self, content: str) -> Optional[int]:
        """从内容中提取行号"""
        import re
        patterns = [
            r'line\s*(\d+)',
            r':(\d+)(?::\d+)?',
            r'L(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    continue
        return None
    
    def _find_symbol_line(self, content: str, symbol: str) -> Optional[int]:
        """在文件内容中查找符号所在行"""
        import re
        lines = content.split('\n')
        patterns = [
            rf'(?:def|class|function|const|let|var|export)\s+{re.escape(symbol)}',
            rf'{re.escape(symbol)}\s*[=:(]',
        ]
        for i, line in enumerate(lines, 1):
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    return i
        return None
    
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
            stop_conditions="\n".join(state.stop_conditions) if state.stop_conditions else "No explicit stop conditions defined.",
            anchors_summary=state.get_anchors_summary(),
            evidence_summary=state.get_evidence_summary(max_length=4000),
            tool_history=self._format_tool_history(state.tool_calls_history),
            iteration_count=state.iteration_count + 1,
            max_iterations=state.max_iterations
        )
        
        try:
            response = self.llm_evaluator.chat(messages, temperature=0.1)
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
        is_doc_intent = state.query_intent in DOC_CENTRIC_INTENTS
        
        # 检查 1：是否有锚点（文档类意图不强制）
        definition_anchors = [a for a in state.anchors if a.anchor_type == AnchorType.DEFINITION]
        if not definition_anchors:
            if is_doc_intent:
                # 文档类意图不需要定义锚点，略微降低置信度即可
                result["max_confidence"] = min(result["max_confidence"], 0.85)
            elif is_soft_anchor_intent:
                result["max_confidence"] = min(result["max_confidence"], 0.8)
            else:
                if state.iteration_count < 2:
                    result["passed"] = False
                    result["reasons"].append("No definition anchor found")
                result["max_confidence"] = min(result["max_confidence"], 0.7)
        
        # 检查 2：证据类型覆盖
        evidence_types = {e.evidence_type for e in state.evidence_cards}
        # LEXICAL_MATCH + definition_anchors 视为足够的结构化证据：
        # RAG 命中代码文件（LEXICAL_MATCH）且存在定义锚点，说明已定位到实现位置，
        # 无需强制要求只有 file_read / code_graph 才算结构化。
        has_structural = (
            EvidenceType.DEFINITION in evidence_types
            or EvidenceType.DIRECT_CALL in evidence_types
            or (EvidenceType.LEXICAL_MATCH in evidence_types and bool(definition_anchors))
        )
        has_semantic = EvidenceType.SEMANTIC_MATCH in evidence_types or EvidenceType.DOCUMENTATION in evidence_types

        if not has_structural:
            if (is_soft_anchor_intent or is_doc_intent) and has_semantic:
                # 文档/软意图有语义证据即可
                result["max_confidence"] = min(result["max_confidence"], 0.78)
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

    @staticmethod
    def _rerank_evidence(state: AgentState) -> None:
        """按证据类型权重 + 原始 relevance 重排，截断到前 30 条。"""
        type_weight = {
            EvidenceType.DEFINITION: 1.0,
            EvidenceType.DIRECT_CALL: 0.9,
            EvidenceType.ROUTE_CONFIG: 0.85,
            EvidenceType.TEST_ASSERTION: 0.8,
            EvidenceType.DOCUMENTATION: 0.75,
            EvidenceType.LEXICAL_MATCH: 0.72,
            EvidenceType.SEMANTIC_MATCH: 0.6,
        }

        def score(e: EvidenceCard) -> float:
            w = type_weight.get(e.evidence_type, 0.5)
            conf = {"confirmed": 1.0, "likely": 0.7, "unknown": 0.4}.get(
                e.confidence.value if e.confidence else "unknown", 0.4
            )
            return w * conf

        state.evidence_cards.sort(key=score, reverse=True)
        if len(state.evidence_cards) > 30:
            state.evidence_cards = state.evidence_cards[:30]
    
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
            conversation_history=state.get_compressed_history() or "No conversation history",
        )
        
        try:
            response = self.llm_synthesizer.chat(messages, temperature=0.3)
            synth_data = self._parse_json_response(response)
            
            if synth_data:
                state.final_answer = synth_data.get("answer", response)
                state.mermaid_diagram = synth_data.get("mermaid")
                # Filter out "unknown" sources
                raw_sources = synth_data.get("sources", [])
                state.sources = [s for s in raw_sources if s and s.lower() != "unknown"]
                state.caveats = synth_data.get("caveats", [])
            else:
                state.final_answer = response
            
            # 补充来源（过滤 unknown）
            for evidence in state.evidence_cards:
                citation = evidence.get_citation()
                if citation and citation.lower() != "unknown" and citation not in state.sources:
                    state.sources.append(citation)
            
            # 强制注入 context_scratchpad 中所有检索到的原始来源
            for piece in state.context_scratchpad:
                # 检查 piece.file_path 或 metadata 中的 source
                source_path = piece.file_path or piece.metadata.get("source") or piece.metadata.get("file_path")
                if source_path and isinstance(source_path, str) and source_path.lower() != "unknown":
                    # 统一格式处理（如果是 RAG 的 category:path 格式，提取 path）
                    clean_source = source_path.split(":", 1)[-1] if ":" in source_path and not source_path.startswith("/") else source_path
                    if clean_source not in state.sources:
                        state.sources.append(clean_source)
                    
        except Exception as e:
            logger.error(f"[Synthesizer] Failed: {e}")
            if state.context_scratchpad:
                state.final_answer = self._fallback_answer(state)
            else:
                state.final_answer = "Sorry, unable to generate an answer. Try rephrasing the question or providing more context."
        
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
        """回写会话记忆和长期记忆。"""
        # 1) 实体 / 主题跨轮携带 → SessionMemory
        new_entities = [e for e in state.entities if e not in state.session_memory.key_entities]
        state.session_memory.key_entities.extend(new_entities)
        # 限制长度
        state.session_memory.key_entities = state.session_memory.key_entities[-20:]

        # 2) 只有高置信度的结论才写入 RepoFactsMemory
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
                    top_k=args.get("top_k", 20)
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
            elif tool_name == "lsp_resolve" and self.lsp_tool:
                return self.lsp_tool.execute(
                    symbol_name=args.get("symbol_name", ""),
                    operation=args.get("operation", "find_definition"),
                    file_hint=args.get("file_hint"),
                )
            elif tool_name == "lsp_resolve" and not self.lsp_tool:
                # No repo_root: fall back to code_graph or rag
                symbol = (args.get("symbol_name") or "").strip()
                if symbol and self.graph_tool:
                    return self.graph_tool.execute(
                        operation="find_definition",
                        symbol_name=symbol,
                    )
                if symbol:
                    return self.rag_tool.execute(query=symbol, top_k=20)
                return ContextPiece(
                    source="lsp_resolve",
                    content="lsp_resolve requires repo_root; no repo configured.",
                    relevance_score=0.0,
                    metadata={"error": "no_repo_root"},
                )
            elif tool_name == "web_search":
                return self.web_tool.execute(
                    query=args.get("query", ""),
                    search_type=args.get("search_type", "general"),
                    max_results=int(args.get("max_results", 5)),
                    domain_filter=args.get("domain_filter"),
                )
            elif tool_name == "grep_search" and self.grep_tool:
                return self.grep_tool.execute(
                    pattern=args.get("pattern", ""),
                    is_regex=args.get("is_regex", False),
                    file_pattern=args.get("file_pattern"),
                    max_results=int(args.get("max_results", 50)),
                    case_sensitive=bool(args.get("case_sensitive", False)),
                    path_prefix=args.get("path_prefix"),
                    context_lines=int(args.get("context_lines", 2)),
                )
            elif tool_name == "grep_search" and not self.grep_tool:
                # 优先使用 _enrich_tool_queries 预置的富上下文 query，
                # 再退而使用原始 pattern；避免以单词级短串检索。
                q = (args.get("query") or args.get("pattern") or "").strip()
                if q:
                    return self.rag_tool.execute(query=q, top_k=args.get("top_k", 20))
                return ContextPiece(
                    source="grep_search",
                    content="grep_search requires repo_root; pattern was empty and no RAG query to fall back.",
                    relevance_score=0.0,
                    metadata={"error": "grep_unavailable", "fallback_skipped": True},
                )
            elif tool_name == "file_read" and not self.file_tool:
                return ContextPiece(
                    source="file_read",
                    content="file_read unavailable: repository root is not configured for this session.",
                    relevance_score=0.0,
                    metadata={"error": "no_repo_root"},
                )
            elif tool_name == "repo_map" and not self.repo_map_tool:
                q = (args.get("query") or "").strip()
                if not q:
                    q = "repository structure overview"
                return self.rag_tool.execute(query=q, top_k=args.get("top_k", 20))
            elif tool_name == "code_graph" and not self.graph_tool:
                parts = [p for p in (args.get("symbol_name"), args.get("file_path")) if p]
                q = " ".join(str(p) for p in parts) if parts else (args.get("query") or "").strip()
                if not q:
                    return ContextPiece(
                        source="code_graph",
                        content="code_graph unavailable and no symbol/file/query for RAG fallback.",
                        relevance_score=0.0,
                        metadata={"error": "no_graph_no_query"},
                    )
                return self.rag_tool.execute(query=q, top_k=args.get("top_k", 5))
            else:
                q = (args.get("query") or "").strip()
                if not q and tool_name == "grep_search":
                    q = (args.get("pattern") or "").strip()
                if not q:
                    return ContextPiece(
                        source=tool_name,
                        content=f"Unknown or unavailable tool '{tool_name}' with no query for rag_search fallback.",
                        relevance_score=0.0,
                        metadata={"error": "fallback_no_query", "requested_tool": tool_name},
                    )
                return self.rag_tool.execute(query=q, top_k=args.get("top_k", 20))
                
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
            "topic_coverage": QueryIntent.TOPIC_COVERAGE,
            "relationship": QueryIntent.RELATIONSHIP,
            "evidence": QueryIntent.EVIDENCE,
            "section_locator": QueryIntent.SECTION_LOCATOR,
            "repo_overview": QueryIntent.REPO_OVERVIEW,
            "followup_clarification": QueryIntent.FOLLOWUP_CLARIFICATION,
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
            "lexical_match": EvidenceType.LEXICAL_MATCH,
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
            role = "User" if msg.get("role") == "user" else "Assistant"
            parts.append(f"{role}: {msg.get('content', '')}")
        return "\n".join(parts)
    
    def _format_tool_history(self, history: List[ToolCall]) -> str:
        """格式化工具调用历史"""
        if not history:
            return "No prior tool calls"
        
        parts = []
        for call in history[-5:]:
            status = "✓" if call.success else "✗"
            parts.append(f"{status} {call.tool.value}({call.arguments}) -> {call.result[:200] if call.result else 'N/A'}...")
        return "\n".join(parts)
    
    def _format_trajectory(self, trajectory: List[Dict[str, Any]]) -> str:
        """格式化推理轨迹"""
        if not trajectory:
            return "No exploration trace"
        
        parts = []
        for i, step in enumerate(trajectory, 1):
            parts.append(f"Step {i}: {step.get('tool', 'unknown')} - {step.get('arguments', {})}")
        return "\n".join(parts)
    
    def _fallback_answer(self, state: AgentState) -> str:
        """生成回退答案"""
        answer_parts = [f"Analysis of '{state.original_question}':\n"]
        
        for piece in state.context_scratchpad[:5]:
            if piece.relevance_score > 0.3:
                answer_parts.append(f"**Source: {piece.source}**")
                if piece.file_path:
                    answer_parts.append(f"File: {piece.file_path}")
                answer_parts.append(piece.content[:500])
                answer_parts.append("---")
        
        if not answer_parts[1:]:
            answer_parts.append("Not enough relevant information was found to answer this question.")
        
        return "\n\n".join(answer_parts)


def create_agent_graph(
    vector_store_path: str,
    graph_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    max_iterations: int = 5,
    on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    language: str = "en",
) -> AgentGraphRunner:
    """创建 Agent 图执行器的工厂函数"""
    return AgentGraphRunner(
        vector_store_path=vector_store_path,
        graph_path=graph_path,
        repo_root=repo_root,
        max_iterations=max_iterations,
        on_event=on_event,
        language=language,
)


def run_agent(
    question: str,
    repo_url: str,
    vector_store_path: str,
    graph_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    max_iterations: int = 5,
    language: str = "en",
) -> Dict[str, Any]:
    """运行 Agent 的便捷函数"""
    runner = create_agent_graph(
        vector_store_path=vector_store_path,
        graph_path=graph_path,
        repo_root=repo_root,
        max_iterations=max_iterations,
        language=language,
    )
    
    return runner.run(
        question=question,
        repo_url=repo_url,
        conversation_history=conversation_history
    )
