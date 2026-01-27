# Project Wiki Generation API 文档

## 概述

Project Wiki Generation API 是一个基于 FastAPI 的 RESTful API 服务，用于将项目仓库 URL 转换为 Wiki 结构和内容。该 API 采用异步任务模式，支持长时间运行的任务处理，并将生成的结果上传到 Cloudflare R2 存储。

## 基础信息

- **API 框架**: FastAPI
- **默认端口**: 8000
- **API 文档**: 访问 `/docs` 查看 Swagger UI，或访问 `/redoc` 查看 ReDoc
- **CORS**: 已启用，允许所有来源（开发环境）

## 部署信息

- **Docker 镜像**: `wiki-api-service:latest`
- **容器端口映射**: `8000:8000`
- **工作目录**: `/app`
- **Python 版本**: 3.11

## API 端点

### 1. 健康检查

检查 API 服务是否正常运行。

**端点**: `GET /health`

**响应**:

```json
{
  "status": "ok"
}
```

**状态码**: `200 OK`

---

### 2. 创建 Wiki 生成任务

创建一个新的 Wiki 生成任务。该接口会立即返回任务 ID，任务将在后台异步执行。

**端点**: `POST /generate`

**请求体**:

```json
{
  "url_link": "https://github.com/username/repository.git"
}
```

**请求参数**:

| 参数         | 类型   | 必填 | 说明                                                |
| ------------ | ------ | ---- | --------------------------------------------------- |
| `url_link` | string | 是   | 项目仓库的 URL（支持 HTTP/HTTPS/Git URL）或本地路径 |

**响应**:

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "任务已创建，正在后台处理。请使用 /task/{task_id} 查询进度。"
}
```

**状态码**: `200 OK`

**错误响应**:

- `500 Internal Server Error`: 服务器内部错误

**示例**:

```bash
curl -X POST "http://localhost:8000/generate" \
  -H "Content-Type: application/json" \
  -d '{"url_link": "https://github.com/username/repository.git"}'
```

---

### 3. 查询任务状态

查询指定任务的当前状态、进度和结果。

**端点**: `GET /task/{task_id}`

**路径参数**:

| 参数        | 类型   | 必填 | 说明                                 |
| ----------- | ------ | ---- | ------------------------------------ |
| `task_id` | string | 是   | 任务 ID（由 `/generate` 接口返回） |

**响应**:

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "processing",
  "progress": 45.0,
  "current_step": "正在生成 Wiki 内容（此步骤可能耗时较长）...",
  "created_at": "2026-01-27T10:00:00",
  "updated_at": "2026-01-27T10:05:00",
  "result": null,
  "error": null
}
```

**任务状态 (status)**:

- `pending`: 任务已创建，等待执行
- `processing`: 任务正在执行
- `completed`: 任务已完成
- `failed`: 任务执行失败

