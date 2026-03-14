# Docker API 接口文档

## 概述

- **服务名称**: Project Wiki Generation API
- **描述**: 将项目仓库 URL 转换为 Wiki 结构和内容的 API（异步任务模式）
- **主文件**: `scripts/api.py`

---

## 1. POST `/generate`

**功能**: 创建 Wiki 生成任务（异步）

### 输入 (Request Body)

```json
{
  "url_link": "https://github.com/user/repo",
  "user_id": "user_uuid_string"
}
```

### 输出 (Response)

```json
{
  "task_id": "task_uuid_string",
  "message": "任务已创建，正在后台处理。请使用 /task/{task_id} 查询进度。"
}
```

### 流程

接收仓库 URL，创建后台任务，立即返回 task_id，需通过 `/task/{task_id}` 轮询进度和结果。

---

## 2. POST `/task/{task_id}`

**功能**: 查询任务状态和进度

### 输入 (Path Parameter)

```json
{
  "task_id": "task_uuid_string"
}
```

### 输出 (Response)

```json
{
  "task": {
    "id": "db_uuid",
    "user_id": "user_uuid",
    "task_id": "task_uuid",
    "repo_url": "https://github.com/...",
    "status": "processing",
    "progress": 45.5,
    "current_step": "正在生成 Wiki 内容...",
    "result": null,
    "error": null,
    "created_at": "2026-03-11T10:00:00Z",
    "last_updated": "2026-03-11T10:05:00Z"
  }
}
```

---

## 3. POST `/tasks`

**功能**: 列出指定用户的所有任务

### 输入 (Request Body)

```json
{
  "user_id": "user_uuid_string"
}
```

### 输出 (Response)

```json
{
  "tasks": [
    {
      "id": "db_uuid",
      "task_id": "task_uuid",
      "status": "completed",
      "progress": 100.0,
      "..." : "..."
    }
  ]
}
```

---

## 4. DELETE `/task/{task_id}`

**功能**: 删除已完成或失败的任务记录

### 输入 (Path Parameter)

```json
{
  "task_id": "task_uuid_string"
}
```

### 输出 (Response)

```json
{
  "message": "任务 task_uuid_string 已删除"
}
```

### 约束

- 仅可删除 `completed` 或 `failed` 的任务
- `processing` 的任务不可删除

---

## 5. POST `/chat`

**功能**: RAG 问答接口，并自动将对话记录存入 Supabase。

### 输入 (Request Body)

```json
{
  "user_id": "user_uuid_string",
  "repo_url": "https://github.com/user/repo",
  "question": "这个项目的核心功能是什么？",
  "chat_id": "chat_uuid_string (可选，不传则创建新会话)",
  "conversation_history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么我可以帮你的吗？"}
  ],
  "current_page_context": "当前正在查看 README.md 页面"
}
```

### 输出 (Response)

```json
{
  "answer": "该项目的核心功能包括...",
  "sources": ["README.md", "src/main.py"],
  "chat_id": "chat_uuid_string",
  "repo_url": "https://github.com/user/repo"
}
```

---

## 6. GET `/chat/repos`

**功能**: 列出所有已建立索引、可用于聊天的仓库

### 输出 (Response)

```json
{
  "repos": [
    {
      "repo_url": "https://github.com/user/repo",
      "vector_store_path": "/path/to/store",
      "last_updated": "2026-03-11T10:00:00Z"
    }
  ]
}
```

---

## 7. GET `/chat/history`

**功能**: 获取指定用户的所有聊天记录（会话列表）

### 输入 (Query Parameters)

- `user_id`: 用户 UUID

### 输出 (Response)

```json
{
  "history": [
    {
      "id": "chat_uuid",
      "user_id": "user_uuid",
      "repo_url": "https://github.com/...",
      "title": "会话标题",
      "created_at": "2026-03-11T10:00:00Z",
      "updated_at": "2026-03-11T10:05:00Z"
    }
  ]
}
```

---

## 8. GET `/chat/messages/{chat_id}`

**功能**: 获取指定会话的所有消息详情

### 输入 (Path Parameter)

- `chat_id`: 会话 UUID

### 输出 (Response)

