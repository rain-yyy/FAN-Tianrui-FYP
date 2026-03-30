'use client';

import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useParams, useSearchParams } from 'react-router-dom';
import WikiViewer from '@/components/WikiViewer';
import { api, TaskStatusResponse } from '@/lib/api';
import { useAuth } from '@/providers/AuthProvider';
import { ComponentDataGuard } from '@/router/guards';

export default function WikiPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const loadTask = async () => {
      if (!taskId) return;
      setIsLoading(true);
      try {
        const result = await api.getTaskStatus(taskId);
        setStatus(result);
        localStorage.setItem('wiki_gen_task_id', taskId);
        const repo = result.result?.repo_url || result.repo_url;
        if (repo) {
          localStorage.setItem('wiki_gen_repo_url', repo);
        }
      } catch {
        setStatus(null);
      } finally {
        setIsLoading(false);
      }
    };
    void loadTask();
  }, [taskId]);

  const ready = useMemo(() => {
    return Boolean(
      status &&
        (status.status === 'completed' || status.status === 'cached') &&
        status.result?.r2_structure_url &&
        status.result?.r2_content_urls
    );
  }, [status]);

  const repoUrl = searchParams.get('repo') || status?.result?.repo_url || status?.repo_url || '';
  const initialChatId = searchParams.get('chatId') || undefined;

  if (isLoading) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-sky-600" />
      </div>
    );
  }

  return (
    <ComponentDataGuard allow={ready && Boolean(user)}>
      <WikiViewer
        userId={user!.id}
        structureUrl={status!.result!.r2_structure_url!}
        contentUrls={status!.result!.r2_content_urls!}
        repoUrl={repoUrl}
        initialChatId={initialChatId}
      />
    </ComponentDataGuard>
  );
}
