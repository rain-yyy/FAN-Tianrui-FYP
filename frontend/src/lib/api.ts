//const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? '').trim() || 'http://localhost:8000';
const API_BASE_URL = 'http://localhost:8000';
// const API_BASE_URL = "https://fan-tianrui-fyp.fly.dev"

/**
 * Normalize repo URL for consistent comparison with backend-stored URLs.
 * Matches backend _normalize_repo_url behavior for common cases.
 */
export const normalizeRepoUrl = (url: string): string => {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim().toLowerCase();
  if (!trimmed) return '';
  const withoutGit = trimmed.replace(/\.git\/?$/i, '').replace(/\/+$/, '');
  try {
    const parsed = new URL(withoutGit.startsWith('http') ? withoutGit : `https://${withoutGit}`);
    const pathParts = parsed.pathname.split('/').filter(Boolean);
    if (pathParts.length >= 2) {
      return `${parsed.protocol}//${parsed.host}/${pathParts[0]}/${pathParts[1]}`;
    }
  } catch {
    // Fallback for malformed URLs
  }
  return withoutGit;
};

export interface HealthResponse {
  status: string;
}

export interface TaskResponse {
  task_id: string;
  message: string;
}

export interface GenResponse {
  r2_structure_url: string | null;
  r2_content_urls: string[] | null;
  json_wiki: string | null;
  json_content: string | null;
  vector_store_path: string | null;
  repo_url: string | null;
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

// ============ Unified chat types (single agent-first /chat, /chat/stream) ============

export interface ChatTurnRequest {
  question: string;
  repo_url: string;
  user_id: string;
  chat_id?: string | null;
  current_page_context?: string | null;
}

export interface ToolTrajectoryStep {
  tool: string;
  arguments: Record<string, unknown>;
  status: 'success' | 'error';
  summary: string;
  duration_ms: number | null;
}

export interface ChatTurnResponse {
  chat_id: string;
  repo_url: string;
  answer: string;
  sources: string[];
  tool_trajectory: ToolTrajectoryStep[];
  iterations: number;
}

export type ChatStreamEvent =
  | { type: 'turn_start'; data: { chat_id: string; repo_url: string } }
  | { type: 'iteration_start'; data: { iteration: number; max_iterations: number } }
  | { type: 'tool_call_start'; data: { tool: string; arguments: Record<string, unknown>; iteration: number } }
  | { type: 'tool_call_result'; data: { tool: string; status: 'success' | 'error'; summary: string } }
  | { type: 'answer_token'; data: { delta: string } }
  | { type: 'answer_done'; data: { answer: string; sources: string[] } }
  | { type: 'complete'; data: { chat_id: string; repo_url: string } }
  | { type: 'error'; data: { detail: string } };

export interface ChatHistoryItem {
  id: string;
  chat_id: string;
  user_id: string;
  repo_url: string;
  created_at: string;
  updated_at: string;
  title?: string;
  message_count?: number;
}

export interface ChatHistoryMessage {
  id: string;
  chat_id: string;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
  metadata?: Record<string, unknown>;
}

export interface AvailableRepo {
  repo_url: string | null;
  vector_store_path: string;
  repo_hash?: string;
  has_code_index: boolean;
  has_text_index: boolean;
}

export interface AvailableReposResponse {
  repos: AvailableRepo[];
  count: number;
}

export interface TaskStatusResponse {
  id: string;
  user_id: string;
  task_id: string;
  repo_url: string;
  status: 'pending' | 'processing' | 'completed' | 'cached' | 'failed';
  progress: number;
  current_step: string;
  created_at: string;
  last_updated: string;
  result: GenResponse | null;
  error: string | null;
}

/** 工作台卡片：以 repositories 表为准，含跳转 Wiki 用的 task_id */
export interface DashboardRepoEntry {
  repo_url: string;
  task_id: string;
  github_short_description?: string | null;
  description?: string | null;
  stargazers_count?: number | null;
  vector_store_path?: string | null;
  last_updated?: string | null;
}

export interface DashboardReposResponse {
  repos: DashboardRepoEntry[];
}


const requestJson = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
    ...init,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errorData = await res.json() as { detail?: string };
      detail = errorData.detail || detail;
    } catch {
      // ignore json parsing error and use status text
    }
    const err = new Error(detail || 'Request failed') as Error & { statusCode?: number };
    err.statusCode = res.status;
    throw err;
  }

  return res.json() as Promise<T>;
};