```json
{
  "messages": [
    {
      "id": "message_uuid",
      "role": "user",
      "content": "问题内容",
      "metadata": {},
      "created_at": "2026-03-11T10:00:00Z"
    },
    {
      "id": "message_uuid",
      "role": "assistant",
      "content": "回答内容",
      "metadata": {"sources": ["README.md"]},
      "created_at": "2026-03-11T10:00:01Z"
    }
  ]
}
```

---

## 9. GET `/health`

**功能**: 健康检查

### 输出 (Response)

```json
{
  "status": "ok"
}
```

---

## 10. POST `/agent/chat`

**功能**: Agent 模式问答接口（高级模式）

与 RAG 模式不同，Agent 模式会：
1. 分析问题意图并制定探索计划
2. 迭代式收集上下文（使用 RAG、代码图谱、文件读取等工具）
3. 自我反思评估信息充分性
4. 生成带有 Mermaid 图表和精确溯源的答案

### 输入 (Request Body)

```json
{
  "user_id": "user_uuid_string",
  "repo_url": "https://github.com/user/repo",
  "question": "登录流程是如何实现的？",
  "chat_id": "chat_uuid_string (可选)",
  "conversation_history": [
    {"role": "user", "content": "你好"},
    {"role": "assistant", "content": "你好！有什么我可以帮你的吗？"}
  ],
  "current_page_context": "当前正在查看 AuthService.ts 页面"
}
```

### 输出 (Response)

```json
{
  "answer": "登录流程的实现涉及以下几个关键步骤...\n\n```typescript\n// AuthController.ts:25-40\nasync login(credentials) {...}\n```",
  "mermaid": "sequenceDiagram\n  User->>AuthController: login()\n  AuthController->>AuthService: validateCredentials()\n  AuthService->>Database: findUser()\n  Database-->>AuthService: User\n  AuthService-->>AuthController: Token\n  AuthController-->>User: Success",
  "sources": ["src/auth/AuthController.ts:25-40", "src/auth/AuthService.ts:10-30"],
  "trajectory": [
    {
      "step": 1,
      "tool": "rag_search",
      "description": "搜索知识库：登录流程实现...",
      "success": true
    },
    {
      "step": 2,
      "tool": "code_graph",
      "description": "查询代码图谱：find_callees(login)",
      "success": true
    }
  ],
  "confidence": 0.85,
  "iterations": 3,
  "chat_id": "chat_uuid_string",
  "repo_url": "https://github.com/user/repo"
}
```

### 响应字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| answer | string | 最终答案，包含代码引用和解释 |
| mermaid | string \| null | Mermaid 图表代码，可用于前端渲染 |
| sources | string[] | 引用的源文件列表，格式为 `file:line_range` |
| trajectory | object[] | Agent 推理轨迹，展示每步的工具调用 |
| confidence | number | 置信度分数 (0-1) |
| iterations | number | 反思循环迭代次数 |
| chat_id | string | 会话 ID |
| repo_url | string | 仓库 URL |

---

## 11. POST `/agent/chat/stream`

**功能**: Agent 模式流式问答接口 (Server-Sent Events)

实时返回 Agent 的思考过程和工具调用轨迹，适合需要展示"Agent 正在思考"等实时反馈的场景。

### 输入 (Request Body)

与 `/agent/chat` 相同。

### 输出 (Server-Sent Events)

```
event: planning
data: {"status": "开始分析问题..."}

event: tool_call
data: {"tool": "rag_search", "query": "登录流程", "status": "正在搜索..."}

event: tool_call
data: {"tool": "code_graph", "operation": "find_callees", "symbol": "login"}

event: evaluation
data: {"is_sufficient": false, "missing": ["需要查看 AuthService 实现"]}

event: synthesis
data: {"status": "正在生成最终答案..."}

event: complete
data: {"answer": "...", "mermaid": "...", "sources": [...], "confidence": 0.85}
```

### 事件类型

| 事件 | 说明 |
|------|------|
| planning | Agent 正在分析问题意图 |
| tool_call | Agent 正在调用工具 |
| evaluation | Agent 正在评估上下文充分性 |
| synthesis | Agent 正在合成最终答案 |
| complete | 执行完成，包含完整结果 |
| error | 发生错误 |

---

## 错误响应

所有错误接口均返回标准的 HTTP 状态码及以下格式的 JSON：

```json
{
  "detail": "错误详细描述信息"
}
```

### 常见状态码

```json
{
  "400": "请求参数缺失或非法操作",
  "404": "任务或资源不存在",
  "500": "服务器内部错误"
}
```
