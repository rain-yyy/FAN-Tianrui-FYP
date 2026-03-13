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
