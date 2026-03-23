# Docker API 接口文档

## 概述

- **服务名称**: Project Wiki Generation API  
- **描述**: 将项目仓库 URL 转换为 Wiki 结构和内容的 API（异步任务 + RAG / Agent 问答）  
- **主文件**: `docker/scripts/api.py`  
- **默认端口**: `8000`（`uvicorn` 启动时）  
- **CORS**: 开发环境允许任意来源（生产应收紧 `allow_origins`）

---

## 通用约定

- **Content-Type**: `application/json`（除 SSE 接口外）  
- **错误体**: FastAPI 标准 `{ "detail": "..." }`  
- **语言**: 产品仅支持英文输出；聊天类接口不再接受 `language` 参数，回答始终为英文。

---

## 1. GET `/health`

**功能**: 健康检查  

**响应示例**:

```json
{ "status": "ok" }
```

---

## 2. GET `/profile`

**功能**: 读取用户偏好（主题等；`profiles` 中可能仍有历史 `language` 列，但 API 不再用于切换语言）  

**Query**

| 参数 | 必填 | 说明 |
|------|------|------|
| `user_id` | 是 | Supabase `auth.users` / `profiles.id` |

**响应**: 始终包在 `profile` 键下；无记录时返回默认 `theme`。

```json
{
  "profile": {
    "id": "uuid",
    "email": "...",
    "theme": "dark",
    "updated_at": "2026-03-11T10:00:00Z"
  }
}
```

---

## 3. PATCH `/profile`

**功能**: 更新 `theme`（写入 `profiles`）  

**Body**

```json
{
  "user_id": "uuid",
  "theme": "light"
}
```

- 必须提供 `theme`；合法值：`light` / `dark`。  

**响应**

```json
{ "success": true }
```

---

## 4. POST `/generate`

**功能**: 创建 Wiki 生成任务（异步）；若 `repositories` 中已有完整 Wiki 元数据则**命中缓存**，任务直接标记为 `cached`。

**Body**

```json
{
  "url_link": "https://github.com/user/repo",
  "user_id": "user_uuid"
}
```

**响应**

```json
{
  "task_id": "task_uuid",
  "message": "任务已创建，正在后台处理。请使用 /task/{task_id} 查询进度。"
}
```

缓存命中时 `message` 为类似「命中缓存，已直接加载已有文档。」

---

## 5. POST `/task/{task_id}`

**功能**: 查询任务状态与进度（**POST**，非 GET）

**响应**

```json
{
  "task": {
    "id": "db_uuid",
    "user_id": "user_uuid",
    "task_id": "task_uuid",
    "repo_url": "https://github.com/...",
    "status": "pending | processing | completed | cached | failed",
    "progress": 45.5,
    "current_step": "正在生成 Wiki 内容...",
    "result": null,
    "error": null,
    "created_at": "...",
    "last_updated": "..."
  }
}
```

- `result` 在完成/缓存时包含 `r2_structure_url`、`r2_content_urls`、`vector_store_path`、`repo_url` 等（与 `GenResponse` 对齐）。

---

## 6. POST `/tasks`

**功能**: 列出某用户的全部任务  

**Body**

```json
{ "user_id": "user_uuid" }
```

**响应**

```json
{ "tasks": [ /* 同 task 对象数组 */ ] }
```

---

## 7. POST `/task/{task_id}/cancel`

**功能**: 终止**正在运行**的生成任务（内存中有 asyncio Task 时取消；否则若 DB 仍为 `processing` 则标记为失败「任务被用户强制终止」）。

**响应**

```json
{ "success": true, "message": "任务已强制终止" }
```

或

```json
{ "success": false, "message": "未找到正在运行的该任务或任务不在处理中" }
```

---

## 8. DELETE `/task/{task_id}`

**功能**: 删除任务记录（进行中 / 已完成 / 失败均可删，需校验归属用户）

**Query**

| 参数 | 必填 |
|------|------|
| `user_id` | 是 |

**响应**

```json
{ "success": true }
```

---

## 9. POST `/file/content`

**功能**: 在服务器本地克隆目录下读取仓库内文件（用于 Wiki 源码预览等）

**Body**

```json
{
  "repo_url": "https://github.com/user/repo",
  "file_path": "src/main.py"
}
```

**响应**

```json
{ "content": "文件 UTF-8 文本（非法字节已替换）" }
```

路径必须落在解析到的仓库根目录之下，否则 `404`。

---

## 10. POST `/chat`

**功能**: RAG 问答，会话与消息写入 Supabase。

**Body**

```json
{
  "user_id": "uuid",
  "repo_url": "https://github.com/user/repo",
  "question": "What is the main purpose of this project?",
  "chat_id": "optional; omit to start a new session",
  "conversation_history": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ],
  "current_page_context": "optional wiki page summary for the model"
}
```

