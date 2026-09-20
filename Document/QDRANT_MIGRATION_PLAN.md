> **状态：已完成（存档）。** 本迁移已全部落地——`docker/src/ingestion/vector_store.py` 现在直接写 Qdrant，`docker/requirements.txt` 已移除 `faiss-cpu`、加入 `qdrant-client`/`fastembed`，磁盘上不再产生新的 `index.faiss`/`index.pkl`。本文档现在只作为"为什么现在是这个样子"的历史记录保留，不再是待办事项列表——下面所有"需要改动的文件"章节描述的都已经是过去时。
>
> 注意：本文档第 86 行提到的 `Document/AGENT_CHAT_ARCHITECTURE.md`、`Document/API_DOCUMENTATION.md` 中"检索后端已换成 Qdrant"的同步更新——`API_DOCUMENTATION.md` 已重新创建并反映 Qdrant 现状；`AGENT_CHAT_ARCHITECTURE.md` 描述的旧 Agent 状态机已在后续一次（与本次 Qdrant 迁移无关、目前尚未提交的）聊天后端重写中被整体删除替换，不再需要恢复该文档。
>
> 当前最新的后端 API / 数据库参考见 `Document/API_DOCUMENTATION.md`。

# 迁移 RAG 向量库到 Qdrant Cloud

## Context

当前 RAG 检索栈是自建的：每个仓库在本地磁盘 `VECTOR_STORE_ROOT/<repo_dir>/{code,text}/` 下各存一份 FAISS 索引（`index.faiss`+`index.pkl`），配合一个手写的内存 BM25（`docker/src/core/retrieval.py::SparseBM25Index`），混合检索、MMR、HyDE 逻辑在 `docker/src/core/chat.py`（RAG 对话）和 `docker/src/agent/tools/rag_tool.py`（Agent 的 `rag_search` 工具）里各实现了一份、参数还不一致（`CATEGORY_TOP_K` 5/2 vs 20/20，`MMR_TARGET` 12 vs 8）。用户希望把"数据库 + 检索"整体交给 Qdrant 托管，不再自己维护 FAISS 文件、进程内 BM25 索引和 `VectorStoreCache` 这一整套基础设施。

已与用户确认三个关键决策：
1. **部署方式**：已有 Qdrant Cloud 实例，稍后提供 `QDRANT_URL` / `QDRANT_API_KEY`。不需要改 `docker-compose.yml`（无需自建 Qdrant 容器/卷）。
2. **存量数据**：直接丢弃，不写迁移脚本。用户下次对某仓库调用 `/generate` 时自然触发全量重新 embedding 并写入 Qdrant。
3. **稀疏检索**：彻底改用 Qdrant 原生稀疏向量（FastEmbed 的 `Qdrant/bm25` 模型，客户端本地计算后连同 dense 向量一起 upsert/query），退役手写的 `SparseBM25Index` 用于整仓文档检索的部分。

设计目标是"无缝衔接"：只替换"候选召回"这一层的实现，`RankedCandidate`/MMR/HyDE/Prompt 拼装/SSE 事件契约、`path_resolver.py` 的路径解析、Supabase schema、前端 `AgentStreamEvent` 类型全部保持不变，把改动面收紧到 ingestion 的写入路径和 retrieval 的召回路径。

## 数据模型（Qdrant）

- **两个 collection**（不是每仓库一个 collection，避免 collection 数量随仓库数线性增长）：`code_chunks`、`text_chunks`，直接对应现有的 `code`/`text` 分类。
- 每个 collection 用**命名向量**：
  - `dense`：由 `get_openrouter_embeddings()`（`docker/src/ingestion/embedding_utils.py`，`qwen/qwen3-embedding-8b`）产出，distance=Cosine。维度不要硬编码——首次创建 collection 时用一次真实 `embed_query` 调用的返回长度来确定。
  - `bm25`：稀疏向量，用 `fastembed.SparseTextEmbedding(model_name="Qdrant/bm25")` 在客户端本地算好 `SparseVector(indices, values)` 再 upsert/query（不依赖 Qdrant Cloud 的 server-side inference，保证在任何 plan 下都能跑，和 dense 向量"自己算好再传"的模式一致）。
