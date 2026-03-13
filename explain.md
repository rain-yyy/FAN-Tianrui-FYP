# Project Wiki Generation API 技术文档与架构解析

## 1. 项目概述

本项目是一个高度自动化的 Wiki 生成与项目理解系统。它能够深度分析 Git 仓库（如 GitHub 项目），并将其转化为结构化的 Wiki 文档以及可交互的 RAG（检索增强生成）知识库。

系统融合了多种前沿技术：
* **静态分析**: 使用 Tree-sitter 解析代码，构建精准的抽象语法树。
* **代码知识图谱**: 提取实体（类、函数、文件）及其依赖关系（调用、包含）。
* **GraphRAG (社区发现)**: 应用 Leiden 算法识别功能模块，并生成业务层面的摘要。
* **混合检索 RAG**: 结合 BM25 稀疏检索与向量稠密检索，支持 HyDE 增强与 MMR 多样性重排，提供精准的项目问答能力。
* **异步任务流**: 完整的自动化流水线，从克隆仓库到内容生成、向量索引构建及云端持久化。

---

## 2. 系统核心工作流 (Pipeline)

整个 Wiki 生成流程由 `src/core/wiki_pipeline.py` 调度，采用异步任务模式。

1.  **任务初始化**: 用户提交 URL 后，系统生成 UUID 并初始化任务状态。
2.  **环境准备**: 后端异步克隆仓库并加载项目配置。
3.  **结构生成 (Structure Generation)**:
    *   生成文件树。
    *   **构建代码知识图谱**: 解析 Python, JS, TS, TSX 等，提取调用链。
    *   **GraphRAG 社区分析**: 运行 Leiden 算法划分社区，并利用 LLM 为每个社区生成功能摘要。
    *   **生成 Wiki 目录**: 融合文件树、代码图谱上下文与社区信息，生成层级化的 Wiki 结构。
4.  **内容生成 (Content Generation)**:
    *   遍历 Wiki 结构，为每个章节收集上下文（源码、依赖关系、面包屑）。
    *   调用 LLM 生成详细的技术说明与 **Mermaid 架构图**。
5.  **RAG 向量索引构建 (Indexing)**:
    *   自动分离代码文件与文本文件。
    *   使用 `baai/bge-m3` 模型生成嵌入向量。
    *   构建 **FAISS** 向量库与 **BM25** 稀疏索引。
6.  **结果持久化与清理**:
    *   将 `wiki_structure.json` 与各章节 JSON 上传至 **Cloudflare R2**。
    *   将向量库路径同步至 **Supabase**。
    *   清理本地临时文件，释放存储空间。

---

## 3. API 端点详解

### 3.1 任务管理 (Wiki 生成)

*   **POST `/generate`**:
    *   **功能**: 创建 Wiki 生成任务（异步）。
    *   **输入**: `{ "url_link": "https://github.com/user/repo" }`
    *   **返回**: `task_id`。
*   **GET `/task/{task_id}`**:
    *   **功能**: 查询任务进度（0-100%）、当前步骤及结果 URL。
*   **GET `/tasks`**:
    *   **功能**: 列出所有任务记录。
*   **DELETE `/task/{task_id}`**:
    *   **功能**: 删除已完成或失败的任务。

### 3.2 项目问答 (RAG Chat)

*   **POST `/chat`**:
    *   **功能**: 基于已生成的向量库进行项目问答。
    *   **输入**:
        ```json
        {
          "question": "项目是如何处理身份验证的？",
          "repo_url": "仓库URL",
          "conversation_history": [],
          "current_page_context": "当前 Wiki 页面内容(可选)"
        }
        ```
    *   **返回**: 答案文本、引用来源列表。
*   **GET `/chat/repos`**:
    *   **功能**: 列出所有已建立索引、可进行聊天的仓库。

---

## 4. 关键技术实现

### 4.1 异步任务架构 (`task_manager.py`)
系统使用 FastAPI 的异步特性，通过 `asyncio.create_task` 在后台线程池执行耗时的 AI 生成与 Git 操作。`TaskStatus` 枚举（Pending, Processing, Completed, Failed）实时跟踪任务状态。

### 4.2 AI 客户端集成 (OpenRouter)
系统统一通过 **OpenRouter** 集成顶级模型（如 Claude 3.5 Sonnet, Qwen Plus），极大简化了多模型供应商的管理。
*   **AIClientFactory**: 动态获取模型客户端。
*   **OpenRouterEmbeddings**: 针对 `baai/bge-m3` 优化的向量嵌入实现，支持 8192 token 上下文。

### 4.3 增强型 RAG 检索 (`core/chat.py` & `core/retrieval.py`)
系统不只是简单的向量搜索，而是实现了一套复杂的检索 pipeline：
*   **HyDE (Hypothetical Document Embeddings)**: 先生成假设性回答，再用回答去检索，解决问题与代码之间的语义鸿沟。
*   **Hybrid Retrieval (混合检索)**: 同时运行向量相似度检索（语义）与 BM25 关键词检索（精确符号匹配）。
*   **MMR (Maximum Marginal Relevance)**: 对候选文档进行重排，确保检索结果既相关又具有多样性，避免信息冗余。
*   **社区优先检索**: 结合 GraphRAG 结果，优先从逻辑相关的代码社区中召回信息。

### 4.4 存储方案
*   **Cloudflare R2**: 存储生成的结构化 JSON 文档，提供公网访问能力。
*   **FAISS**: 本地持久化存储向量索引。
*   **Supabase**: 存储仓库元数据与向量库的映射关系，实现跨会话的仓库定位。

---

## 5. 开发与部署

*   **环境依赖**: Python 3.10+, Docker.
*   **核心配置**: `docker/config/repo_config.json` 控制文件过滤规则。
*   **环境变量**:
    *   `OPENROUTER_API_KEY`: AI 推理与 Embedding。
    *   `R2_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY`: 云存储。
    *   `VECTOR_STORE_PATH`: 向量库本地持久化路径。

---

## 6. 未来优化 (Roadmap)

1.  **持久化队列**: 将内存中的任务状态迁移至 Redis，支持断点续传。
2.  **多层级社区发现**: 支持更大规模项目的递归社区划分。
3.  **前端实时同步**: 引入 WebSocket 或 SSE 实时推送生成进度。
4.  **自动化测试**: 增强 `api-test.py` 的覆盖范围，支持 RAG 准确率评估。
