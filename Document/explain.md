# Project Wiki Generation API 技术文档与架构解析

## 1. 项目概述

本项目是一个高度自动化的 Wiki 生成与项目理解系统。它能够深度分析 Git 仓库（如 GitHub 项目），并将其转化为结构化的 Wiki 文档以及可交互的 RAG（检索增强生成）知识库，并支持 **Agent 多轮工具调用** 模式做更深度的代码理解。

系统融合了多种技术：

- **静态分析**: 使用 Tree-sitter 解析代码，构建抽象语法树。
- **代码知识图谱**: 提取实体（类、函数、文件）及其依赖关系。
- **GraphRAG (社区发现)**: 应用 Leiden 算法识别功能模块，并生成模块摘要。
- **混合检索 RAG**: BM25 + 向量检索，HyDE 与 MMR 重排；可选社区上下文。
- **异步任务流**: 克隆 → 结构/内容生成 → 向量索引 → R2 与 Supabase 持久化。
- **用户偏好**: Supabase `profiles` 提供 `language`（`zh`/`en`）与 `theme`（`light`/`dark`），聊天接口可显式传 `language` 或回退到 profile。

---

## 2. 系统核心工作流（Pipeline）

Wiki 生成由 `docker/src/core/wiki_pipeline.py` 中的 `execute_generation_task` 异步调度（由 `docker/scripts/api.py` 在 `POST /generate` 后 `asyncio.create_task` 触发）。

1. **任务初始化**: 用户提交 `url_link` + `user_id`，写入 `tasks`；若 `repositories` 已有完整 Wiki 元数据则任务直接 **cached**。
2. **环境准备**: 克隆或读取仓库（`scripts/setup_repository` 等）。
3. **结构生成**: 文件树、代码图谱、GraphRAG 社区、Wiki 目录结构（`run_structure_generation` → `struct_gen` 等）。
4. **内容生成**: 按章节拉取上下文并调用 LLM 生成正文与 Mermaid（`run_wiki_content_generation`）。
5. **RAG 索引**: 代码/文本分路，`baai/bge-m3` 嵌入，FAISS + BM25（`run_rag_indexing`）。
6. **持久化**: Wiki JSON 上传 **Cloudflare R2**；`repositories` / `tasks` 更新 **Supabase**；本地临时文件清理。

> 当前生成流水线**未**在 HTTP 层接收 `language` 参数；Wiki 正文语言由 prompt/默认配置决定。RAG 与 Agent 问答已支持按用户 `language` 输出。

---

## 3. API 端点（与实现对齐）

详细请求/响应、SSE 事件表见同目录 **[API_DOCUMENTATION.md](./API_DOCUMENTATION.md)**。摘要如下：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET/PATCH | `/profile` | 用户语言、主题等 |
| POST | `/generate` | 创建生成任务（支持缓存命中） |
| POST | `/task/{task_id}` | 查询任务 |
| POST | `/tasks` | 用户任务列表 |
| POST | `/task/{task_id}/cancel` | 取消运行中任务 |
| DELETE | `/task/{task_id}?user_id=` | 删除任务 |
| POST | `/file/content` | 读取克隆仓库内文件 |
| POST | `/chat`、`/chat/stream` | RAG 问答（流式为 SSE） |
| GET | `/chat/repos`、`/chat/history`、`/chat/messages/{id}` | 仓库列表与会话 |
| DELETE | `/chat/history/{chat_id}?user_id=` | 删除会话 |
| POST | `/agent/chat`、`/agent/chat/stream` | Agent 问答（流式含完整 `run_agent` 字段） |

---

## 4. 前端应用（Next.js + React Router）

- **入口**: `frontend/src/app/page.tsx` 与 `frontend/src/app/[...slug]/page.tsx` 动态加载 `frontend/src/router/RouterApp.tsx`（客户端路由，关闭 SSR）。
- **API 基址**: 环境变量 `NEXT_PUBLIC_API_URL`，默认 `http://localhost:8000`（见 `frontend/src/lib/api.ts`）。
- **认证**: Supabase Auth；`AuthProvider` 同步 profile 的 `language`/`theme` 到文档根 `lang` 与 `data-theme`。
- **文案**: 轻量 i18n（`frontend/src/lib/i18n.ts`）随 locale 切换。

路由拓扑见 [../frontend/docs/页面拓扑清单.md](../frontend/docs/页面拓扑清单.md)。

---

## 5. 关键技术实现

### 5.1 异步任务与取消

- 运行中的 `asyncio.Task` 登记在 `api.py` 的 `running_tasks`；`POST /task/{id}/cancel` 可 `cancel()` 并更新 DB。
- 任务状态含 **`cached`**（命中已有仓库文档）。

### 5.2 AI 客户端（OpenRouter）

- `docker/src/clients/ai_client_factory.py` 统一推理与嵌入；嵌入常用 `baai/bge-m3`。

### 5.3 RAG（`docker/src/core/chat.py`、`retrieval.py`）

- HyDE、混合检索、MMR、代码/文本分索引等。

### 5.4 Agent（`docker/src/agent/`）

- 规划 → 工具执行 ↔ 评估循环 → 合成；工具含 `rag_search`、`code_graph`、`file_read`、`repo_map`。  
- 架构与事件详见 **[AGENT_CHAT_ARCHITECTURE.md](./AGENT_CHAT_ARCHITECTURE.md)**。

### 5.5 存储

- **R2**: Wiki JSON 公网或签名 URL（视配置）。
- **FAISS / BM25**: 本地 `VECTOR_STORE_PATH`（默认与 `wiki_pipeline` 中 `VECTOR_STORE_ROOT` 一致，生产常为 `/data/vector_stores`）。
- **克隆仓库**: `REPO_STORE_PATH`（如 `/data/repos/<hash>`），供 `/file/content` 与 Agent 读文件。
- **Supabase**: `tasks`、`repositories`、`chat_history`、`chat_messages`、`profiles`。

---

## 6. 开发与部署

- **依赖**: Python 3.10+、Docker（推荐）。
- **仓库配置**: `docker/config/repo_config.json`（过滤与包含规则）。
- **环境变量（示例）**  
  - `OPENROUTER_API_KEY`  
  - R2 相关密钥（若启用上传）  
  - `VECTOR_STORE_PATH`、`REPO_STORE_PATH`  
  - Supabase URL / Service Key（`SupabaseClient`）

本地 Docker Compose 将仓库根目录的 `data/` 挂载到容器 `/data`（`../data:/data`），需存在 `data/vector_stores`、`data/repos`（详见 `API_DOCUMENTATION` 历史说明或 compose 注释）。

---

## 7. 未来优化（Roadmap）

1. 持久化任务队列（Redis 等）与断点续传。  
2. 更大规模项目的层级社区发现。  
3. Wiki 生成进度 SSE/WebSocket 推送（聊天已具备流式）。  
4. 自动化评测与回归（RAG / Agent 质量）。