- **payload 字段**（作为 Qdrant 的文档存储，因为不再有 FAISS 的 `docstore._dict` 可以还原 `Document`）：`repo_id`（= `get_repo_disk_directory_name(repo_url)`，和现在磁盘目录名同一套推导逻辑）、`content`（原 `page_content`）、`source`、`chunk_type`，以及分类特有字段：code 是 `start_line`/`end_line`/`node_type`/`name`/`chunk_part`，text 是 `section_heading`/`heading_level`/`breadcrumb`/`chunk_index`。对 `repo_id` 建 keyword payload index，所有查询都按它过滤。
- **point ID**：复用已有的 `compute_doc_key(doc)`（`retrieval.py:30`），做 `uuid5(NAMESPACE, doc_key)` 转成 Qdrant 合法 ID。天然幂等：同一 chunk 内容不变时重复 upsert 不产生新点。
- **重建语义**：现在每次 `/generate` 都是 `FAISS.from_documents(...)` 整份重建；Qdrant 里没有"目录"概念，所以每次写入前要按 `repo_id`（+ collection）做 filter delete，再 upsert 新批次，这样才能等价地清掉已从仓库中删除的文件对应的旧 chunk。

## 需要改动的文件

### 1. `docker/src/ingestion/vector_store.py` — 改写写入逻辑
保留文件名（`wiki_pipeline.py` 的 import 路径不用改），内部逻辑换成：
- `get_qdrant_client() -> QdrantClient`：读 `QDRANT_URL`/`QDRANT_API_KEY` 环境变量，模块级单例。
- `ensure_collection(category: str)`：幂等 `create_collection`（不存在才创建），dense+bm25 双向量配置 + `repo_id` payload index。
- `upsert_vector_store(docs: list[Document], repo_id: str, category: str) -> None`：替代现有 `create_and_save_vector_store(docs, db_path)`。复用现有的批次节流常量 `EMBEDDING_BATCH_SIZE=50`/`INTER_BATCH_SLEEP_SEC=2.0`（避免打爆 OpenRouter）；对每批：`get_openrouter_embeddings().embed_documents(...)` 拿 dense 向量，`SparseTextEmbedding` 拿 sparse 向量，组装 `PointStruct`；写入前先按 `repo_id` filter delete 该 collection 里的旧点（只需在第一批前做一次），再 `client.upsert(collection, points=batch)`。
- 删除 `docker/src/ingestion/kb_loader.py`（纯 FAISS load，不再需要）。

`docker/src/core/wiki_pipeline.py::run_rag_indexing`（`wiki_pipeline.py:209-305`）两处调用：
```python
create_and_save_vector_store(code_docs, str(vector_store_path / "code"))
create_and_save_vector_store(text_docs, str(vector_store_path / "text"))
```
改为：
```python
upsert_vector_store(code_docs, repo_id=repo_dir, category="code")
upsert_vector_store(text_docs, repo_id=repo_dir, category="text")
```
`graphrag_communities.json`/`code_graph.json` 的拷贝逻辑（`wiki_pipeline.py:270-294`）**不动**——这两个结构化 JSON 继续放本地磁盘 `vector_store_path` 下，只有 embedding chunk 数据搬去 Qdrant。`update_repo_vector_path(repo_url, str(vector_store_path))`（`wiki_pipeline.py:298`）**不动**——Supabase 的 `repositories.vector_store_path` 字段含义不变，它现在只用来定位本地的 `graphrag_communities.json`/`code_graph.json`/克隆仓库根，不再是向量数据的位置，这样完全不用碰 Supabase schema。

移除 `from src.core.chat import invalidate_vector_store_cache` 及其调用点（`wiki_pipeline.py:19`，以及 `execute_generation_task` 里全量重跑前的调用，约 `wiki_pipeline.py:507-512`）——Qdrant 是远程服务，没有进程内索引需要失效。

**前置阻断项**（与 Qdrant 无关，但不修就无法端到端验证）：`wiki_pipeline.py:230` 调用 `get_files_to_process(repo_path, str(config_path))`，但 `docker/src/ingestion/file_processor.py:96` 当前签名是 `get_files_to_process(repo_path)`，只接受一个参数——现状下 `run_rag_indexing` 会直接抛 `TypeError`，ingestion 根本跑不起来。需要先把两边签名对齐（要么 `file_processor.py` 恢复接收 `config_path` 参数，要么调用处去掉多余参数）才能验证本计划。

### 2. `docker/src/core/retrieval.py` — 新增共享 Qdrant 召回函数，其余不动
新增（不改动已有的 `RankedCandidate`/`normalize_scores`/`mmr_select`/`compute_doc_key`/`SparseBM25Index`/`CommunityFirstRetriever` 定义本身）：
```python
def qdrant_search_category(
    client, category: str, repo_id: str, query: str,
    *, dense_k: int, sparse_k: int,
) -> List[RankedCandidate]
```
内部：对 `dense`/`bm25` 两个命名向量分别发起一次按 `repo_id` filter 的 `query_points`，各自 `normalize_scores()` 后按调用方传入的权重（chat.py 用 0.6/0.4，rag_tool.py 也用 0.6/0.4，各自常量不变）加权求和，命中的 payload 还原成 `Document(page_content=payload["content"], metadata={...})`。返回形状和现有 `_collect_candidates_for_category` 完全一致，这样 `chat.py`/`rag_tool.py` 下游的 HyDE、`mmr_select`、格式化逻辑不用动。