export const api = {
  checkHealth: async (): Promise<boolean> => {
    try {
      const data = await requestJson<HealthResponse>('/health', { method: 'GET' });
      return data.status === 'ok';
    } catch {
      return false;
    }
  },

  createTask: async (url_link: string, user_id: string): Promise<TaskResponse> => {
    return requestJson<TaskResponse>('/generate', {
      method: 'POST',
      body: JSON.stringify({ url_link, user_id }),
    });
  },

  getTaskStatus: async (task_id: string): Promise<TaskStatusResponse> => {
    return requestJson<TaskStatusResponse>(`/task/${task_id}`, {
      method: 'POST',
    });
  },

  getTasks: async (user_id: string): Promise<TaskStatusResponse[]> => {
    return requestJson<TaskStatusResponse[]>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ user_id }),
    });
  },

  getDashboardRepos: async (user_id: string): Promise<DashboardReposResponse> => {
    return requestJson<DashboardReposResponse>('/dashboard/repos', {
      method: 'POST',
      body: JSON.stringify({ user_id }),
    });
  },

  cancelTask: async (taskId: string): Promise<boolean> => {
    const url = `/task/${encodeURIComponent(taskId)}/cancel`;
    const res = await fetch(`${API_BASE_URL}${url}`, { method: 'POST' });
    let body: { success?: boolean; message?: string; detail?: string | unknown[] } = {};
    try {
      body = (await res.json()) as typeof body;
    } catch {
      // ignore invalid JSON
    }
    if (!res.ok) {
      const d = body.detail;
      const detail =
        typeof d === 'string' ? d : Array.isArray(d) ? JSON.stringify(d) : res.statusText;
      throw new Error(detail || 'Failed to cancel task');
    }
    if (body.success === false) {
      throw new Error(
        (typeof body.message === 'string' && body.message) ||
          (typeof body.detail === 'string' ? body.detail : '') ||
          'Failed to cancel task',
      );
    }
    return true;
  },

  deleteTask: async (taskId: string, userId: string): Promise<boolean> => {
    const url = `/task/${encodeURIComponent(taskId)}?user_id=${encodeURIComponent(userId)}`;
    const res = await fetch(`${API_BASE_URL}${url}`, { method: 'DELETE' });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const errorData = await res.json() as { detail?: string };
        detail = errorData.detail || detail;
      } catch {
        // ignore
      }
      throw new Error(detail || 'Delete failed');
    }
    return true;
  },

  askQuestion: async (request: ChatTurnRequest): Promise<ChatTurnResponse> => {
    try {
      const res = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
      });

      if (!res.ok) {
        const status = res.status;
        let detail = res.statusText;
        try {
          const errorData = await res.json();
          detail = errorData.detail || detail;
        } catch {
          // ignore
        }

        if (status === 404) {
          throw new Error('No vector index for this repo. Generate documentation first, then try chat again.');
        } else if (status === 400) {
          throw new Error(`Invalid request: ${detail}`);
        } else {
          throw new Error(`Chat request failed: ${detail}`);
        }
      }
      return res.json();
    } catch (error) {
      console.error('Ask question failed:', error);
      throw error;
    }
  },

  getAvailableRepos: async (): Promise<AvailableReposResponse> => {
    const data = await requestJson<{ repos?: AvailableRepo[] }>('/chat/repos', { method: 'GET' });
    const repos = data.repos ?? [];
    return { repos, count: repos.length };
  },

  getChatHistory: async (userId: string): Promise<ChatHistoryItem[]> => {
    try {
      const data = await requestJson<{ history: ChatHistoryItem[] }>(`/chat/history?user_id=${encodeURIComponent(userId)}`, { method: 'GET' });
      const raw = data.history || [];
      return raw.map((item) => ({
        ...item,
        chat_id: item.chat_id ?? item.id,
      }));
    } catch (error) {
      console.error('[api] getChatHistory failed:', error);
      return [];
    }
  },

  getChatMessages: async (chatId: string): Promise<ChatHistoryMessage[]> => {
    try {
      const data = await requestJson<{ messages: ChatHistoryMessage[] }>(`/chat/messages/${encodeURIComponent(chatId)}`, { method: 'GET' });
      return data.messages || [];
    } catch (error) {
      console.error('[api] getChatMessages failed:', error);
      return [];
    }
  },

  deleteChatHistory: async (chatId: string, userId: string): Promise<boolean> => {
    const url = `/chat/history/${encodeURIComponent(chatId)}?user_id=${encodeURIComponent(userId)}`;
    const res = await fetch(`${API_BASE_URL}${url}`, { method: 'DELETE' });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const errorData = await res.json() as { detail?: string };
        detail = errorData.detail || detail;
      } catch {
        // ignore
      }
      throw new Error(detail || 'Delete failed');
    }
    return true;
  },

  getFileContent: async (repoUrl: string, filePath: string): Promise<string> => {
    try {
      const res = await fetch(`${API_BASE_URL}/file/content`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url: repoUrl, file_path: filePath }),
      });

      if (!res.ok) {
         throw new Error(`Failed to fetch file: ${res.statusText}`);
      }
      const data = await res.json();
      return data.content;
    } catch (error) {
      console.error('Failed to get file content:', error);
      throw error;
    }
  },

  // Unified agent-first chat streaming API (single event vocabulary, single node loop)
  askQuestionStream: async function* (
    request: ChatTurnRequest
  ): AsyncGenerator<ChatStreamEvent, void, unknown> {
    const res = await fetch(`${API_BASE_URL}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });

    if (!res.ok) {
      throw new Error(`Chat stream request failed: ${res.statusText}`);
    }

    const reader = res.body?.getReader();
    if (!reader) {
      throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';
    let currentEventType: ChatStreamEvent['type'] = 'turn_start';
    let currentDataLines: string[] = [];

    const emitCurrentEvent = (): ChatStreamEvent | null => {
      if (currentDataLines.length === 0) return null;
      const rawData = currentDataLines.join('\n').trim();
      currentDataLines = [];
      if (!rawData) return null;
      try {
        const data = JSON.parse(rawData) as Record<string, unknown>;
        return { type: currentEventType, data } as ChatStreamEvent;
      } catch {
        return {
          type: 'error',
          data: { detail: 'Invalid SSE data payload' },
        };
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (trimmed === '') {
          const event = emitCurrentEvent();
          if (event) {
            yield event;
          }
          continue;
        }

        if (trimmed.startsWith('event:')) {
          const rawType = trimmed.slice(6).trim() as ChatStreamEvent['type'];
          currentEventType = rawType || 'turn_start';
          continue;
        }

        if (trimmed.startsWith('data:')) {
          currentDataLines.push(trimmed.slice(5).trim());
        }
      }
    }

    const tailEvent = emitCurrentEvent();
    if (tailEvent) {
      yield tailEvent;
    }
  },

};
