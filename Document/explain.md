# FAN-Tianrui-FYP 项目最终状态报告（代码审计版）

> **说明**：本报告依据仓库内**当前实际代码与配置**整理，用于毕业论文素材。仓库中**未**发现可引用的「中期报告 II（2026-02-01）」原文 PDF/Markdown；与「2 月 1 日架构」的对比，以下列文档与实现为参照：`plan.md`（Wiki 并发生成与路径隔离方案）、根目录 `explain.md`（系统概览）、以及下文所列源文件。

---

## 1. 技术演进与增量更新（Technical Delta）

### 1.1 后端（FastAPI）

| 结论 | 证据（路径 / 符号） |
|------|---------------------|
| **未发现「仓库内容增量更新」逻辑**：每次生成任务仍走完整流水线（克隆/读盘 → 结构 → 正文 → R2 → RAG 索引）；无基于 commit diff 的增量解析或增量向量更新。 | `docker/src/core/wiki_pipeline.py` 中 `execute_generation_task`：`run_structure_generation` → `run_wiki_content_generation` → `upload_wiki_to_r2` → `run_rag_indexing`。 |
| **存在「任务级隔离 + 失败恢复」类演进**：任务输出目录按 `task_id` 划分；Wiki 先上传 R2，RAG 失败不推翻已完成结果，并调度后台重试索引。 | `wiki_pipeline.py`：`_task_output_dir`、`upload_wiki_to_r2` 先于 `run_rag_indexing`；`_background_retry_rag_indexing`。 |
| **取消任务与数据库一致**：用户取消时写入 Supabase `failed` + `Cancelled by user`，且后台通过 `_task_marked_cancelled_by_user` 避免覆盖取消状态。 | `wiki_pipeline.py`：`_task_marked_cancelled_by_user`；`docker/scripts/api.py`：`cancel_task_api` → `update_task_status(..., error="Cancelled by user")`。 |
| **克隆目录并发安全**：远程仓克隆到 `REPO_STORE_ROOT` 下 `repo_hash_taskid` 子目录，减少多任务同仓互删。 | `docker/scripts/setup_repository.py`：`setup_repository(..., task_id=...)`。 |
| **RAG 主路径**：HyDE + 分类别（`code`/`text`）FAISS 稠密检索 + 自建 BM25 稀疏检索 + 分数归一化混合 + MMR；**未**把 `CommunityFirstRetriever` 接入 `answer_question` / `answer_question_stream`。 | `docker/src/core/chat.py`：`_gather_hybrid_candidates`、`_collect_candidates_for_category`、`mmr_select`；`CommunityFirstRetriever` 仅定义于 `docker/src/core/retrieval.py` 并在 `docker/src/core/__init__.py` 导出，**全仓库无其它引用**。 |
| **「社区优先两阶段检索」已实现但未接线**：`CommunityFirstRetriever.hybrid_retrieve` 的稠密/稀疏融合默认 `alpha=0.6`。 | `docker/src/core/retrieval.py`：`CommunityFirstRetriever`、`hybrid_retrieve`。 |

### 1.2 前端（Next.js）

| 结论 | 证据 |
|------|------|
| **聊天仍支持 RAG 与 Agent 双模式**（`todo.txt` 曾记「去掉 RAG 只保留 Agent」，**代码未落实**）。 | `frontend/src/components/ChatInterface.tsx`：`chatMode`、`handleSetChatMode`、`localStorage` 键 `chat_mode_preference`。 |
| **Agent 侧可视化**：`LiveStepFlow` 展示规划/工具/评估等步骤；`SourcesPanel` 将来源解析为可点击「证据卡片」式结构（含路径规范化、类别前缀）。 | `ChatInterface.tsx`：`LiveStepFlow`、`SourcesPanel`；`frontend/src/components/SourcesPanel.tsx`：`parseSource`、`normalizeFilePath`。 |
| **Mermaid**：流式未完成时不强渲染；语法校验、`fixMermaidSyntax` 标签修复、失败重试与源码查看；容器 `overflow-x-auto` 便于宽图横向浏览。**未发现**独立的「点击放大/缩放控件」实现（与 `todo.txt` 中「图表可以放大」的期望可能仍有差距）。 | `frontend/src/components/Mermaid.tsx`；`WikiViewer.tsx`、 `MessageItem.tsx` 内嵌 `Mermaid`。 |
| **无 Mermaid 之外的交互式图表库**：依赖中无 Recharts/Visx 等。 | `frontend/package.json`。 |

### 1.3 部署与运维

