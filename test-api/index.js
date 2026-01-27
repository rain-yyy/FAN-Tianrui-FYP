const tasks = new Map();

// 生成 UUID v4
const generateUUID = () => {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
};

// 固定的响应结果（基于 request-result.txt）
const FIXED_RESPONSE = {
  task_id: "ad9eb02b-c932-426f-8305-63a9f965fc61",
  status: "completed",
  progress: 100.0,
  current_step: "任务完成",
  created_at: "2026-01-27T06:47:34.657336",
  updated_at: "2026-01-27T06:50:46.866852",
  result: {
    r2_structure_url: "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/wiki_structure.json",
    r2_content_urls: [
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/infrastructure.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/data-models.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/security.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/overview.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/observability.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/license.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/deployment.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/document-processing-pipeline.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/user-guides.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/contributing.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/core-backend-services.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/ai-features.json",
      "https://cityu-fyp.ed8ae7084f2b8a4d83482ce8a3fe4a83.r2.cloudflarestorage.com/Folo/20260127/sections/system-architecture.json"
    ],
    json_wiki: null,
    json_content: null
  },
  error: null
};

// 获取固定响应结果（替换 task_id）
const getFixedResponse = (taskId) => {
  return {
    ...FIXED_RESPONSE,
    task_id: taskId
  };
};

// 处理 CORS
const handleCORS = (request) => {
  const origin = request.headers.get('Origin');
  const headers = {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type': 'application/json',
  };
  return headers;
};

// 创建 JSON 响应
const jsonResponse = (data, status = 200, request, additionalHeaders = {}) => {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      ...handleCORS(request),
      ...additionalHeaders,
    },
  });
};

// 主处理函数
export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;

    // 处理 OPTIONS 请求（CORS 预检）
    if (method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: handleCORS(request),
      });
    }

    // GET /health
    if (method === 'GET' && path === '/health') {
      return jsonResponse({ status: 'ok' }, 200, request);
    }

    // POST /generate
    if (method === 'POST' && path === '/generate') {
      try {
        const body = await request.json();
        const { url_link } = body;

        if (!url_link) {
          return jsonResponse(
            { detail: 'url_link 参数是必需的' },
            400,
            request
          );
        }

        const taskId = generateUUID();
        
        // 记录任务已创建（仅用于跟踪）
        tasks.set(taskId, { created: true });

        return jsonResponse({
          task_id: taskId,
          message: '任务已创建，正在后台处理。请使用 /task/{task_id} 查询进度。',
        }, 200, request);
      } catch (error) {
        return jsonResponse(
          { detail: '请求体解析失败' },
          400,
          request
        );
      }
    }

    // GET /task/{task_id}
    if (method === 'GET' && path.startsWith('/task/')) {
      const taskId = path.split('/task/')[1];
      
      if (!taskId) {
        return jsonResponse(
          { detail: 'task_id 参数是必需的' },
          400,
          request
        );
      }

      // 不管 task_id 是什么，都返回固定的结果（使用 request-result.txt 中的格式）
      const response = getFixedResponse(taskId);
      return jsonResponse(response, 200, request);
    }

    // GET /tasks
    if (method === 'GET' && path === '/tasks') {
      // 返回所有已创建的任务，每个都使用固定结果格式
      const allTasks = Array.from(tasks.keys()).map(taskId => getFixedResponse(taskId));
      return jsonResponse(allTasks, 200, request);
    }

    // DELETE /task/{task_id}
    if (method === 'DELETE' && path.startsWith('/task/')) {
      const taskId = path.split('/task/')[1];
      
      if (!taskId) {
        return jsonResponse(
          { detail: 'task_id 参数是必需的' },
          400,
          request
        );
      }

      const task = tasks.get(taskId);
      
      if (!task) {
        return jsonResponse(
          { detail: '任务不存在' },
          404,
          request
        );
      }

      // 删除任务记录
      tasks.delete(taskId);
      
      return jsonResponse({
        message: `任务 ${taskId} 已删除`,
      }, 200, request);
    }

    // 404 处理
    return jsonResponse(
      { detail: '接口不存在' },
      404,
      request
    );
  },
};