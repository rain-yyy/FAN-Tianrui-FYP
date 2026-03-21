# 后端 Agent Chat 模式架构文档

> 记录 Agent 模式的工作原理、数据流与主要代码位置；与当前 `docker/src/agent` 与 `docker/scripts/api.py` 保持一致。

---

## 一、概述

Agent Chat 是带 **受控反思循环** 的代码理解流程：规划 → 多轮工具执行与评估 → 证据与锚点整理 → 合成答案。与单次 RAG（`POST /chat`）相比，可组合 **向量检索、代码图谱、按行读文件、仓库地图** 等工具。

**HTTP 入口**（`docker/scripts/api.py`）

- `POST /agent/chat` — 同步 JSON（返回字段见 API 文档；部分图状态字段仅流式完整返回）  
- `POST /agent/chat/stream` — SSE，`complete` 事件携带 `run_agent()` 的完整字典 + `chat_id` / `repo_url`

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  HTTP API — docker/scripts/api.py                                            │
│  POST /agent/chat  |  POST /agent/chat/stream                                │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  AgentRunner — docker/src/agent/runner.py                                    │
│  run_sync / run_async / run_streaming，事件回调 → SSE                          │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  AgentGraphRunner — docker/src/agent/graph.py                                 │
│  create_agent_graph() / run() / run_agent()                                  │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Planner     │ →  │ Tool Executor │ →  │  Evaluator    │
└───────────────┘    └───────┬───────┘    └───────────────┘
                             │                       │
                             │    ← 迭代直至就绪或达上限 ←
                             ▼
                    ┌───────────────┐
                    │  Synthesizer  │
                    └───────┬───────┘
                             ▼
                    ┌───────────────┐
                    │ Memory writeback│（高置信度事实）
                    └───────────────┘