| 结论 | 证据 |
|------|------|
| **Docker Compose**：镜像构建上下文为 `docker/`，端口 8000，`VECTOR_STORE_PATH=/data/vector_stores`、`REPO_STORE_PATH=/data/repos`，宿主机 `../data` 挂载到容器 `/data`（与 Fly 持久化思路一致）。 | `docker/docker-compose.yml`。 |
| **Fly.io CI**：`main` 分支变更 `docker/**` 时触发，`flyctl deploy --remote-only --ha=false`。 | `.github/workflows/deploy-flyio.yml`。 |
| **`fly-rsync-helper.sh`**：对指定 machine 执行 `fly ssh console` 转发命令，用于远端调试/同步类操作，**非**应用内部署逻辑。 | 仓库根目录 `fly-rsync-helper.sh`。 |
| **R2 对象键并发隔离**：存在 `task_id` 时使用 `{repo_name}/{date}_{task_id}/...`。 | `docker/src/storage/r2_client.py`：`R2Client._get_r2_path`、`upload_wiki_to_r2` 中 `base_path`。 |

### 1.4 相对「中期架构」可标注的新增/变更（基于 `plan.md` 与代码对照）

- **Wiki 正文并发生成**：`WikiContentGenerator.generate` 使用 `ThreadPoolExecutor`，并发数 `get_wiki_content_concurrency()`（默认 3，见 `docker/config/repo_config.json`）。  
  - `docker/src/wiki/content_gen.py`：`generate`、`ThreadPoolExecutor`  
  - `docker/src/config.py`：`get_wiki_content_concurrency`  
- **章节级进度回写**：50–85 区间按完成章节比例线性推进。同上 `content_gen.py` 中 `progress_callback`。  
- **文件名冲突预处理**：`_build_filename_map`（与 `docker/tests/test_content_gen.py` 中单测对应）。  

---

## 2. 最终实验结果与性能评估（Final Evaluation Metrics）

### 2.1 仓库内**可提取的量化指标**（现状）

| 类型 | 结论 | 证据 |
|------|------|------|
| **端到端流水线耗时**（按仓规模：克隆 / 解析 / 索引 / Wiki） | **代码未**集中记录各阶段 wall-clock 到日志或结果 JSON；**无法**从仓库直接给出「不同规模仓库」的统计表。进度仅见 Supabase 步骤文案与百分比。 | `wiki_pipeline.py`：`_update_progress` |
| **问答延迟（如是否仍 ~20s）** | **无**统一埋点或基准测试脚本输出平均延迟；流式接口仅向前端推送 `answer_delta`，未写 RTT 统计。 | `docker/src/core/chat.py`：`answer_question_stream`；`docker/scripts/api.py` SSE |
| **Wiki 覆盖率 / Mermaid 成功率 / RAG Precision-Recall** | **无**自动化评测流水线或持久化实验结果文件；Mermaid 仅有组件级校验与错误 UI，无成功率聚合。 | `frontend/src/components/Mermaid.tsx` |
| **Agentic 搜索相关** | `docker/eval/agentic_search/run_lexical_eval.py` 对 **`GrepSearchTool`** 做词汇级用例与 **wall_ms**，输出 `median_wall_ms` 等；**不是**完整 Agent 或 RAG 端到端指标。 | `run_lexical_eval.py`、`lexical_cases.json` |

### 2.2 用户研究 / 反馈

- 仓库内**未发现**结构化的 User Study 原始数据（问卷 CSV、访谈记录、统计脚本结果）。  
- `todo.txt` 为内部待办，可视为开发侧主观反馈（如：「直接去掉 rag」「.github folder 需要处理」「幻觉导致 file 找不到」），**不宜**直接作为正式用户研究结论引用。

### 2.3 论文写作建议（若需补齐指标）

- 在 `execute_generation_task` 或 `run_rag_indexing` 起止处打 **结构化日志**（JSON line：repo_size、文件数、各阶段秒数）。  
- 对 `/chat` 与 `/agent/chat` 增加中间件或客户端探针记录 **TTFB、首 token、总时长**。  
- 另建小型 **golden question** 集，人工标注相关文件，计算 Recall@k / nDCG（当前代码未实现）。

---

## 3. 核心技术细节深度挖掘（Implementation Methodology）

### 3.1 代码知识图谱：`ts_parser.py` 与 `CodeGraphBuilder`

**`TreeSitterParser`（`docker/src/ingestion/ts_parser.py`）**

- 语言绑定：优先 `tree_sitter_language_pack`，失败则尝试 `tree_sitter_languages`；`Parser(lang)` / `set_language` 兼容多版本 tree-sitter。  
- `EXTENSION_TO_LANGUAGE`：`.py/.js/.ts/.tsx/.go/.java/.cpp/.c/.rb/.rs` 等。  
- `parse_code` → `_extract_chunks`：按语言选择 AST 节点类型（如 Python `function_definition`，TS `interface_declaration` 等），产出 `CodeChunk`（含行号）；无匹配时整文件一块。

