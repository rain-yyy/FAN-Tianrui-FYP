# 后端 Agent Chat 模式架构文档

> 本文档详细记录后端 Agent 模式的工作原理、流程及各模块代码位置。

---

## 一、概述

Agent Chat 模式是一种**具备反思循环（Reflection Loop）的智能代码理解系统**。借鉴 Sourcegraph Cody 的 Agentic Context Fetching 和 DeepWiki 的结构化图谱理念，实现：

1. **分析问题意图**并制定探索计划
2. **迭代式收集上下文**（RAG、代码图谱、文件读取等）
3. **自我反思**评估信息充分性
4. **生成带 Mermaid 图表和溯源的答案**

与 RAG 模式的单次检索不同，Agent 模式会**多轮迭代**直到上下文充分或达到最大迭代次数。

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            HTTP API 层                                       │
│  docker/scripts/api.py                                                       │
│  - POST /agent/chat          → 同步 Agent 问答                               │
│  - POST /agent/chat/stream   → 流式 Agent 问答 (SSE)                          │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Agent Runner 层                                   │
│  docker/src/agent/runner.py                                                   │
│  - AgentRunner          → 封装 GraphRunner，支持 async/streaming              │
│  - AgentSession         → 多轮对话会话管理                                    │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Agent Graph 层                                    │
│  docker/src/agent/graph.py                                                    │
│  - AgentGraphRunner      → 核心图执行器                                       │
│  - create_agent_graph()  → 工厂函数                                           │
│  - run_agent()           → 便捷入口函数                                       │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Planner     │ →  │ Tool Executor │ →  │  Evaluator    │
│  规划节点     │    │  工具执行节点   │    │  评估节点      │
└───────────────┘    └───────┬───────┘    └───────────────┘
                             │                       │
                             │    ← 反思循环 ←───────┘
                             │
                             ▼
                    ┌───────────────┐
                    │  Synthesizer  │
                    │  合成节点     │
                    └───────────────┘
```

---

## 三、核心工作流程

### 3.1 主流程图

```
用户问题 → [Planner] → state (intent, plan, missing_pieces)
                              │
                              ▼
         ┌────────────────────────────────────────────┐
         │  while !is_ready && iteration < max (5):     │
         │    [Tool Executor] → 选工具、执行、收集上下文  │
         │    [Evaluator]     → 评估充分性、更新 missing │
         │    iteration += 1                            │
         └────────────────────────────────────────────┘
                              │
                              ▼
                    [Synthesizer] → 生成最终答案
                              │
                              ▼
                    返回 answer + mermaid + sources + trajectory