```

---

## 三、核心流程（逻辑）

1. **会话压缩**（`_compress_session`）：对话较长时压缩历史，保留实体与近期轮次。  
2. **规划**（`_node_planner`）：意图分类、探索计划、可选快速路径（无需工具则直接返回答案）。  
3. **循环**：`_node_tool_executor` → `_node_evaluator`，直到 `is_ready` 或 `iteration_count >= max_iterations` 或硬门控触发。  
4. **证据**：`convert_context_to_evidence` 将上下文转为证据卡片（含引用过滤，如排除无意义 `unknown`）。  
5. **合成**（`_node_synthesizer`）：生成 `final_answer`、`mermaid_diagram`、`sources`、`caveats`。  
6. **记忆回写**（`_writeback_memory`）：仅在高置信度下写入长期模块事实。

---

## 四、节点与状态

### 4.1 节点职责（简表）

| 节点 | 职责 |
|------|------|
| Planner | 意图、改写查询、探索计划、首轮工具倾向 |
| Tool Executor | LLM 路由工具、并行执行、写入 `context_scratchpad` / `tool_calls_history` |
| Evaluator | 是否充分、`confidence_score`、`confidence_level`、`missing_pieces` |
| Synthesizer | 最终答案、Mermaid、sources、caveats（JSON 解析失败时有回退答案） |

### 4.2 `QueryIntent`（`docker/src/agent/state.py`）

扩展意图枚举包括但不限于：`location`、`mechanism`、`call_chain`、`impact_analysis`、`debugging`、`architecture`、`change_guidance`、`concept`、`usage`、`general`，并保留 `implementation` 兼容旧逻辑。

### 4.3 `ToolType`（枚举）

与 `docker/src/agent/state.py` 中 `ToolType` 一致，共 **5 种**：

| 值 | 实现类 | 作用 |
|----|--------|------|
| `rag_search` | `RAGSearchTool` | 对已向量化知识库的 **混合检索**（FAISS 密集 + BM25 稀疏，可选 HyDE、MMR），返回语义相关片段 |
| `code_graph` | `CodeGraphTool` | 对预构建 **NetworkX node-link JSON 图谱** 做结构化查询（定义/调用/继承/文件符号等） |
| `file_read` | `FileReadTool` | 在 `repo_root` 下按 **相对路径** 读取源文件，支持行区间（默认单次最多约 100 行） |
| `repo_map` | `RepoMapTool` | 生成仓库 **目录/模块级结构概览**（面向 Python / TS / TSX / JS 等） |
| `grep_search` | `GrepSearchTool` | 对 **`repo_root` 下实时仓库文件** 做词法检索（优先 **ripgrep**，不可用时 Python 回退），带行上下文与 `grep_matches` 元数据 |

**`code_graph` 在代码中支持的 `operation`**（`docker/src/agent/tools/graph_tool.py` → `execute`）：

- `find_definition`、`find_callers`、`find_callees`
- `get_class_hierarchy`、`get_file_symbols`、`get_all_symbols`
- `find_imports`、`get_module_dependencies`（已实现；`prompts.py` 里 Tool Router 的示例表未全部列出，模型仍可能在 JSON 中选用）

**各工具典型参数**（路由 LLM 输出 JSON 中的 `arguments`）：

- `rag_search`：`query`、`top_k`（默认 5）
- `repo_map`：`include_signatures`、`max_depth`
- `file_read`：`file_path`、`start_line`、`end_line`（可省略行号，由工具默认窗口）
- `code_graph`：`operation`、`symbol_name`、`file_path`（按 operation 取舍）
- `grep_search`：`pattern`、`is_regex`、`file_pattern`、可选 `case_sensitive`、`path_prefix`、`max_results`、`context_lines`

**运行时是否真正可用**（`AgentGraphRunner.__init__` / `_execute_tool`）：

- `rag_search`：**必有**（依赖有效 `vector_store_path` 与向量库加载；加载失败时工具内部会表现为错误或低质量结果）。
- `code_graph`：仅当传入 **`graph_path` 且文件存在** 时实例化 `CodeGraphTool`；否则回退到 **`rag_search`**（使用 `symbol_name` / `file_path` 拼查询；若无则返回明确错误片段，避免空 `query`）。
- `file_read` / `repo_map` / `grep_search`：仅当传入 **`repo_root`** 时可用。`grep_search` 不可用时，若有 `pattern` 则 **用其作为 `rag_search` 的 query** 回退；`file_read` 不可用时返回错误说明而非空检索。
- **工具耗时观测**：每轮 `tool_call` 的 `done` 事件中，`results[]` 可含 **`duration_ms`**（单工具耗时）、**`metrics`**（如 `num_matches`、`files_searched`、`truncated`、`engine`）及 **`used_fallback`**（Python 回退）。

**并行与上限**：每轮由 Tool Router 选型，`_build_tool_plan` 会去重并 **截断为最多 3 个** 工具；执行侧 `ThreadPoolExecutor` 的 `max_workers` 与批次长度对齐（最多 3）。

### 4.4 工具互补（实现要点，见 `graph.py` 工具路由）

- 首轮倾向同时覆盖 **仓库结构**（`repo_map`）与 **图谱**（`code_graph`），随意图调整深度/操作。  
- 若仅选 `rag_search`，会尝试补充 `code_graph` 或 `repo_map`（在尚未调用过对应工具且对应工具已实例化时）。  
- 多工具并行时使用 `ThreadPoolExecutor`，worker 数与每轮工具上限在图内配置（如最多 3 个工具、去重）。

---

## 五、代码位置索引

| 区域 | 路径 |
|------|------|
| API Agent 同步/流式 | `docker/scripts/api.py`（`/agent/chat`、`/agent/chat/stream`） |
| 模块导出 | `docker/src/agent/__init__.py` |
| Runner | `docker/src/agent/runner.py`（`AgentRunner`、`AgentEvent`、`run_streaming`） |
| 图与 `run_agent` | `docker/src/agent/graph.py` |
| 状态 / 证据 / 锚点 | `docker/src/agent/state.py` |
| Prompt | `docker/src/agent/prompts.py` |
| 工具 | `docker/src/agent/tools/rag_tool.py`、`graph_tool.py`、`file_tool.py`（含 `RepoMapTool`）、`grep_tool.py` |

**`run_agent(..., language="zh")`**：传入合成与部分 prompt 的英文/中文指令分支。

---

## 六、流式事件（SSE）

`AgentRunner.run_streaming` 将图内 `_emit_event` 与最终 `final_result` 透出。常见 `event` 名：

| 类型 | 含义 |
|------|------|
| `planning` | 分析/回退原因等 |
| `tool_call` | 选型、进行中、完成批次（含批次 `elapsed_ms`、**`results[]` 内单工具 `duration_ms` / `metrics`**） |
| `evaluation` | 评估开始/结束、置信度、缺失点 |
| `synthesis` | 合成开始/结束（长度、来源数、`confidence_level` 等） |
| `complete`（由 API 层发出） | 完整结果 + `chat_id`、`repo_url` |
| `error` | 异常信息 |

同步 `run_sync` 在图跑完后还会发一次摘要性 `complete`（长度统计），与 HTTP 流式的 `complete` 不同层，调用时注意区分。

---

## 七、与 RAG 模式对比

| 维度 | RAG `POST /chat` | Agent `POST /agent/chat` |
|------|------------------|---------------------------|
| 实现 | `docker/src/core/chat.py` | `docker/src/agent/` |
| 检索 | 单次（+ HyDE/混合/MMR） | 多轮可选工具组合 |
| 输出 | `answer` + `sources` | `answer` + `mermaid` + `sources` + `trajectory` + `confidence`（及流式扩展字段） |
| 典型场景 | 文档式问答 | 调用链、架构、定位文件 |

---

## 八、配置与数据依赖

| 项 | 来源 |
|----|------|
| `vector_store_path` | Supabase `repositories`，API 内会规范化旧路径前缀 |
| `graph_path` / `repo_root` | `repositories`（可选，用于图谱与读文件） |
| `max_iterations` | 默认 5（`api.py` 创建 `AgentRunner` 时传入） |
| LLM | `CONFIG` 与各节点所用 client（合成/planner/工具路由等） |

---

## 九、Todo（与先进 Agent 对比：当前不具备的能力）

> 下列对比基于 **2025–2026 年** 常见「代码 Agent」产品/平台公开能力。本仓库 Agent 定位为 **仓库理解 + 文档问答 + 只读代码探索**，与 IDE 全权限 Agent 不同；差距可视为后续产品化或研究扩展项。

### 已实现的优化（2025-06 Phase 1-4）

| 优化项 | 说明 | 涉及文件 |
|--------|------|---------|
| **意图分类扩展** | 新增 6 种 doc/repo 意图 (topic_coverage, relationship, evidence, section_locator, repo_overview, followup_clarification) | `state.py` |
| **三路径分流** | direct（无工具）/ light（1 轮检索）/ deep（多轮迭代），缩短简单问题延迟 | `graph.py` |
| **词法预分类** | 零 LLM 开销判断寒暄、追问等明确意图 | `graph.py._classify_intent_lexical` |
| **Doc-centric 流程** | Evaluator/Hard Gate 对文档类意图不再强制要求 code anchor | `prompts.py`, `graph.py._hard_gate_check`, `state.py.check_stop_conditions` |
| **grep_search 工具** | 文本/正则跨仓库搜索，弥补 code_graph 盲区 | `tools/grep_tool.py`, `graph.py._execute_tool` |
| **RAG 并行检索** | dense + sparse 以及跨 category 并行 | `tools/rag_tool.py` |
| **repo_map 缓存** | 相同参数复用结果，避免重复扫描 | `tools/file_tool.py` |
| **证据去重 + 重排** | 内容 hash 去重 + 类型权重重排，截断到 15 条 | `state.py`, `graph.py._rerank_evidence` |
| **实体跨轮携带** | entities 写入 SessionMemory.key_entities | `graph.py._writeback_memory` |
| **Prompt 全面更新** | Planner/ToolRouter/Evaluator 均适配新意图与 grep_search | `prompts.py` |

### 仍待实现

| 类别 | 先进 Agent 常见能力 | 本仓库 Agent 现状 |
|------|---------------------|-------------------|
| **MCP** | 通过 Model Context Protocol 挂载数据库、文档、第三方 API | **无**：工具为进程内硬编码 Python 类 |
| **终端 / Bash** | 跑测试、构建、lint、`git`、脚本 | **无**：不能执行 shell |
| **写仓库** | `apply_patch` / Write / Edit、多文件重构 | **无**：仅 `file_read` 读取 |
| **Web** | 联网搜索、拉取 URL | **无** |
| **浏览器** | 截图、点击、复现前端 Bug | **无** |
| **用户交互工具** | 任务中途 `ask_question` / 确认危险操作 | **无** |
| **Skills / Rules** | 按项目加载结构化指令 | **无**；行为由 `prompts.py` 固定 |
| **子 Agent** | 并行子代理，上下文隔离 | **无**；单图线性迭代 |
| **多模态** | 读图片、生成示意图素材 | **无** |

**可优先补齐的方向**：

1. **MCP 客户端**：把工具调用改为「LLM → MCP tool list」，便于接 Supabase、浏览器等外部系统。
2. **Web 检索**：对「库版本 / CVE / 官方文档」类问题增加可开关的 `web_search` + `fetch_url`（需限域与缓存策略）。
3. **可选写路径**：若要从「问答」扩展到「改代码」，需独立权限模型 + `patch` 工具 + diff 预览联动。
4. **子 Agent 并行**：对 deep-path 问题，将多个检索子任务分发给子 Agent 并行执行，缩短端到端延迟。
5. **基准测试**：构建 repo-specific Q&A 基准 (ground-truth answers + expected intents)，自动化评估准确率和延迟回归。

---

*文档随仓库迭代维护；若移动大段逻辑请以 `graph.py` / `api.py` 为准。*
