"""
LangGraph 状态机定义

实现 Agent 的核心工作流：
规划 -> 检索 -> 反思 -> 再检索 -> 合成答案

使用 LangGraph 的状态图模型构建具有反思循环的智能代码理解 Agent。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Callable
from dataclasses import asdict

from src.agent.state import (
    AgentState, 
    ContextPiece, 
    ToolCall, 
    ToolType, 
    QueryIntent
)
from src.agent.prompts import (
    get_planner_prompt,
    get_tool_router_prompt,
    get_evaluator_prompt,
    get_synthesizer_prompt,
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


class AgentGraphRunner:
    """
    Agent 图执行器
    
    管理 Agent 的完整生命周期，包括：
    - 初始化工具
    - 执行规划
    - 迭代式上下文收集
    - 反思与评估
    - 答案合成
    """
    
    def __init__(
        self,
        vector_store_path: str,
        graph_path: Optional[str] = None,
        repo_root: Optional[str] = None,
        max_iterations: int = 5,
        on_event: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        """
        初始化 Agent 执行器
        
        Args:
            vector_store_path: 向量库路径
            graph_path: 代码图谱路径
            repo_root: 仓库根目录（用于文件读取）
            max_iterations: 最大反思循环次数
        """
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
        
        Args:
            question: 用户问题
            repo_url: 仓库 URL
            conversation_history: 对话历史
            
        Returns:
            包含答案、轨迹、来源等的结果字典
        """
        state = AgentState(
            original_question=question,
            repo_url=repo_url,
            vector_store_path=self.vector_store_path,
            graph_path=self.graph_path,
            conversation_history=conversation_history or [],
            max_iterations=self.max_iterations,
        )
        
        try:
            state = self._node_planner(state)
            
            while not state.is_ready and state.iteration_count < state.max_iterations:
                state = self._node_tool_executor(state)
                state = self._node_evaluator(state)
                state.iteration_count += 1
            
            state = self._node_synthesizer(state)
            
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
            "iterations": state.iteration_count,
            "error": state.error,
        }
    
    def _node_planner(self, state: AgentState) -> AgentState:
        """
        规划节点：分析问题意图，制定探索计划
        """
        logger.info(f"[Planner] Analyzing question: {state.original_question[:100]}...")
        self._emit_event("planning", {"status": "analyzing", "question": state.original_question[:200]})
        
        prompt = get_planner_prompt()
        history_text = self._format_history(state.conversation_history)
        
        messages = prompt.format_messages(
            question=state.original_question,
            conversation_history=history_text or "无对话历史"
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.2)
            plan_data = self._parse_json_response(response)
            
            if plan_data:
                intent_str = plan_data.get("intent", "implementation")
                state.query_intent = QueryIntent(intent_str) if intent_str in QueryIntent.__members__.values() else QueryIntent.IMPLEMENTATION
                state.rewritten_queries = plan_data.get("rewritten_queries", [state.original_question])
                state.exploration_plan = plan_data.get("exploration_plan", [])
                
                initial_tools = plan_data.get("initial_tools", [])
                if initial_tools:
                    for tool_info in initial_tools[:2]:
                        state.missing_pieces.append(
                            f"Use {tool_info.get('tool', 'rag_search')}: {tool_info.get('reason', 'gather initial context')}"
                        )
                
                logger.info(f"[Planner] Intent: {state.query_intent.value}, Plan steps: {len(state.exploration_plan)}")
                self._emit_event(
                    "planning",
                    {
                        "status": "planned",
                        "intent": state.query_intent.value,
                        "plan_steps": len(state.exploration_plan),
                        "initial_missing_count": len(state.missing_pieces),
                    },
                )
            else:
                state.query_intent = QueryIntent.IMPLEMENTATION
                state.rewritten_queries = [state.original_question]
                state.missing_pieces = ["Start with RAG search for relevant context"]
                
        except Exception as e:
            logger.warning(f"[Planner] Failed to parse plan: {e}")
            state.query_intent = QueryIntent.IMPLEMENTATION
            state.missing_pieces = ["Use rag_search to find relevant documentation"]
            self._emit_event("planning", {"status": "fallback", "reason": str(e)})
        
        return state
    
    def _node_tool_executor(self, state: AgentState) -> AgentState:
        """
        工具执行节点：根据缺失信息选择并执行工具
        """
        logger.info(f"[ToolExecutor] Iteration {state.iteration_count + 1}, missing: {state.missing_pieces[:2]}")
        
        prompt = get_tool_router_prompt()
        
        messages = prompt.format_messages(
            question=state.original_question,
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
                fallback_tools = [{"tool": "rag_search", "arguments": {"query": state.original_question}}]
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
        """构建本轮工具执行计划，支持并行多工具。"""
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

        # 轻量并行加速：首轮且尚未做全局概览时，自动补一个互补工具
        called_tools = {call.tool.value for call in state.tool_calls_history}
        if len(selected_tools) == 1:
            chosen = selected_tools[0]["tool"]
            if chosen == "rag_search" and "repo_map" not in called_tools and self.repo_map_tool:
                selected_tools.append({"tool": "repo_map", "arguments": {"include_signatures": True, "max_depth": 3}})
            elif chosen == "repo_map" and "rag_search" not in called_tools:
                selected_tools.append({"tool": "rag_search", "arguments": {"query": state.original_question, "top_k": 5}})

        dedup: Dict[str, Dict[str, Any]] = {}
        for item in selected_tools:
            key = f"{item['tool']}::{json.dumps(item['arguments'], ensure_ascii=False, sort_keys=True)}"
            dedup[key] = item
        selected_tools = list(dedup.values())[:3]
        return selected_tools

    def _execute_tool_batch(self, state: AgentState, selected_tools: List[Dict[str, Any]]) -> None:
        """并行执行工具批次并写入状态。"""
        started_at = time.time()
        batch_results: List[Dict[str, Any]] = []

        if len(selected_tools) <= 1:
            for item in selected_tools:
                tool_name = item["tool"]
                tool_args = item["arguments"]
                result = self._execute_tool(tool_name, tool_args)
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
                batch_results.append(
                    {
                        "tool": tool_name,
                        "success": success,
                        "relevance": float(getattr(result, "relevance_score", 0.0)) if result else 0.0,
                    }
                )
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
                    batch_results.append(
                        {
                            "tool": tool_name,
                            "success": success,
                            "relevance": float(getattr(result, "relevance_score", 0.0)) if result else 0.0,
                        }
                    )

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
    
    def _node_evaluator(self, state: AgentState) -> AgentState:
        """
        评估节点：评估上下文充分性，决定是否继续迭代
        """
        logger.info(f"[Evaluator] Evaluating context sufficiency (iteration {state.iteration_count + 1})")
        self._emit_event("evaluation", {"status": "start", "iteration": state.iteration_count + 1})
        
        prompt = get_evaluator_prompt()
        
        messages = prompt.format_messages(
            question=state.original_question,
            query_intent=state.query_intent.value if state.query_intent else "implementation",
            context_summary=state.get_context_summary(max_length=6000),
            tool_history=self._format_tool_history(state.tool_calls_history),
            iteration_count=state.iteration_count + 1,
            max_iterations=state.max_iterations
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.1)
            eval_data = self._parse_json_response(response)
            
            if eval_data:
                state.is_ready = bool(eval_data.get("is_sufficient", False))
                state.confidence_score = float(eval_data.get("confidence_score", 0.5))
                state.missing_pieces = [str(x) for x in eval_data.get("missing_pieces", [])]
                
                reflection = eval_data.get("reflection_note", "")
                if reflection:
                    state.reflection_notes.append(reflection)
                
                logger.info(
                    f"[Evaluator] Sufficient: {state.is_ready}, "
                    f"Confidence: {state.confidence_score:.2f}, "
                    f"Missing: {len(state.missing_pieces)}"
                )
                self._emit_event(
                    "evaluation",
                    {
                        "status": "done",
                        "iteration": state.iteration_count + 1,
                        "is_sufficient": state.is_ready,
                        "confidence": state.confidence_score,
                        "missing_count": len(state.missing_pieces),
                        "missing": state.missing_pieces[:3],
                    },
                )
            else:
                if state.iteration_count >= 2 and len(state.context_scratchpad) >= 3:
                    state.is_ready = True
                    state.confidence_score = 0.6
                    
        except Exception as e:
            logger.warning(f"[Evaluator] Evaluation failed: {e}")
            if state.iteration_count >= 2:
                state.is_ready = True
                state.confidence_score = 0.5
            self._emit_event("evaluation", {"status": "error", "error": str(e)})
        
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
            context_summary=state.get_context_summary(max_length=8000),
            trajectory=self._format_trajectory(state.get_trajectory()),
            conversation_history=self._format_history(state.conversation_history) or "无对话历史"
        )
        
        try:
            response = self.llm.chat(messages, temperature=0.3)
            synth_data = self._parse_json_response(response)
            
            if synth_data:
                state.final_answer = synth_data.get("answer", response)
                state.mermaid_diagram = synth_data.get("mermaid")
                state.sources = synth_data.get("sources", [])
                
                caveats = synth_data.get("caveats", [])
                if caveats and state.confidence_score < 0.7:
                    state.final_answer += "\n\n**注意事项：**\n" + "\n".join(f"- {c}" for c in caveats)
            else:
                state.final_answer = response
                
            for piece in state.context_scratchpad:
                if piece.file_path and piece.file_path not in state.sources:
                    state.sources.append(piece.file_path)
                    
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
            },
        )
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
    """
    创建 Agent 图执行器的工厂函数
    
    Args:
        vector_store_path: 向量库路径
        graph_path: 代码图谱路径
        repo_root: 仓库根目录
        max_iterations: 最大迭代次数
        
    Returns:
        AgentGraphRunner 实例
    """
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
    """
    运行 Agent 的便捷函数
    
    Args:
        question: 用户问题
        repo_url: 仓库 URL
        vector_store_path: 向量库路径
        graph_path: 代码图谱路径
        repo_root: 仓库根目录
        conversation_history: 对话历史
        max_iterations: 最大迭代次数
        
    Returns:
        Agent 执行结果
    """
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
