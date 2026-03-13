//TODO : 记得改回来
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000' || 'https://cityu-fyp-testapi.tianruifan21.workers.dev';

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

export interface ChatRequest {
  user_id: string;
  question: string;
  repo_url: string;
  chat_id?: string;
  conversation_history?: ChatMessage[];
  current_page_context?: string;
}

export interface ChatResponse {
  answer: string;
  sources: string[];
  chat_id: string;
  repo_url: string;
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

export interface TasksResponse {
  tasks: TaskStatusResponse[];
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
    throw new Error(detail || 'Request failed');
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
    try {
      const data = await requestJson<{ task: TaskStatusResponse }>(`/task/${task_id}`, {
        method: 'POST',
      });
      return data.task;
    } catch (error) {
      throw error;
    }
  },

  getTasks: async (user_id: string): Promise<TasksResponse> => {
    return requestJson<TasksResponse>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ user_id }),
    });
  },

  askQuestion: async (request: ChatRequest): Promise<ChatResponse> => {
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
          throw new Error('未找到该仓库的向量索引。请先生成文档后再使用聊天功能。');
        } else if (status === 400) {
          throw new Error(`请求无效: ${detail}`);
        } else {
          throw new Error(`聊天请求失败: ${detail}`);
        }
      }
      return res.json();
    } catch (error) {
      console.error('Ask question failed:', error);
      throw error;
    }
  },

  getAvailableRepos: async (): Promise<AvailableReposResponse> => {
    try {
      const data = await requestJson<{ repos: AvailableRepo[] }>('/chat/repos', { method: 'GET' });
      return { repos: data.repos, count: data.repos.length };
    } catch {
      return { repos: [], count: 0 };
    }
  },
};