**`CodeGraphBuilder`（`docker/src/ingestion/code_graph.py`）**

- **两阶段**：先 `_extract_nodes`（文件节点 + 类/函数节点，`file → contains → symbol`），再 `_extract_edges`（`calls`）。  
- **跨语言引用**：**未**做统一符号表或 LSP；**调用边**在 `call`/`call_expression` 上取标识符（或 `attribute` 最后一节），再在**全图**中查找 `type==function` 且 `name` 相同的节点并连边——属于**跨文件、启发式、易同名误连**策略。  
- **导入关系**：`import_statement` 等分支为 **`pass`，未建边**。  
- **结论**：所谓「跨语言」在实现上体现为「多语言各自 Tree-sitter 解析 + 全局同名匹配」，**不是**精确的跨语言链接解析。

### 3.2 Leiden 与 Wiki ToC：`CommunityEngine` + Prompt 工程

**社区发现**

- `CommunityEngine.run_leiden`（`docker/src/ingestion/community_engine.py`）：`nx.DiGraph.to_undirected()` → `igraph` + `leidenalg.find_partition(..., ModularityVertexPartition)`，得到 `communities: Dict[int, List[str]]`。  
- `generate_summaries`：对每个社区取 `type in {file, class}` 的节点描述，截断约 30 条，调用 LLM 生成英文摘要（`get_model_config(CONFIG, "community_summary")`）。

**转化为结构生成输入**

- `generate_wiki_structure`（`docker/src/wiki/struct_gen.py`）：构建 `communities_info` 字符串，格式为每个 `Community id` + `Summary` + `Key Files`（取不含 `:` 的节点至多 5 个，多为文件路径）。  
- 与 `file_tree`、`readme`、`repo_map`、**强制合法路径列表** `valid_file_list` 一并传入 `STRUCTURE_PROMPT`（`docker/src/prompts.py`）。  
- Prompt 中 **`<COMMUNITIES>`** 与规则 **「Community Logic (GraphRAG)」**：要求模型利用社区识别子系统，但可合并/丢弃低价值簇；**并非**算法直接把 Leiden 输出映射为 ToC 树，而是 **LLM 自由生成 JSON ToC**，受 `valid_file_list` 约束以减少路径幻觉。

### 3.3 RAG 混合检索：BM25、FAISS 融合与 MMR

**BM25（稀疏）**

- `docker/src/core/retrieval.py`：`SparseBM25Index`，参数 `k1=1.5`、`b=0.75`；分词 `default_tokenizer`（中英数字下划线）。

**稠密 + 稀疏融合（主 RAG 路径）**

- `docker/src/core/chat.py`：  
  - `HYBRID_DENSE_WEIGHT = 0.6`，`HYBRID_SPARSE_WEIGHT = 0.4`（注释与代码一致：稠密 0.6、稀疏 0.4）。  
  - `_collect_candidates_for_category`：对每类分别 `similarity_search_with_relevance_scores`（FAISS）与 `sparse_index.search`，归一化后 `final_score = 0.6*dense + 0.4*sparse`。  
  - 多类候选再对 `final_score` 做一次全局 `normalize_scores` 并截断 `MAX_TOTAL_CANDIDATES = 60`。  
  - `CATEGORY_TOP_K`：`code: 5`，`text: 2`；`DENSE_K_MULTIPLIER = 6`，`SPARSE_K_MULTIPLIER = 4`，经 `_plan_method_k` 限制在 `[MIN_METHOD_K=6, MAX_METHOD_K=30]`。

**MMR**

- `retrieval.py`：`mmr_select(..., lambda_mult: float = 0.5)`，公式为 `lambda_mult * sim_to_query - (1 - lambda_mult) * penalty`（余弦相似度基于 `default_tokenizer` 词袋）。  
- `chat.py`：`MMR_TARGET = 12`，`MMR_LAMBDA = 0.5`；**MMR 的 query 文本为原始用户问题**（非 HyDE 拼接串），见 `_answer_with_stores` 中 `mmr_select(..., question, ...)`。  

**Agent 工具内 RAG（与 Chat 略有不同）**

- `docker/src/agent/tools/rag_tool.py`：`MMR_TARGET = 8`，`HYBRID_*` 与 `0.6/0.4` 一致；短查询可跳过 HyDE 等优化。

**HyDE**

- `chat.py`：`HYDE_ENABLED = True`；`_generate_hyde_document` 使用 `HYDE_PROMPT` 与 `get_model_config(CONFIG, "hyde_generation")`；检索阶段 `retrieval_query = question + "\n\n" + hyde_doc`。

