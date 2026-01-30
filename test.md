# Docker API 完整测试流程文档

本文档提供了一份完整的 Docker API 测试流程，涵盖了 Wiki 生成和 RAG 聊天两大核心功能模块。你可以按照以下步骤手动执行测试。

---

## 1. 准备工作

在开始测试之前，请确保：
- Docker 容器已启动：`docker-compose up -d`
- API 服务运行在：`http://localhost:8000`
- 已安装 `curl` 和 `jq`（可选，用于美化 JSON 输出）

---

## 2. 基础健康检查

验证 API 服务是否正常响应。

```bash
# 健康检查
curl -X GET "http://localhost:8000/health"
```

---

## 3. Wiki 生成工作流测试 (Workflow 1)

此工作流测试从仓库 URL 生成 Wiki 结构、内容并上传到 R2 的异步过程。

### 步骤 1：创建生成任务
替换 `url_link` 为你想测试的 GitHub 仓库地址。

```bash
curl -X POST "http://localhost:8000/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "url_link": "https://github.com/FAN-Tianrui-FYP/FAN-Tianrui-FYP.git",
    "user_id": "test_user_001"
  }'
```
**预期输出**: 返回 `task_id`。

### 步骤 2：轮询任务进度
将 `{task_id}` 替换为上一步获取的 ID。

```bash
curl -X GET "http://localhost:8000/task/{task_id}"
```
**说明**: 你可以多次执行此命令观察 `progress` 和 `current_step` 的变化。直到 `status` 变为 `completed`。

### 步骤 3：查看所有活跃任务
列出内存中存储的所有任务状态。

```bash
curl -X GET "http://localhost:8000/tasks"
```

### 步骤 4：删除任务记录
任务完成后，可以清理掉任务记录（仅限 `completed` 或 `failed` 状态）。

```bash
curl -X DELETE "http://localhost:8000/task/{task_id}"
```

---

## 4. RAG 聊天工作流测试 (Workflow 2)

此工作流测试基于已索引仓库的智能问答功能。

### 步骤 1：获取可用仓库列表
检查哪些仓库已经完成了向量索引。

```bash
curl -X GET "http://localhost:8000/chat/repos"
```

### 步骤 2：发起 RAG 提问
选择一个已索引的 `repo_url` 进行提问。如果是新会话，无需提供 `chat_id`。

```bash
curl -X POST "http://localhost:8000/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "question": "这个项目的核心功能是什么？",
    "repo_url": "https://github.com/FAN-Tianrui-FYP/FAN-Tianrui-FYP.git",
    "user_id": "test_user_001"
  }'
```
**预期输出**: 返回 `answer`、`sources` 和 `chat_id`。

### 步骤 3：获取用户会话列表
查看指定用户的所有聊天记录。

```bash
curl -X GET "http://localhost:8000/chat/sessions?user_id=test_user_001"
```

### 步骤 4：查看具体会话详情
获取某个会话的所有历史消息。

```bash
curl -X GET "http://localhost:8000/chat/session/{chat_id}?include_messages=true"
```


### 步骤 6：删除会话
清理不需要的聊天会话。

```bash
curl -X DELETE "http://localhost:8000/chat/session/{chat_id}"
```

---

## 5. 常见问题排查

1. **404 错误**: 
   - 检查 `task_id` 或 `chat_id` 是否正确。
   - 确保在调用 `/chat` 之前，该仓库已通过 `/generate` 完成了索引构建。
2. **500 错误**:
   - 检查 Docker 日志：`docker logs -f wiki-api-container`。
   - 验证环境变量（如 API Keys, R2 配置）是否正确设置。
3. **任务长时间处于 processing**:
   - 大型仓库生成 Wiki 内容可能需要 5-10 分钟，请耐心等待。
   - 检查网络连接是否可以正常克隆 GitHub 仓库。

---

## 6. 快捷测试脚本 (PowerShell)
 
如果你使用 PowerShell，可以使用以下脚本快速测试：

```powershell
# 1. 创建任务
$resp = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/generate" `
    -ContentType "application/json" `
    -Body '{"url_link": "https://github.com/FAN-Tianrui-FYP/FAN-Tianrui-FYP.git", "user_id": "ps_user"}'
$taskId = $resp.task_id
Write-Host "Created Task: $taskId"

# 2. 循环检查直到完成
do {
    $statusResp = Invoke-RestMethod -Method Get -Uri "http://localhost:8000/task/$taskId"
    Write-Host "Status: $($statusResp.status), Progress: $($statusResp.progress)%, Step: $($statusResp.current_step)"
    if ($statusResp.status -eq "completed" -or $statusResp.status -eq "failed") { break }
    Start-Sleep -Seconds 10
} while ($true)

# 3. 提问
if ($statusResp.status -eq "completed") {
    $chatResp = Invoke-RestMethod -Method Post -Uri "http://localhost:8000/chat" `
        -ContentType "application/json" `
        -Body "{`"question`": `"介绍一下这个项目`", `"repo_url`": `"https://github.com/FAN-Tianrui-FYP/FAN-Tianrui-FYP.git`", `"user_id`": `"ps_user`"}"
    Write-Host "AI Answer: $($chatResp.answer)"
}
```
