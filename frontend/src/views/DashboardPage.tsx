'use client';

import { FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, Link2, Loader2, RotateCcw, ShieldCheck, Sparkles, Terminal } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api, TaskStatusResponse } from '@/lib/api';
import { cn } from '@/lib/utils';
import { useAuth } from '@/providers/AuthProvider';
import { prefetchRouteModule } from '@/router/prefetch';
import RepoGrid from '@/components/RepoGrid';
import { t } from '@/lib/i18n';

export default function DashboardPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [url, setUrl] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [health, setHealth] = useState<boolean | null>(null);

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);
  const activePollTaskIdRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  const pollAttemptRef = useRef(0);
  const maxRetries = 5;

  const getPollDelayMs = useCallback((attempt: number) => {
    if (typeof document !== 'undefined' && document.hidden) return 20000;
    if (attempt <= 1) return 5000;
    if (attempt <= 3) return 10000;
    return 20000;
  }, []);

  const clearTask = useCallback(() => {
    setTaskId(null);
    setStatus(null);
    localStorage.removeItem('wiki_gen_task_id');
    localStorage.removeItem('wiki_gen_repo_url');
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    activePollTaskIdRef.current = null;
    pollInFlightRef.current = false;
    pollAttemptRef.current = 0;
  }, []);

  const goToWiki = useCallback(
    (finishedTaskId: string, repoUrl: string) => {
      prefetchRouteModule('wiki');
      navigate(`/app/wiki/${finishedTaskId}?repo=${encodeURIComponent(repoUrl)}`);
    },
    [navigate]
  );

  const pollStatus = useCallback(
    async (id: string) => {
      if (activePollTaskIdRef.current !== id) return;
      if (pollInFlightRef.current) return;
      pollInFlightRef.current = true;

      try {
        const result = await api.getTaskStatus(id);
        setStatus(result);
        pollAttemptRef.current = 0;

        if (result.status === 'completed' || result.status === 'cached') {
          if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
          activePollTaskIdRef.current = null;
          pollInFlightRef.current = false;
          const repo = result.result?.repo_url || localStorage.getItem('wiki_gen_repo_url') || url;
          goToWiki(id, repo);
          return;
        }

        if (result.status === 'failed') {
          pollInFlightRef.current = false;
          return;
        }

        pollInFlightRef.current = false;
        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
        pollTimerRef.current = setTimeout(() => {
          void pollStatus(id);
        }, getPollDelayMs(pollAttemptRef.current));
      } catch (error) {
        pollInFlightRef.current = false;
        const statusCode = (error as Error & { statusCode?: number }).statusCode;
        if (statusCode === 404) {
          clearTask();
          return;
        }

        pollAttemptRef.current += 1;
        if (pollAttemptRef.current >= maxRetries) {
          clearTask();
          return;
        }

        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
        pollTimerRef.current = setTimeout(() => {
          void pollStatus(id);
        }, getPollDelayMs(pollAttemptRef.current));
      }
    },
    [clearTask, getPollDelayMs, goToWiki, url]
  );

  useEffect(() => {
    const bootstrap = async () => {
      const isOk = await api.checkHealth();
      setHealth(isOk);
    };
    void bootstrap();
    prefetchRouteModule('history');

    const tryRestorePendingTask = async () => {
      const savedTaskId = localStorage.getItem('wiki_gen_task_id');
      const savedRepoUrl = localStorage.getItem('wiki_gen_repo_url');
      if (!savedTaskId) return;

      if (savedRepoUrl) setUrl(savedRepoUrl);
      try {
        const savedTaskStatus = await api.getTaskStatus(savedTaskId);
        const isPendingTask = savedTaskStatus.status === 'pending' || savedTaskStatus.status === 'processing';
        if (!isPendingTask) {
          clearTask();
          return;
        }
        setTaskId(savedTaskId);
        setStatus(savedTaskStatus);
        activePollTaskIdRef.current = savedTaskId;
        pollAttemptRef.current = 0;
        void pollStatus(savedTaskId);
      } catch {
        clearTask();
      }
    };
    void tryRestorePendingTask();

    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      activePollTaskIdRef.current = null;
      pollInFlightRef.current = false;
    };
  }, [pollStatus]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!url.trim() || !user) return;
    const normalizedInputUrl = url.trim();
    setIsSubmitting(true);
    setStatus(null);

    try {
      const result = await api.createTask(normalizedInputUrl, user.id);
      setTaskId(result.task_id);
      localStorage.setItem('wiki_gen_task_id', result.task_id);
      localStorage.setItem('wiki_gen_repo_url', normalizedInputUrl);
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      activePollTaskIdRef.current = result.task_id;
      pollAttemptRef.current = 0;
      void pollStatus(result.task_id);
    } catch {
      alert('Failed to start task. Please check the URL or API health.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const getStatusColor = (value: string) => {
    switch (value) {
      case 'completed':
        return 'text-emerald-800 bg-emerald-50 border-emerald-200';
      case 'cached':
        return 'text-sky-800 bg-sky-50 border-sky-200';
      case 'failed':
        return 'text-rose-800 bg-rose-50 border-rose-200';
      case 'processing':
        return 'text-amber-800 bg-amber-50 border-amber-200';
      default:
        return 'text-stone-700 bg-stone-100 border-stone-200';
    }
  };

  const isProcessing = taskId !== null && status?.status === 'processing';

  const handleAddRepo = () => {
    inputRef.current?.focus();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="max-w-7xl mx-auto px-4 lg:px-8 space-y-10 py-10">
      <div className="text-center space-y-4">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white border border-stone-200 text-xs text-stone-600 font-mono shadow-sm">
          <div className={cn('w-2 h-2 rounded-full', health ? 'bg-emerald-500 animate-pulse' : 'bg-red-500')} />
          API SYSTEM: {health ? 'ONLINE' : 'OFFLINE'}
        </div>
        <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-stone-900">
          {t('dashboardTitle')}
        </h1>
      </div>

      <div className="relative group max-w-3xl mx-auto w-full">
        <div className="absolute -inset-1 rounded-[2rem] bg-gradient-to-r from-sky-200/50 via-teal-100/40 to-sky-100/50 blur-2xl opacity-70 group-hover:opacity-90 transition-all duration-500" />
        {!taskId || status?.status === 'failed' ? (
          <form onSubmit={handleSubmit}>
            <div className="relative rounded-[2rem] border border-stone-200 bg-white p-5 md:p-7 shadow-xl shadow-stone-900/5">
              <div className="relative flex items-center gap-2 bg-stone-50 border border-stone-200 rounded-2xl p-2 transition-all focus-within:ring-2 focus-within:ring-sky-300 focus-within:border-sky-300">
                <div className="ml-2 p-2 rounded-xl bg-white border border-stone-200 shadow-sm">
                  <Link2 className="w-4 h-4 text-sky-600" />
                </div>
                <input
                  ref={inputRef}
                  type="url"
                  placeholder="Search for repositories (or paste a link)"
                  className="flex-1 bg-transparent border-none outline-none text-stone-900 px-3 py-3 placeholder:text-stone-400 font-mono text-sm"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={isSubmitting || isProcessing}
                />
                <button
                  type="submit"
                  disabled={isSubmitting || isProcessing || !url.trim()}
                  className="px-5 py-3 rounded-xl font-medium text-white bg-sky-600 hover:bg-sky-500 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 shadow-sm"
                  aria-label="Generate documentation"
                >
                  {isSubmitting ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <span className="flex items-center gap-2">
                      Generate <ArrowRight className="w-4 h-4" />
                    </span>
                  )}
                </button>
              </div>
            </div>
          </form>
        ) : (
          <div className="relative rounded-[2rem] border border-stone-200 bg-white p-8 shadow-xl shadow-stone-900/5">
            <div className="flex flex-col items-center gap-5 text-center">
              <div className="relative w-16 h-16 rounded-full border border-sky-200 bg-sky-50 flex items-center justify-center">
                <Loader2 className="w-8 h-8 text-sky-600 animate-spin" />
              </div>
              <div className="space-y-1">
                <p className="text-stone-900 text-lg font-semibold">Loading into Wiki Engine...</p>
                <p className="text-stone-600 text-sm">{status?.current_step || 'Preparing repository analysis pipeline...'}</p>
              </div>
              <button
                type="button"
                onClick={clearTask}
                className="text-sm text-rose-700 hover:text-rose-800 transition-colors inline-flex items-center gap-2"
              >
                <RotateCcw className="w-3 h-3" />
                Cancel & Start New
              </button>
            </div>
          </div>
        )}
      </div>

      {user && (
        <RepoGrid userId={user.id} onAddRepo={handleAddRepo} />
      )}

      {status && (
        <div className="bg-white border border-stone-200 rounded-3xl p-6 md:p-8 space-y-6 max-w-3xl mx-auto shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-stone-50 rounded-lg border border-stone-200">
                <Terminal className="w-6 h-6 text-sky-600" />
              </div>
              <div>
                <div className="text-sm text-stone-500 font-mono uppercase tracking-wider">Task ID</div>
                <div className="font-mono text-sm md:text-base text-stone-800 break-all">{status.task_id}</div>
              </div>
            </div>
            <div className={cn('px-3 py-1 rounded-full text-xs font-medium border uppercase tracking-wider', getStatusColor(status.status))}>
              {status.status}
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-stone-600">{status.current_step}</span>
              <span className="text-stone-900 font-mono">{Math.round(status.progress)}%</span>
            </div>
            <div className="h-2.5 rounded-full bg-stone-100 border border-stone-200 overflow-hidden">
              <div className="h-full bg-gradient-to-r from-sky-500 to-teal-500" style={{ width: `${Math.max(4, status.progress)}%` }} />
            </div>
            <div className="text-xs text-stone-500 inline-flex items-center gap-1">
              <ShieldCheck className="w-3 h-3" />
              When the task completes you will be redirected to `/app/wiki/:taskId`
            </div>
          </div>
          {status.status === 'failed' && (
            <div className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-rose-900 text-sm">
              Error: {status.error || 'Unknown error occurred'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
