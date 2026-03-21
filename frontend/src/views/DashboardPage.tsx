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
        return 'text-emerald-300 bg-emerald-400/10 border-emerald-300/25';
      case 'cached':
        return 'text-cyan-300 bg-cyan-400/10 border-cyan-300/25';
      case 'failed':
        return 'text-rose-300 bg-rose-400/10 border-rose-300/25';
      case 'processing':
        return 'text-fuchsia-300 bg-fuchsia-400/10 border-fuchsia-300/25';
      default:
        return 'text-amber-300 bg-amber-400/10 border-amber-300/25';
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
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/[0.03] border border-white/15 text-xs text-zinc-300 font-mono">
          <div className={cn('w-2 h-2 rounded-full', health ? 'bg-green-500 animate-pulse' : 'bg-red-500')} />
          API SYSTEM: {health ? 'ONLINE' : 'OFFLINE'}
        </div>
        <h1 className="text-3xl md:text-4xl font-semibold tracking-tight text-white">
          {t('dashboardTitle')}
        </h1>
      </div>

      <div className="relative group max-w-3xl mx-auto w-full">
        <div className="absolute -inset-1 bg-gradient-to-r from-fuchsia-500/30 via-violet-500/25 to-cyan-500/30 rounded-[2rem] blur-2xl opacity-80 group-hover:opacity-100 transition-all duration-500" />
        {!taskId || status?.status === 'failed' ? (
          <form onSubmit={handleSubmit}>
            <div className="relative rounded-[2rem] border border-white/15 bg-[linear-gradient(155deg,rgba(18,12,36,0.95),rgba(9,8,20,0.92))] backdrop-blur-2xl p-5 md:p-7 shadow-[0_30px_90px_rgba(76,29,149,0.35)]">
              <div className="relative flex items-center gap-2 bg-black/35 border border-white/15 rounded-2xl p-2 transition-all focus-within:ring-2 focus-within:ring-fuchsia-400/40">
                <div className="ml-2 p-2 rounded-xl bg-white/5 border border-white/10">
                  <Link2 className="w-4 h-4 text-fuchsia-200" />
                </div>
                <input
                  ref={inputRef}
                  type="url"
                  placeholder="Search for repositories (or paste a link)"
                  className="flex-1 bg-transparent border-none outline-none text-white px-3 py-3 placeholder:text-zinc-500 font-mono text-sm"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={isSubmitting || isProcessing}
                />
                <button
                  type="submit"
                  disabled={isSubmitting || isProcessing || !url.trim()}
                  className="px-5 py-3 rounded-xl font-medium text-white bg-gradient-to-r from-fuchsia-500 via-violet-500 to-indigo-500 hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
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
          <div className="relative rounded-[2rem] border border-white/15 bg-[linear-gradient(155deg,rgba(18,12,36,0.95),rgba(9,8,20,0.92))] backdrop-blur-2xl p-8 shadow-[0_30px_90px_rgba(76,29,149,0.35)]">
            <div className="flex flex-col items-center gap-5 text-center">
              <div className="relative w-16 h-16 rounded-full border border-fuchsia-300/40 bg-fuchsia-500/10 flex items-center justify-center">
                <Loader2 className="w-8 h-8 text-fuchsia-300 animate-spin" />
              </div>
              <div className="space-y-1">
                <p className="text-zinc-100 text-lg font-semibold">Loading into Wiki Engine...</p>
                <p className="text-zinc-400 text-sm">{status?.current_step || 'Preparing repository analysis pipeline...'}</p>
              </div>
              <button
                type="button"
                onClick={clearTask}
                className="text-sm text-rose-300 hover:text-rose-200 transition-colors inline-flex items-center gap-2"
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
        <div className="bg-[linear-gradient(150deg,rgba(21,14,42,0.72),rgba(10,10,24,0.72))] backdrop-blur-xl border border-white/15 rounded-3xl p-6 md:p-8 space-y-6 max-w-3xl mx-auto">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="p-3 bg-white/5 rounded-lg border border-white/10">
                <Terminal className="w-6 h-6 text-fuchsia-300" />
              </div>
              <div>
                <div className="text-sm text-zinc-400 font-mono uppercase tracking-wider">Task ID</div>
                <div className="font-mono text-sm md:text-base text-white/80 break-all">{status.task_id}</div>
              </div>
            </div>
            <div className={cn('px-3 py-1 rounded-full text-xs font-medium border uppercase tracking-wider', getStatusColor(status.status))}>
              {status.status}
            </div>
          </div>
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-zinc-300">{status.current_step}</span>
              <span className="text-white font-mono">{Math.round(status.progress)}%</span>
            </div>
            <div className="h-2.5 rounded-full bg-black/45 border border-white/10 overflow-hidden">
              <div className="h-full bg-gradient-to-r from-fuchsia-500 via-violet-400 to-cyan-400" style={{ width: `${Math.max(4, status.progress)}%` }} />
            </div>
            <div className="text-xs text-zinc-400 inline-flex items-center gap-1">
              <ShieldCheck className="w-3 h-3" />
              When the task completes you will be redirected to `/app/wiki/:taskId`
            </div>
          </div>
          {status.status === 'failed' && (
            <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-200 text-sm">
              Error: {status.error || 'Unknown error occurred'}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
