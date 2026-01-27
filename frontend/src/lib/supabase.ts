import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL || '';
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '';

if (!supabaseUrl || !supabaseAnonKey) {
  console.warn('Supabase credentials are missing. Please check your .env.local file.');
}

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

// Helper functions for common database operations
export const supabaseApi = {
  // Sync a new task to Supabase
  syncTaskStart: async (userId: string, taskId: string, repoUrl: string) => {
    const { error } = await supabase.from('tasks').insert({
      user_id: userId,
      task_id: taskId,
      repo_url: repoUrl,
      status: 'processing',
    });
    return { error };
  },

  // Sync task completion or failure
  syncTaskEnd: async (taskId: string, status: 'completed' | 'failed', result: any = null) => {
    const { error } = await supabase
      .from('tasks')
      .update({
        status,
        result,
      })
      .eq('task_id', taskId);
    return { error };
  },

  // Sync repository info
  syncRepository: async (repoUrl: string, structureUrl: string, contentUrls: string[]) => {
    const { error } = await supabase.from('repositories').upsert({
      repo_url: repoUrl,
      r2_structure_url: structureUrl,
      r2_content_urls: contentUrls,
      last_updated: new Date().toISOString(),
    });
    return { error };
  },

  // Get user task history
  getTaskHistory: async (userId: string) => {
    const { data, error } = await supabase
      .from('tasks')
      .select('*')
      .eq('user_id', userId)
      .order('created_at', { ascending: false });
    return { data, error };
  },

  // Get repository history
  getProcessedRepositories: async () => {
    const { data, error } = await supabase
      .from('repositories')
      .select('*')
      .order('last_updated', { ascending: false });
    return { data, error };
  },

  // Get user profile
  getProfile: async (userId: string) => {
    const { data, error } = await supabase
      .from('profiles')
      .select('*')
      .eq('id', userId)
      .single();
    return { data, error };
  },

  // Update user profile
  updateProfile: async (userId: string, updates: any) => {
    const { error } = await supabase
      .from('profiles')
      .update({
        ...updates,
        updated_at: new Date().toISOString(),
      })
      .eq('id', userId);
    return { error };
  }
};