---

## 4. 关键评审与反思（Critical Review & Learning）

### 4.1 棘手问题在代码中的体现

| 问题域 | 表现 | 代码位置 |
|--------|------|----------|
| **大规模 AST / 图** | 全仓多文件解析与 NetworkX 构图；Leiden 依赖 igraph；社区摘要多次 LLM 调用，成本高、慢（`todo.txt` 亦提到社区构建慢）。 | `code_graph.py`、`community_engine.py`、`struct_gen.py` |
| **幻觉（路径/文件）** | 结构生成用 `valid_file_list` 约束 `files` 数组；`todo.txt` 仍提到少量路径找不到与 `.github` 过滤。 | `struct_gen.py`、`prompts.py` `STRUCTURE_PROMPT`；`repo_config.json` `excluded_files` 含 `.github` |
| **R2 与可靠性** | 单文件上传重试 + 指数退避；目录批量上传无并行（顺序 `upload_file`）。 | `r2_client.py`：`upload_file`、`upload_directory` |
| **并发与客户端** | Wiki 正文每 worker `client_factory()` 独立客户端；向量库 `VectorStoreCache` 线程锁 + LRU。 | `content_gen.py`、`chat.py`：`VectorStoreCache` |
| **取消与长任务** | `asyncio.Task.cancel()` 无法立刻中断 `run_in_executor` 内的同步代码；依赖完成后检查 DB 是否已取消。 | `api.py`、`wiki_pipeline.py`：`_task_marked_cancelled_by_user` |

### 4.2 技术选型：成效与调整

| 选型 | 观察 | 依据 |
|------|------|------|
| **OpenRouter 统一网关** | 单工厂 `get_ai_client` + `repo_config.json` 按任务选模型，便于换模型而少改代码。 | `ai_client_factory.py`、`docker/config/repo_config.json` |
| **多模型分工** | 例如 HyDE/结构/Agent 规划多用 `google/gemini-2.5-flash`，RAG 答案与 Agent 合成用 `qwen/qwen3-235b-a22b-2507`，Wiki 正文 `qwen/qwen3.5-flash-02-23`——体现**成本与能力折中**，而非单一 OpenAI 模型贯穿。 | `repo_config.json` → `ai_models.models` |
| **自建 BM25 + FAISS** | 避免强依赖外部检索服务，但社区检索模块未接入主路径，存在**工程冗余**。 | `retrieval.py`、`chat.py` |
| **Tree-sitter 双后端** | 提升语言包可用性，也增加环境差异（`todo.txt`：language pack 拉取问题）。 | `ts_parser.py` |

### 4.3 文档与代码不一致处（写论文时应显式说明）

- 根目录 `explain.md` 写「混合检索 … 可选社区上下文」——当前 **RAG 问答主实现未使用** `CommunityFirstRetriever`。  
- `todo.txt` 与 **当前前端**（仍保留 RAG/Agent 切换）不一致。  
- `prompts.py` 中 `PromptDefinition.format_messages` **每次**写入 `structure_prompt.json`（`TODO` 注释），副作用不适合生产长期开启，属技术债。

---

## 5. 附录：关键文件索引

| 主题 | 路径 |
|------|------|
| 任务编排 | `docker/src/core/wiki_pipeline.py` |
| RAG 问答 | `docker/src/core/chat.py` |
| 检索与 MMR / 社区检索（库） | `docker/src/core/retrieval.py` |
| Wiki 结构 | `docker/src/wiki/struct_gen.py`、`docker/src/prompts.py` |
| Wiki 正文 | `docker/src/wiki/content_gen.py` |
| 代码图 / TS 解析 | `docker/src/ingestion/code_graph.py`、`docker/src/ingestion/ts_parser.py` |
| 社区与 Leiden | `docker/src/ingestion/community_engine.py` |
| HTTP API | `docker/scripts/api.py` |
| 克隆与路径 | `docker/scripts/setup_repository.py` |
| 配置与模型 | `docker/config/repo_config.json`、`docker/src/config.py` |
| Agent RAG 工具 | `docker/src/agent/tools/rag_tool.py` |
| 前端聊天与证据 UI | `frontend/src/components/ChatInterface.tsx`、`SourcesPanel.tsx` |
| Mermaid | `frontend/src/components/Mermaid.tsx` |
| 评测脚本（Grep） | `docker/eval/agentic_search/run_lexical_eval.py` |
| 并发/文件名测试 | `docker/tests/test_content_gen.py` |

---

*报告生成依据：仓库静态审计；若需与「中期报告 II」逐条 diff，请补充该报告文件或提交哈希以便对照。*
