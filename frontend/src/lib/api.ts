import axios from 'axios';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'https://fan-tianrui-fyp.fly.dev';

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

  createTask: async (url_link: string): Promise<TaskResponse> => {
    const res = await axios.post<TaskResponse>(`${API_BASE_URL}/generate`, { url_link });
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
};