这里选择"两次独立请求 + 客户端加权融合"而不是 Qdrant 原生的 `prefetch`+`Fusion.RRF` 单次混合查询，是为了保留现有 0.6/0.4 加权语义、行为不漂移；原生 RRF 融合可以作为后续优化单独评估，不放进本次迁移范围。

`CommunityFirstRetriever`（`retrieval.py:231-524`）类本身不动——它的社区摘要 BM25（`_build_community_index`）和"社区内二次 BM25"（`retrieve_from_communities`）操作的对象很小（社区数量级是几十个），继续留在内存里，不算用户说的"手动维护的向量数据库"。唯一要改的是它的 `hybrid_retrieve(query, dense_results, ...)` 的 `dense_results` 入参来源，从 FAISS 换成 Qdrant 的 dense-only 查询结果（见下面 chat.py 部分）。

### 3. `docker/src/core/chat.py` — 摘掉 FAISS/BM25 基础设施
删除：`VectorStoreCache`/`CachedStoreEntry`/`_vector_store_cache`/`invalidate_vector_store_cache`/`get_vector_store_cache_stats`（`chat.py:47-137, 310-325`）、`CategoryRetrievalUnit` dataclass、`_extract_store_documents`、`_load_vector_stores`、`_resolve_category_path`、`_resolve_vector_store_root`。不再需要"先把整份索引加载进内存"这一步——Qdrant client 是轻量远程句柄，天然替代了这套 LRU 缓存。

`answer_question`/`answer_question_stream`（`chat.py:585, 775`）**保留 `db_path` 参数名和调用方式不变**（`api/routers/chat.py`/`agent.py` 两处调用点完全不用改），函数内部第一行加 `repo_id = Path(db_path).name`（`vector_store_path` 的最后一段本来就是 `get_repo_disk_directory_name(repo_url)`，参见 `wiki_pipeline.py:222-223`），后续检索都用这个 `repo_id` 做 Qdrant filter；`db_path` 本身仍然传给 `_load_graphrag_communities(root_path)`（`chat.py:351`）去读本地的 `graphrag_communities.json`，这部分完全不变。

`_collect_candidates_for_category`（`chat.py:489-545`）内部的 `unit.dense_store.similarity_search_with_relevance_scores(...)` + `unit.sparse_index.search(...)` 两段替换成一次 `qdrant_search_category(client, category, repo_id, question, dense_k=..., sparse_k=...)` 调用；`_gather_hybrid_candidates`（`chat.py:548-582`）外层循环、归一化、`MAX_TOTAL_CANDIDATES` 截断逻辑不变。

`_gather_dense_faiss_hits`（`chat.py:398-422`，供 `CommunityFirstRetriever.hybrid_retrieve` 用）改名 `_gather_dense_qdrant_hits`，内部把 `unit.dense_store.similarity_search_with_relevance_scores` 换成 Qdrant 的 dense-only 按 `repo_id` filter 查询，逐分类合并去重逻辑不变。

`_answer_with_stores`、HyDE 生成（`_generate_hyde_document`）、`mmr_select` 调用、`_format_documents`、`answer_question_stream` 的 SSE 事件序列（`retrieval_start → hyde_generated → retrieval_done → answer_delta* → answer_done`）**完全不动**——这些只消费 `RankedCandidate`/`Document`，和后端是 FAISS 还是 Qdrant 无关。前端 `AgentStreamEvent` 类型和 `ChatInterface.tsx` 不需要任何改动。

### 4. `docker/src/agent/tools/rag_tool.py` — 同样只换召回后端
`RAGSearchTool.__init__(vector_store_path)`（`rag_tool.py:70`）签名不变（`agent/graph.py:130` 的调用点不用改）。删除 `_ensure_loaded`/`stores`/`_resolve_vector_store_root`/`_resolve_category_path`/`_extract_store_documents`、以及桥接 `chat.py._vector_store_cache` 的那段转换代码（`rag_tool.py:81-129`）——`execute()` 里直接算 `repo_id = Path(self.vector_store_path).name`，`_collect_candidates_for_category`（`rag_tool.py:334-413`）改调用第 2 节新增的共享 `qdrant_search_category`（复用它自己的 `CATEGORY_TOP_K={"code":20,"text":20}`、`MMR_TARGET=8`、`HYBRID_DENSE_WEIGHT/SPARSE_WEIGHT`、以及只对 `text` 分类做的 `_should_use_hyde` 条件 HyDE 逻辑，都不变）。这一步顺带把 `chat.py`/`rag_tool.py` 两份重复的"召回"实现合并成一个共享函数，但两边各自的 top_k/MMR 调优参数保持独立传参，不强行统一，避免引入行为变化。