```

### 3.2 四个核心节点说明

| 节点 | 职责 | 输出到 State |
|------|------|--------------|
| **Planner** | 分析问题意图、分类 (concept/implementation/architecture/debugging/usage)、生成探索计划、初选工具 | `query_intent`, `rewritten_queries`, `exploration_plan`, `missing_pieces`, `initial_tools` |
| **Tool Executor** | 根据 missing_pieces 调用 LLM 选工具，并行执行工具，收集结果 | `context_scratchpad`, `tool_calls_history` |
| **Evaluator** | 评估上下文是否充分，计算置信度，更新缺失信息列表 | `is_ready`, `confidence_score`, `missing_pieces`, `reflection_notes` |
| **Synthesizer** | 基于上下文生成最终答案、Mermaid 图表、引用来源 | `final_answer`, `mermaid_diagram`, `sources` |

---

## 四、代码位置索引

### 4.1 API 层

| 文件 | 行号 | 说明 |
|------|------|------|
| `docker/scripts/api.py` | 317-439 | `POST /agent/chat` 同步 Agent 接口 |
| `docker/scripts/api.py` | 442-523 | `POST /agent/chat/stream` 流式 Agent 接口 |
| `docker/scripts/api.py` | 24 | 导入 `run_agent`, `AgentRunner` |

**请求体**: `question`, `repo_url`, `user_id`, `chat_id?`, `conversation_history?`, `current_page_context?`  

**响应**: `answer`, `mermaid`, `sources`, `trajectory`, `confidence`, `iterations`, `chat_id`, `repo_url`

### 4.2 Agent 模块入口

| 文件 | 说明 |
|------|------|
| `docker/src/agent/__init__.py` | Agent 模块统一导出：`run_agent`, `AgentGraphRunner`, `AgentRunner`, `create_agent_graph`, `AgentState`, `AgentEvent`, tools, prompts |

### 4.3 Runner 层

| 文件 | 类/函数 | 说明 |
|------|---------|------|
| `docker/src/agent/runner.py` | `AgentRunner` | 封装 `AgentGraphRunner`，提供 `run_sync`, `run_async`, `run_streaming` |
| `docker/src/agent/runner.py` | `AgentEvent` | 流式事件：`planning`, `tool_call`, `evaluation`, `synthesis`, `complete`, `error` |
| `docker/src/agent/runner.py` | `AgentSession` | 多轮对话会话管理 |
| `docker/src/agent/runner.py` | L78-93 | 懒加载 `AgentGraphRunner`，注册 `on_event` 回调 |
| `docker/src/agent/runner.py` | L170-229 | `run_streaming` 异步生成器实现 |

### 4.4 Graph 层（核心执行逻辑）

| 文件 | 类/函数 | 行号 | 说明 |
|------|---------|------|------|
| `docker/src/agent/graph.py` | `AgentGraphRunner` | 45-457 | 图执行器，管理四节点流程 |
| `docker/src/agent/graph.py` | `run()` | 98-145 | 主执行入口，串行调用 Planner → (ToolExecutor + Evaluator 循环) → Synthesizer |
| `docker/src/agent/graph.py` | `_node_planner()` | 148-201 | 规划节点：意图解析、探索计划、初始工具 |
| `docker/src/agent/graph.py` | `_node_tool_executor()` | 203-264 | 工具执行节点：LLM 选工具、并行执行、收集上下文 |
| `docker/src/agent/graph.py` | `_execute_tool_batch()` | 303-367 | 工具批量执行（支持 ThreadPoolExecutor 并行） |
| `docker/src/agent/graph.py` | `_node_evaluator()` | 369-325 | 评估节点：充分性判断、置信度、missing_pieces |
| `docker/src/agent/graph.py` | `_node_synthesizer()` | 327-372 | 合成节点：生成答案、Mermaid、sources |
| `docker/src/agent/graph.py` | `_execute_tool()` | 374-405 | 单工具调度：rag_search / code_graph / file_read / repo_map |
| `docker/src/agent/graph.py` | `create_agent_graph()` | 458-480 | 工厂函数 |
| `docker/src/agent/graph.py` | `run_agent()` | 483-517 | 便捷入口函数 |

### 4.5 状态定义

| 文件 | 类/枚举 | 说明 |
|------|---------|------|
| `docker/src/agent/state.py` | `AgentState` | 核心状态：original_question, query_intent, context_scratchpad, missing_pieces, tool_calls_history, is_ready, confidence_score, final_answer, mermaid_diagram, sources |
| `docker/src/agent/state.py` | `ToolType` | 枚举：rag_search, code_graph, file_read, repo_map |
| `docker/src/agent/state.py` | `QueryIntent` | 枚举：concept, implementation, architecture, debugging, usage |
| `docker/src/agent/state.py` | `ContextPiece` | 上下文片段：source, content, file_path, relevance_score |
| `docker/src/agent/state.py` | `ToolCall` | 工具调用记录：tool, arguments, result, success |

### 4.6 工具集

| 文件 | 工具类 | 说明 |
|------|--------|------|
| `docker/src/agent/tools/__init__.py` | - | 导出 RAGSearchTool, CodeGraphTool, FileReadTool, RepoMapTool |
| `docker/src/agent/tools/rag_tool.py` | `RAGSearchTool` | FAISS+BM25 混合检索，HyDE 增强，返回 ContextPiece |
| `docker/src/agent/tools/graph_tool.py` | `CodeGraphTool` | NetworkX 图谱查询：find_definition, find_callers, find_callees, get_class_hierarchy, get_file_symbols, get_all_symbols |
| `docker/src/agent/tools/file_tool.py` | `FileReadTool` | 精准读取仓库内文件指定行范围 |
| `docker/src/agent/tools/file_tool.py` | `RepoMapTool` | 仓库目录树、文件统计、关键类/函数签名 |

### 4.7 Prompt 定义

| 文件 | Prompt | 说明 |
|------|--------|------|
| `docker/src/agent/prompts.py` | `QUERY_PLANNER_PROMPT` | 意图分类、探索计划、initial_tools |
| `docker/src/agent/prompts.py` | `TOOL_ROUTER_PROMPT` | 工具选择与参数规划，支持并行多工具 |
| `docker/src/agent/prompts.py` | `EVALUATOR_PROMPT` | 上下文充分性评估、confidence_score、missing_pieces |
| `docker/src/agent/prompts.py` | `ANSWER_SYNTHESIZER_PROMPT` | 答案合成、Mermaid 图表、sources 引用 |

---

## 五、数据流详解

### 5.1 API 请求 → Agent 执行

```
1. 校验 question, repo_url, user_id
2. 创建/获取 chat_id（Supabase）
3. 写入用户消息
4. 从 Supabase 获取 vector_store_path, graph_path, repo_root
5. 若有 current_page_context，拼接到 enhanced_question
6. 调用 run_agent() 或 AgentRunner.run_streaming()
7. 保存 assistant 消息及 metadata
8. 返回 JSON 或 SSE 流
```

### 5.2 State 在节点间的传递

```
Planner:
  - 输入: state (original_question, conversation_history)
  - 输出: state (query_intent, rewritten_queries, exploration_plan, missing_pieces)

