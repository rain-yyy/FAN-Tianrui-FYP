'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ChevronRight, Clock, Github, Loader2, Trash2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api, TaskStatusResponse } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/providers/AuthProvider';
import { prefetchRouteModule } from '@/router/prefetch';
import { t } from '@/lib/i18n';

export default function HistoryPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskStatusResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!user) return;
    setIsLoading(true);
    try {
      const taskResult = await api.getTasks(user.id);
      setTasks(taskResult.tasks);
    } finally {
      setIsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    prefetchRouteModule('wiki');
    void loadData();
  }, [loadData]);

  const sortedTasks = useMemo(() => {
    return [...tasks].sort(
      (a, b) => new Date(b.last_updated || b.created_at).getTime() - new Date(a.last_updated || a.created_at).getTime()
    );
  }, [tasks]);

  const handleOpenTask = (task: TaskStatusResponse) => {
    if ((task.status !== 'completed' && task.status !== 'cached') || !task.result) {
      alert('This task did not complete successfully.');
      return;
    }
    navigate(`/app/wiki/${task.task_id}?repo=${encodeURIComponent(task.result.repo_url || task.repo_url)}`);
  };

  const handleDeleteTask = async (task: TaskStatusResponse, event: React.MouseEvent) => {
    event.stopPropagation();
    if (!user) return;
    setDeletingTaskId(task.task_id);
    try {
      if (task.status === 'processing' || task.status === 'pending') {
        try {
          await api.cancelTask(task.task_id);
        } catch (err) {
          throw new Error(err instanceof Error ? `${t('cancelTaskFailed')}: ${err.message}` : t('cancelTaskFailed'));
        }
      }
      
      await api.deleteTask(task.task_id, user.id);
      setTasks((prev) => prev.filter((item) => item.task_id !== task.task_id));
    } catch (err) {
      alert(err instanceof Error ? err.message : t('deleteFailed'));
    } finally {
      setDeletingTaskId(null);
    }
  };

  const getStatusClass = (status: string) => {
    switch (status) {
      case 'completed':
        return 'text-emerald-700';
      case 'cached':
        return 'text-sky-700';
      case 'failed':
        return 'text-rose-700';
      default:
        return 'text-stone-500';
    }
  };

  if (isLoading) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-sky-600" />
      </div>
    );
  }

  if (sortedTasks.length === 0) {
    return (
      <div className="min-h-[50vh] flex items-center justify-center">
        <div className="text-center py-12 bg-stone-50 rounded-2xl border border-dashed border-stone-200 px-12">
          <Clock className="w-12 h-12 text-stone-400 mx-auto mb-4" />
          <p className="text-stone-600">{t('noHistory')}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <h1 className="text-2xl font-semibold text-stone-900">{t('historyTitle')}</h1>
      <div className="space-y-3 min-w-0">
        <h2 className="text-lg font-medium text-stone-800">{t('tasks')}</h2>
        <div className="space-y-3">
          {sortedTasks.map((item) => {
            const clickable = item.status === 'completed' || item.status === 'cached';
            const deleting = deletingTaskId === item.task_id;
            return (
              <div
                key={`task-${item.id}`}
                role="button"
                tabIndex={0}
                onClick={() => !deleting && clickable && handleOpenTask(item)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    if (!deleting && clickable) handleOpenTask(item);
                  }
                }}
                className={cn(
                  'w-full text-left p-4 bg-white border border-stone-200 rounded-xl transition-all shadow-sm',
                  deleting
                    ? 'opacity-60'
                    : clickable
                      ? 'hover:bg-stone-50 hover:border-sky-300 cursor-pointer'
                      : 'opacity-75 cursor-not-allowed'
                )}
              >
                <div className="flex items-start justify-between">
                  <div className="space-y-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <Github className="w-4 h-4 text-stone-500 shrink-0" />
                      <span className="font-mono text-sm text-stone-900 break-all">{item.repo_url}</span>
                    </div>
                    <div className="text-xs text-stone-500 inline-flex items-center gap-2">
                      <span>{new Date(item.created_at).toLocaleDateString('en-US')}</span>
                      <span>•</span>
                      <span className={getStatusClass(item.status)}>{item.status}</span>
                    </div>
                  </div>
                  <div className="inline-flex items-center gap-2 ml-4 shrink-0">
                    <button
                      type="button"
                      onClick={(event) => void handleDeleteTask(item, event)}
                      disabled={deleting}
                      aria-label="Delete task"
                      className="p-1.5 rounded-md text-stone-500 hover:text-rose-700 hover:bg-rose-50"
                    >
                      {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    </button>
                    <ChevronRight className="w-5 h-5 text-stone-400 shrink-0" />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