`ContextPiece` 返回结构（`source`/`content`/`relevance_score`/`metadata`）不变。

### 5. 依赖与配置
- `docker/requirements.txt`：删除 `faiss-cpu>=1.7.4`（`requirements.txt:35`），新增 `qdrant-client` 和 `fastembed`。
- 仓库根 `.env`/`.env.local`：新增 `QDRANT_URL`、`QDRANT_API_KEY`（用户稍后提供；已在 `.env.local` 中占位）。
- `docker/docker-compose.yml`：不需要改（Qdrant Cloud 是外部服务，沿用现有 `env_file: .env` 即可读到新变量）。

### 6. 清理
- 已有仓库磁盘上残留的 `data/vector_stores/<repo>/{code,text}/index.faiss|index.pkl` 不用主动删——代码不再读它们，留着无害；下次该仓库 `/generate` 时，`graphrag_communities.json`/`code_graph.json` 会被原地覆盖，chunk 数据改写入 Qdrant。
- `CLAUDE.md` 里"两种 chat 模式共用同一份 vector store/graph"的描述、`Document/AGENT_CHAT_ARCHITECTURE.md`、`Document/API_DOCUMENTATION.md` 中提到 FAISS/BM25 的部分，迁移完成后建议同步更新一句话说明检索后端已换成 Qdrant（不在本次代码改动范围内，但别忘了）。

## 不受影响的部分（"无缝衔接"的关键）

- `docker/src/core/path_resolver.py`（`normalize_vector_store_path`/`resolve_agent_paths`）——一行不改，继续负责本地 `graphrag_communities.json`/`code_graph.json`/克隆仓库根的定位。
- `docker/scripts/setup_repository.py::get_repo_disk_directory_name`——继续是"仓库 URL → 唯一标识"的单一来源，现在同时喂给本地目录名和 Qdrant 的 `repo_id` payload。
- Supabase `repositories` 表 schema、`docker/src/storage/supabase_client.py`——不改。
- R2 存储、wiki JSON 生成流程——完全不涉及。
- `docker/src/agent/tools/graph_tool.py`（`CodeGraphTool`）——纯 NetworkX 图遍历，和向量检索无关，不改。
- 前端：`frontend/src/lib/api.ts` 的 `AgentStreamEvent` 联合类型、SSE 事件消费逻辑——不改，因为 `/chat`、`/chat/stream`、`/agent/chat`、`/agent/chat/stream` 的请求/响应形状全部不变。

## 验证方式

1. 先修好 `file_processor.py` 的 `get_files_to_process` 签名不一致问题（否则 ingestion 端到端跑不起来，这个 bug 和 Qdrant 无关，是现状阻断项）。
2. 配置好 `QDRANT_URL`/`QDRANT_API_KEY` 后，`cd docker && python scripts/api.py` 起后端。
3. 对一个较小的测试仓库调用 `POST /generate`，观察日志里 Qdrant upsert 的批次日志，到 Qdrant Cloud 控制台确认 `code_chunks`/`text_chunks` 两个 collection 出现、点数合理、payload 里能看到 `repo_id`。
4. 调用 `POST /chat`（非流式）验证能正常拿到 `answer`+`sources`；再调用 `POST /chat/stream` 确认 SSE 事件序列（`retrieval_start`/`hyde_generated`/`retrieval_done`/`answer_delta`/`answer_done`）和之前行为一致。
5. 调用 `POST /agent/chat` 验证 `rag_search` 工具在 Agent 轨迹里正常返回结果、`sources`/`trajectory` 字段不为空。
6. 对同一仓库重复调用一次 `/generate`，确认 Qdrant 里该 `repo_id` 的旧点被替换而不是重复累加（用 Qdrant 控制台或 `count` API 核对点数前后是否合理，而不是翻倍）。
7. 若前端可跑，在浏览器里对该仓库走一遍 Wiki 生成 + Chat + Agent 问答，确认 UI 无异常（用 `run` skill 或手动 `npm run dev` 验证）。
