import axios from 'axios';

// is test api now, will be changed to the production api later
// TODO: change to the production api
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://cityu-fyp-testapi.tianruifan21.workers.dev';

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

// ============ Chat API Types ============

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatRequest {
  question: string;
  repo_url: string;
  conversation_history?: ChatMessage[];
  current_page_context?: string;
}

export interface ChatResponse {
  answer: string;
  sources: string[];
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
  task_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  progress: number;
  current_step: string;
  created_at: string;
  updated_at: string;
  result: GenResponse | null;
  error: string | null;
}

export const api = {
  checkHealth: async (): Promise<boolean> => {
    try {
      const res = await axios.get<HealthResponse>(`${API_BASE_URL}/health`);
      return res.data.status === 'ok';
    } catch (error) {
      console.error('Health check failed:', error);
      return false;
    }
  },

  createTask: async (url_link: string, user_id: string): Promise<TaskResponse> => {
    const res = await axios.post<TaskResponse>(`${API_BASE_URL}/generate`, { url_link, user_id });
    return res.data;
  },

  getTaskStatus: async (task_id: string): Promise<TaskStatusResponse> => {
    try {
      const res = await axios.get<TaskStatusResponse>(`${API_BASE_URL}/task/${task_id}`);
      return res.data;
    } catch (error) {
      if (axios.isAxiosError(error) && error.response?.status === 404) {
        return {
          task_id,
          status: 'failed',
          progress: 0,
          current_step: 'Task not found',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          result: null,
          error: 'Task not found or expired'
        };
      }
      throw error;
    }
  },

  // ============ RAG Chat API ============

  /**
   * 发送问题到 RAG 聊天接口
   * @param request 聊天请求参数
   * @returns 聊天响应，包含答案和来源
   */
  askQuestion: async (request: ChatRequest): Promise<ChatResponse> => {
    try {
      const res = await axios.post<ChatResponse>(`${API_BASE_URL}/chat`, request);
      return res.data;
    } catch (error) {
      if (axios.isAxiosError(error)) {
        const status = error.response?.status;
        const detail = error.response?.data?.detail || error.message;
        
        if (status === 404) {
          throw new Error('未找到该仓库的向量索引。请先生成文档后再使用聊天功能。');
        } else if (status === 400) {
          throw new Error(`请求无效: ${detail}`);
        } else {
          throw new Error(`聊天请求失败: ${detail}`);
        }
      }
      throw error;
    }
  },

  /**
   * 获取所有可用于聊天的仓库列表
   * @returns 可用仓库列表
   */
  getAvailableRepos: async (): Promise<AvailableReposResponse> => {
    try {
      const res = await axios.get<AvailableReposResponse>(`${API_BASE_URL}/chat/repos`);
      return res.data;
    } catch (error) {
      console.error('Failed to fetch available repos:', error);
      return { repos: [], count: 0 };
    }
  },
};