**响应**

```json
{
  "answer": "...",
  "sources": ["README.md", "src/main.py"],
  "chat_id": "uuid",
  "repo_url": "https://github.com/..."
}
```

---

## 11. POST `/chat/stream`

**功能**: RAG 流式问答（**Server-Sent Events**）。流结束后将完整答案写入 `chat_messages`。

**Body**: 与 `POST /chat` 相同。

**SSE 事件**（`event:` + `data:` JSON）

| event | 说明 |
|--------|------|
| `retrieval_start` | 检索开始 |
| `hyde_generated` | HyDE 假设文档已生成 |
| `retrieval_done` | 检索结束 |
| `answer_delta` | 答案增量，`data` 含 `delta` |
| `answer_done` | 答案段结束，`data` 可含 `sources` |
| `complete` | 收尾：`chat_id`、`repo_url`、`answer`、`sources` |
| `error` | 错误 |

---

## 12. GET `/chat/repos`

**功能**: 列出 Supabase `repositories` 表中可用于聊天的仓库（`select *`）。

**响应**

```json
{
  "repos": [
    {
      "repo_url": "https://github.com/user/repo",
      "vector_store_path": "/data/vector_stores/...",
      "r2_structure_url": "...",
      "r2_content_urls": [],
      "description": "...",
      "last_updated": "..."
    }
  ]
}
```

实际字段以表结构为准（可能含 `graph_path`、`repo_root` 等扩展列）。

---

## 13. GET `/chat/history`

**功能**: 某用户的会话列表  

**Query**: `user_id`（必填）

**响应**

```json
{
  "history": [
    {
      "id": "chat_uuid",
      "user_id": "...",
      "repo_url": "...",
      "title": "...",
      "preview_text": "...",
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

---

## 14. GET `/chat/messages/{chat_id}`

**功能**: 指定会话的全部消息  

**响应**

```json
{
  "messages": [
    {
      "id": "...",
      "chat_id": "...",
      "role": "user | assistant",
      "content": "...",
      "metadata": {},
      "created_at": "..."
    }
  ]
}
```

---

## 15. DELETE `/chat/history/{chat_id}`

**功能**: 删除会话及其消息（仅当会话属于该用户）

**Query**: `user_id`（必填）

**响应**

```json
{ "success": true }
```

---

## 16. POST `/agent/chat`

**功能**: Agent 模式问答（多轮工具 + 合成）。会话持久化逻辑与 `/chat` 类似。

**Body**: 与 `POST /chat` 相同（不再包含 `language`）。

**响应**（当前 HTTP 层显式返回字段）

```json
{
  "answer": "...",
  "mermaid": "sequenceDiagram\n...",
  "sources": ["path/to/file.ts:10-20"],
  "trajectory": [
    {
      "step": 1,
      "tool": "rag_search",
      "description": "...",
      "success": true
    }
  ],
  "confidence": 0.85,
  "iterations": 3,
  "chat_id": "uuid",
  "repo_url": "https://github.com/..."
}
```

> **说明**: 图执行器 `run_agent()` 内部还包含 `confidence_level`、`caveats`、`anchors_count`、`evidence_count`、`error` 等；同步接口的 JSON **未全部透出**，完整字段见流式 `complete` 事件。助手消息的 `metadata` 中保存 `sources`、`mermaid`、`trajectory`、`confidence`、`iterations`、`mode: "agent"`。

---

## 17. POST `/agent/chat/stream`

**功能**: Agent 流式执行（SSE）。  

**Body**: 同 `/agent/chat`。

**SSE 事件**

| event | 说明 |
|--------|------|
| `planning` | 规划阶段（含 intent、计划步骤等，字段以实际 payload 为准） |
| `tool_call` | 工具选择与执行（含 `iteration`、`elapsed_ms`、`results` 等） |
| `evaluation` | 充分性评估 |
| `synthesis` | 合成开始/结束 |
| `complete` | **最终结果**：在 `run_agent()` 返回值基础上附加 `chat_id`、`repo_url`（含 `confidence_level`、`caveats`、`anchors_count`、`evidence_count`、`error` 等） |
| `error` | 异常 |

前端可使用 `fetch` + `ReadableStream` 解析 `event:` / `data:` 行（与 `EventSource` 相比可携带 POST Body）。

---

## 错误响应

| HTTP | 常见原因 |
|------|-----------|
| 400 | 缺少必填字段、非法 profile 枚举、`PATCH /profile` 无更新字段 |
| 404 | 任务/会话不存在、无仓库向量索引、文件不在仓库根内 |
| 500 | 下游 LLM/存储异常 |

```json
{ "detail": "错误描述" }
```