**完成时的响应示例**:

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "progress": 100.0,
  "current_step": "任务完成",
  "created_at": "2026-01-27T10:00:00",
  "updated_at": "2026-01-27T10:15:00",
  "result": {
    "r2_structure_url": "https://r2.example.com/wiki/xxx/wiki_structure.json",
    "r2_content_urls": [
      "https://r2.example.com/wiki/xxx/sections/part1.json",
      "https://r2.example.com/wiki/xxx/sections/part2.json"
    ],
    "json_wiki": null,
    "json_content": null
  },
  "error": null
}
```

**失败时的响应示例**:

```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "progress": 30.0,
  "current_step": "任务失败",
  "created_at": "2026-01-27T10:00:00",
  "updated_at": "2026-01-27T10:10:00",
  "result": null,
  "error": "仓库克隆失败: Connection timeout"
}
```

**状态码**:

- `200 OK`: 查询成功
- `404 Not Found`: 任务不存在

**示例**:

```bash
curl "http://localhost:8000/task/550e8400-e29b-41d4-a716-446655440000"
```

---

### 4. 列出所有任务

获取所有任务的列表（用于调试和管理）。

**端点**: `GET /tasks`

**响应**:

```json
[
  {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "status": "completed",
    "progress": 100.0,
    "current_step": "任务完成",
    "created_at": "2026-01-27T10:00:00",
    "updated_at": "2026-01-27T10:15:00",
    "result": {
      "r2_structure_url": "https://r2.example.com/wiki/xxx/wiki_structure.json",
      "r2_content_urls": [
        "https://r2.example.com/wiki/xxx/sections/part1.json",
        "https://r2.example.com/wiki/xxx/sections/part2.json"
      ],
      "json_wiki": null,
      "json_content": null
    },
    "error": null
  },
  {
    "task_id": "660e8400-e29b-41d4-a716-446655440001",
    "status": "processing",
    "progress": 50.0,
    "current_step": "正在生成 Wiki 内容...",
    "created_at": "2026-01-27T11:00:00",
    "updated_at": "2026-01-27T11:05:00",
    "result": null,
    "error": null
  }
]
```

**状态码**: `200 OK`

**示例**:

```bash
curl "http://localhost:8000/tasks"
```

---

### 5. 删除任务

删除已完成或失败的任务记录。正在处理中的任务不能删除。

**端点**: `DELETE /task/{task_id}`

**路径参数**:

| 参数        | 类型   | 必填 | 说明            |
| ----------- | ------ | ---- | --------------- |
| `task_id` | string | 是   | 要删除的任务 ID |

**响应**:

```json
{
  "message": "任务 550e8400-e29b-41d4-a716-446655440000 已删除"
}
```

**状态码**:

- `200 OK`: 删除成功
- `400 Bad Request`: 不能删除正在处理中或等待中的任务
- `404 Not Found`: 任务不存在

**示例**:

```bash
curl -X DELETE "http://localhost:8000/task/550e8400-e29b-41d4-a716-446655440000"
```

---

## 数据模型

### GenRequest

创建任务时的请求模型。

```typescript
{
  url_link: string  // 项目仓库 URL 或本地路径
}
```

### TaskCreateResponse

创建任务后的响应模型。

```typescript
{
  task_id: string      // 任务唯一标识符
  message: string      // 提示信息
}
```

### TaskStatusResponse

任务状态查询响应模型。

```typescript
{
  task_id: string                    // 任务唯一标识符
  status: "pending" | "processing" | "completed" | "failed"  // 任务状态
  progress: number                   // 进度百分比 (0-100)
  current_step: string               // 当前步骤描述
  created_at: string                 // 创建时间 (ISO 8601)
  updated_at: string                 // 更新时间 (ISO 8601)
  result: GenResponse | null         // 任务结果（完成时）
  error: string | null               // 错误信息（失败时）
}
```

### GenResponse

任务完成后的结果模型。

```typescript
{
  r2_structure_url: string | null      // R2 中 wiki_structure.json 的 URL
  r2_content_urls: string[] | null     // R2 中 content 目录的所有文件 URL
  json_wiki: string | null             // 保留字段（兼容性）
  json_content: string | null          // 保留字段（兼容性）
}
```

---

## 任务处理流程

Wiki 生成任务包含以下步骤：

1. **任务创建** (0%)

   - 接收仓库 URL，创建任务记录
2. **仓库准备** (5-15%)

   - 克隆或读取仓库
   - 加载配置文件
3. **文件树生成** (20%)

   - 分析仓库结构，生成文件树
4. **Wiki 结构生成** (30-40%)

   - 基于文件树生成 Wiki 目录结构
5. **Wiki 内容生成** (45-85%)

   - 初始化 AI 客户端
   - 逐条生成 Wiki 内容和 Mermaid 图
   - 写入 JSON 文件
6. **上传到 R2** (90%)

   - 上传 wiki_structure.json
   - 上传 content 目录
7. **任务完成** (100%)

   - 更新任务状态为 completed
   - 后台静默清理本地临时文件

---

## 错误处理

### 常见错误码

- `400 Bad Request`: 请求参数错误
- `404 Not Found`: 资源不存在（如任务不存在）
- `500 Internal Server Error`: 服务器内部错误

### 错误响应格式

```json
{
  "detail": "错误描述信息"
}
```

---

## 使用示例

### 完整工作流示例

```bash
# 1. 创建任务
RESPONSE=$(curl -X POST "http://localhost:8000/generate" \
  -H "Content-Type: application/json" \
  -d '{"url_link": "https://github.com/username/repository.git"}')

TASK_ID=$(echo $RESPONSE | jq -r '.task_id')
echo "任务 ID: $TASK_ID"

# 2. 轮询任务状态
while true; do
  STATUS=$(curl -s "http://localhost:8000/task/$TASK_ID" | jq -r '.status')
  PROGRESS=$(curl -s "http://localhost:8000/task/$TASK_ID" | jq -r '.progress')
  STEP=$(curl -s "http://localhost:8000/task/$TASK_ID" | jq -r '.current_step')
  
  echo "状态: $STATUS, 进度: $PROGRESS%, 步骤: $STEP"
  
  if [ "$STATUS" = "completed" ] || [ "$STATUS" = "failed" ]; then
    break
  fi
  
  sleep 5
done

# 3. 获取结果
RESULT=$(curl -s "http://localhost:8000/task/$TASK_ID")
echo $RESULT | jq '.result'

# 4. 清理任务（可选）
curl -X DELETE "http://localhost:8000/task/$TASK_ID"
```

---

## 环境变量

API 服务需要以下环境变量（通过 `.env` 文件或 Docker 环境变量配置）：

- `PYTHONPATH`: Python 模块搜索路径（默认: `/app`）
- `HOST`: 服务监听地址（默认: `0.0.0.0`）
- `PORT`: 服务端口（默认: `8000`）
- AI 客户端相关配置（OpenAI/Qwen/DeepSeek API Key）
- Cloudflare R2 存储配置（Access Key ID, Secret Access Key, Endpoint, Bucket）

---

## 注意事项

1. **任务存储**: 当前使用内存存储任务信息，服务重启后任务记录会丢失。生产环境建议使用 Redis 等持久化存储。
2. **并发限制**: 大量并发任务可能会消耗大量系统资源，建议在生产环境中添加任务队列和并发限制。
3. **文件清理**: 任务完成后会自动清理本地临时文件，但失败的任务可能保留部分文件。
4. **CORS 配置**: 当前允许所有来源的跨域请求，生产环境应限制为特定域名。
5. **任务超时**: 长时间运行的任务可能会超时，建议实现任务超时机制。

---

## 开发与调试

### 本地运行

```bash
cd docker
docker-compose up --build
```

### 查看日志

```bash
docker logs -f wiki-api-container
```

### 访问 API 文档

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## 版本信息

- **API 版本**: 1.0.0
- **FastAPI 版本**: >= 0.104.0
- **Python 版本**: 3.11