Tool Executor:
  - 输入: state (context_summary, missing_pieces, tool_history, exploration_plan)
  - 输出: state (context_scratchpad += new pieces, tool_calls_history += new calls)

Evaluator:
  - 输入: state (context_summary, tool_history, iteration_count)
  - 输出: state (is_ready, confidence_score, missing_pieces, reflection_notes)

Synthesizer:
  - 输入: state (context_summary, trajectory, conversation_history)
  - 输出: state (final_answer, mermaid_diagram, sources)
```

### 5.3 工具并行与互补策略

- 首轮若只选 `rag_search`，且尚未调用过 `repo_map`，则自动补 `repo_map`
- 首轮若只选 `repo_map`，且尚未调用 `rag_search`，则自动补 `rag_search`
- 多工具时使用 `ThreadPoolExecutor` 并行执行，最多 3 个 worker
- 每轮最多 3 个工具，去重后执行

---

## 六、流式输出 (SSE)

| 事件类型 | 触发时机 | 数据内容 |
|----------|----------|----------|
| `planning` | Planner 开始/完成 | status, question, intent, plan_steps |
| `tool_call` | 工具选型/执行开始/执行完成 | status, iteration, tools, results, elapsed_ms |
| `evaluation` | Evaluator 开始/完成 | status, is_sufficient, confidence, missing |
| `synthesis` | Synthesizer 开始/完成 | status, answer_length, sources_count |
| `complete` | 全部结束 | answer, mermaid, sources, trajectory, confidence |
| `error` | 发生异常 | error |

前端通过 `EventSource` 监听 `text/event-stream` 即可实时展示 Agent 思考过程。

---

## 七、依赖关系

```
api.py
  └─ run_agent / AgentRunner  ← agent/__init__.py
       └─ AgentGraphRunner    ← agent/graph.py
            ├─ AgentState     ← agent/state.py
            ├─ Prompts        ← agent/prompts.py
            └─ Tools
                 ├─ RAGSearchTool  ← agent/tools/rag_tool.py (依赖 core/retrieval, ingestion/kb_loader)
                 ├─ CodeGraphTool  ← agent/tools/graph_tool.py (依赖 NetworkX)
                 ├─ FileReadTool    ← agent/tools/file_tool.py
                 └─ RepoMapTool     ← agent/tools/file_tool.py
```

---

## 八、配置与环境

| 配置项 | 来源 | 说明 |
|--------|------|------|
| `vector_store_path` | Supabase `repo_information` | 向量库路径 |
| `graph_path` | Supabase `repo_information` | 代码图谱 JSON 路径 |
| `repo_root` | Supabase `repo_information` | 仓库本地根目录 |
| `max_iterations` | 默认 5 | 反思循环最大次数 |
| LLM 模型 | `CONFIG` / `rag_answer` | 所有节点共用该配置 |

---

## 九、与 RAG 模式对比

| 维度 | RAG 模式 | Agent 模式 |
|------|----------|-------------|
| 入口 | `POST /chat` | `POST /agent/chat` |
| 实现 | `docker/src/core/chat.py` | `docker/src/agent/` |
| 检索 | 单次 HyDE + 混合检索 + MMR | 多轮迭代，可选 RAG / 图谱 / 文件 / 仓库结构 |
| 输出 | answer + sources | answer + mermaid + sources + trajectory + confidence |
| 适用场景 | 简单问答、文档检索 | 架构分析、调用链追踪、深度代码理解 |

---

*文档版本：基于当前代码库生成，如有变更请同步更新。*
