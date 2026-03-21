"""
Agent Runner 高级执行器

提供 Agent 的高级执行接口，包括：
- 异步执行支持
- 流式输出支持（用于实时展示 Agent 思考过程）
- 会话管理
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Any, Optional, AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import datetime

from src.agent.graph import AgentGraphRunner, create_agent_graph
from src.agent.state import AgentState, ToolCall, ContextPiece

logger = logging.getLogger("app.agent.runner")


@dataclass
class AgentEvent:
    """
    Agent 执行过程中的事件
    
    事件类型：
    - 'planning': 规划阶段 {status, question, intent, entities, plan_steps}
    - 'tool_call': 工具调用 {status, iteration, tool, operation, query, file_path, symbol_name, tools, elapsed_ms, results}
    - 'evaluation': 评估阶段 {status, iteration, is_sufficient, confidence, confidence_level, missing}
    - 'synthesis': 合成阶段 {status, answer_length, sources_count}
    - 'answer_delta': 答案增量 {delta}
    - 'complete': 完成 {answer_length, iterations, sources_count}
    - 'final_result': 最终结果 {answer, sources, trajectory, ...}
    - 'error': 错误 {error}
    """
    event_type: str
    data: Dict[str, Any]
    timestamp: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
        }
    
    @staticmethod
    def create_tool_call_event(
        status: str,
        iteration: int,
        tools: List[Dict[str, Any]] = None,
        elapsed_ms: int = None,
        results: List[Dict[str, Any]] = None
    ) -> "AgentEvent":
        """创建工具调用事件"""
        data = {
            "status": status,
            "iteration": iteration,
        }
        
        if tools:
            # 格式化工具信息为更详细的描述
            formatted_tools = []
            for tool in tools:
                tool_name = tool.get("tool", "unknown")
                args = tool.get("arguments", {})
                
                formatted = {
                    "tool": tool_name,
                    "description": _get_tool_description(tool_name, args),
                }
                
                # 添加关键参数用于前端展示
                if tool_name == "rag_search":
                    formatted["query"] = args.get("query", "")[:100]
                elif tool_name == "code_graph":
                    formatted["operation"] = args.get("operation", "")
                    formatted["symbol_name"] = args.get("symbol_name", "")
                elif tool_name == "file_read":
                    formatted["file_path"] = args.get("file_path", "")
                elif tool_name == "repo_map":
                    formatted["include_signatures"] = args.get("include_signatures", True)
                elif tool_name == "grep_search":
                    formatted["pattern"] = args.get("pattern", "")[:100]
                    formatted["is_regex"] = args.get("is_regex", False)
                    formatted["case_sensitive"] = args.get("case_sensitive", False)
                
                formatted_tools.append(formatted)
            
            data["tools"] = formatted_tools
            data["parallel"] = len(tools) > 1
        
        if elapsed_ms is not None:
            data["elapsed_ms"] = elapsed_ms
        
        if results:
            data["results"] = results
        
        return AgentEvent(
            event_type="tool_call",
            data=data,
            timestamp=datetime.now().isoformat()
        )


class AgentRunner:
    """
    Agent 高级执行器
    
    封装 AgentGraphRunner，提供更高级的接口：
    - 支持异步执行
    - 支持事件回调（用于流式输出）
    - 支持执行取消
    """
    
    def __init__(
        self,
        vector_store_path: str,
        graph_path: Optional[str] = None,
        repo_root: Optional[str] = None,
        max_iterations: int = 5,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
    ):
        """
        初始化 Agent Runner
        
        Args:
            vector_store_path: 向量库路径
            graph_path: 代码图谱路径
            repo_root: 仓库根目录
            max_iterations: 最大迭代次数
            on_event: 事件回调函数
        """
        self.vector_store_path = vector_store_path
        self.graph_path = graph_path
        self.repo_root = repo_root
        self.max_iterations = max_iterations
        self.on_event = on_event
        
        self._runner: Optional[AgentGraphRunner] = None
        self._cancelled = False
    
    def _get_runner(self) -> AgentGraphRunner:
        """懒加载 AgentGraphRunner"""
        if self._runner is None:
            def graph_event_callback(event_type: str, data: Dict[str, Any]) -> None:
                self._emit_event(event_type, data)

            self._runner = create_agent_graph(
                vector_store_path=self.vector_store_path,
                graph_path=self.graph_path,
                repo_root=self.repo_root,
                max_iterations=self.max_iterations,
                on_event=graph_event_callback,
            )
        return self._runner
    
    def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """发送事件"""
        event = AgentEvent(
            event_type=event_type,
            data=data,
            timestamp=datetime.now().isoformat()
        )
        
        if self.on_event:
            try:
                self.on_event(event)
            except Exception as e:
                logger.warning(f"Event callback failed: {e}")
    
    def run_sync(
        self,
        question: str,
        repo_url: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        同步执行 Agent
        
        Args:
            question: 用户问题
            repo_url: 仓库 URL
            conversation_history: 对话历史
            
        Returns:
            Agent 执行结果
        """
        self._cancelled = False
        runner = self._get_runner()
        
        self._emit_event("planning", {
            "question": question,
            "status": "Analyzing question intent...",
        })
        
        result = runner.run(
            question=question,
            repo_url=repo_url,
            conversation_history=conversation_history
        )
        
        self._emit_event("complete", {
            "answer_length": len(result.get("answer", "")),
            "iterations": result.get("iterations", 0),
            "sources_count": len(result.get("sources", []))
        })
        
        return result
    
    async def run_async(
        self,
        question: str,
        repo_url: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        异步执行 Agent
        
        Args:
            question: 用户问题
            repo_url: 仓库 URL
            conversation_history: 对话历史
            
        Returns:
            Agent 执行结果
        """
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self.run_sync(question, repo_url, conversation_history)
        )
        return result
    
    async def run_streaming(
        self,
        question: str,
        repo_url: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> AsyncGenerator[AgentEvent, None]:
        """
        流式执行 Agent，通过 async generator 返回事件
        
        Args:
            question: 用户问题
            repo_url: 仓库 URL
            conversation_history: 对话历史
            
        Yields:
            AgentEvent: 执行过程中的事件
        """
        self._cancelled = False
        events_queue: asyncio.Queue[Optional[AgentEvent]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        
        def event_callback(event: AgentEvent):
            loop.call_soon_threadsafe(
                events_queue.put_nowait, event
            )
        
        original_callback = self.on_event
        self.on_event = event_callback
        
        async def run_in_background():
            try:
                result = await self.run_async(question, repo_url, conversation_history)
                final_event = AgentEvent(
                    event_type="final_result",
                    data=result,
                    timestamp=datetime.now().isoformat()
                )
                await events_queue.put(final_event)
            except Exception as e:
                error_event = AgentEvent(
                    event_type="error",
                    data={"error": str(e)},
                    timestamp=datetime.now().isoformat()
                )
                await events_queue.put(error_event)
            finally:
                await events_queue.put(None)
        
        task = asyncio.create_task(run_in_background())
        
        try:
            while True:
                event = await events_queue.get()
                if event is None:
                    break
                yield event
        finally:
            self.on_event = original_callback
            if not task.done():
                task.cancel()
    
    def cancel(self) -> None:
        """取消正在执行的 Agent"""
        self._cancelled = True
        logger.info("Agent execution cancelled")
    
    @staticmethod
    def format_trajectory_for_display(trajectory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        格式化轨迹用于前端显示
        
        Args:
            trajectory: 原始轨迹数据
            
        Returns:
            格式化后的轨迹，适合前端渲染
        """
        formatted = []
        
        for i, step in enumerate(trajectory):
            tool = step.get("tool", "unknown")
            args = step.get("arguments", {})
            result = step.get("result", "")
            success = step.get("success", True)
            
            tool_icons = {
                "rag_search": "🔍",
                "code_graph": "🕸️",
                "file_read": "📄",
                "repo_map": "🗺️",
                "grep_search": "⚡",
            }
            
            entry: Dict[str, Any] = {
                "step": i + 1,
                "icon": tool_icons.get(tool, "🔧"),
                "tool": tool,
                "description": _get_tool_description(tool, args),
                "success": success,
                "preview": result[:200] + "..." if len(result) > 200 else result,
            }
            if step.get("duration_ms") is not None:
                entry["duration_ms"] = step["duration_ms"]
            if step.get("metrics"):
                entry["metrics"] = step["metrics"]
            if step.get("used_fallback"):
                entry["used_fallback"] = True
            formatted.append(entry)
        
        return formatted


def _get_tool_description(tool: str, args: Dict[str, Any]) -> str:
    """
    生成工具调用的人类可读描述
    
    用于前端展示具体的工具调用步骤，类似 DeepWiki 风格。
    """
    if tool == "rag_search":
        query = args.get("query", "")
        if len(query) > 60:
            query = query[:60] + "..."
        return f"Search knowledge base: \"{query}\""
    
    elif tool == "code_graph":
        op = args.get("operation", "")
        symbol = args.get("symbol_name", "")
        file_path = args.get("file_path", "")
        
        op_descriptions = {
            "find_definition": f"Find definition of `{symbol}`",
            "find_callers": f"Find callers of `{symbol}`",
            "find_callees": f"Find callees of `{symbol}`",
            "get_all_symbols": "List all symbols",
            "get_file_symbols": f"List symbols in `{file_path}`",
        }
        return op_descriptions.get(op, f"Code graph: {op}({symbol})")
    
    elif tool == "file_read":
        path = args.get("file_path", "")
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        
        if path:
            parts = path.split("/")
            short_path = "/".join(parts[-2:]) if len(parts) > 2 else path
            
            if start_line and end_line:
                return f"Read `{short_path}` lines {start_line}-{end_line}"
            elif start_line:
                return f"Read `{short_path}` from line {start_line}"
            else:
                return f"Read file `{short_path}`"
        return "Read file"
    
    elif tool == "repo_map":
        include_signatures = args.get("include_signatures", True)
        max_depth = args.get("max_depth", 3)
        if include_signatures:
            return f"Repository map (signatures, depth {max_depth})"
        return f"Repository tree (depth {max_depth})"
    
    elif tool == "grep_search":
        pattern = args.get("pattern", "")
        if len(pattern) > 60:
            pattern = pattern[:60] + "..."
        is_regex = args.get("is_regex", False)
        mode = "regex" if is_regex else "text"
        return f"Grep search ({mode}): \"{pattern}\""
    
    else:
        return f"Run tool {tool}"


class AgentSession:
    """
    Agent 会话管理器
    
    管理多轮对话的上下文和状态。
    """
    
    def __init__(
        self,
        session_id: str,
        repo_url: str,
        vector_store_path: str,
        graph_path: Optional[str] = None,
        repo_root: Optional[str] = None
    ):
        """
        创建会话
        
        Args:
            session_id: 会话 ID
            repo_url: 仓库 URL
            vector_store_path: 向量库路径
            graph_path: 代码图谱路径
            repo_root: 仓库根目录
        """
        self.session_id = session_id
        self.repo_url = repo_url
        self.conversation_history: List[Dict[str, str]] = []
        
        self.runner = AgentRunner(
            vector_store_path=vector_store_path,
            graph_path=graph_path,
            repo_root=repo_root
        )
    
    async def chat(self, question: str) -> Dict[str, Any]:
        """
        发送消息并获取回复
        
        Args:
            question: 用户问题
            
        Returns:
            Agent 回复
        """
        self.conversation_history.append({
            "role": "user",
            "content": question
        })
        
        result = await self.runner.run_async(
            question=question,
            repo_url=self.repo_url,
            conversation_history=self.conversation_history[:-1]
        )
        
        answer = result.get("answer", "")
        self.conversation_history.append({
            "role": "assistant",
            "content": answer
        })
        
        return result
    
    def clear_history(self) -> None:
        """清空对话历史"""
        self.conversation_history.clear()
    
    def get_history(self) -> List[Dict[str, str]]:
        """获取对话历史"""
        return self.conversation_history.copy()
