'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { User } from '@supabase/supabase-js';
import { api, TaskStatusResponse } from '@/lib/api';
import { supabase } from '@/lib/supabase';
import WikiViewer from '@/components/WikiViewer';
import Auth from '@/components/Auth';
import {
  Github,
  ArrowRight,
  Loader2,
  Terminal,
  RotateCcw,
  History,
  LogOut,
  Clock,
  ChevronRight,
  Sparkles,
  Link2,
  ShieldCheck,
} from 'lucide-react';
import { cn } from '@/lib/utils';

type HistoryItem = TaskStatusResponse;

interface UserProfile {
  id: string;
  email?: string;
  phone?: string;
}

export default function Home() {
  const [user, setUser] = useState<User | null>(null);
  const [url, setUrl] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [health, setHealth] = useState<boolean | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);
  const activePollTaskIdRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  const pollAttemptRef = useRef(0);

  const buildUserProfile = useCallback((supabaseUser: User | null): UserProfile | null => {
    if (!supabaseUser) return null;
    return {
      id: supabaseUser.id,
      email: supabaseUser.email,
      phone: supabaseUser.phone,
    };
  }, []);

  const loadHistory = useCallback(async (userId: string) => {
    try {
      const { tasks } = await api.getTasks(userId);
      setHistory(tasks);
    } catch {
      setHistory([]);
    }
  }, []);

  const getPollDelayMs = useCallback((attempt: number) => {
    if (typeof document !== 'undefined' && document.hidden) return 10000;
    if (attempt <= 1) return 2500;
    if (attempt <= 3) return 4000;
    return 6000;
  }, []);

  const pollStatus = useCallback(async (id: string) => {
    if (activePollTaskIdRef.current !== id) return;
    if (pollInFlightRef.current) return;
    pollInFlightRef.current = true;

    try {
      const res = await api.getTaskStatus(id);
      setStatus(res);
      pollAttemptRef.current = 0;

      if (res.status === 'completed' || res.status === 'cached' || res.status === 'failed') {
        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
        activePollTaskIdRef.current = null;
        pollInFlightRef.current = false;
        if (user) {
          await loadHistory(user.id);
        }
        return;
      }

      pollInFlightRef.current = false;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = setTimeout(() => {
        void pollStatus(id);
      }, getPollDelayMs(pollAttemptRef.current));
    } catch (error: unknown) {
      console.error('Polling failed:', error);
      pollInFlightRef.current = false;
      pollAttemptRef.current += 1;
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      pollTimerRef.current = setTimeout(() => {
        void pollStatus(id);
      }, getPollDelayMs(pollAttemptRef.current));
    }
  }, [getPollDelayMs, loadHistory, user]);

  useEffect(() => {
    const init = async () => {
      const isOk = await api.checkHealth();
      setHealth(isOk);

      const {
        data: { session },
      } = await supabase.auth.getSession();
      const currentUser = session?.user ?? null;
      setUser(currentUser);
      setProfile(buildUserProfile(currentUser));
      if (currentUser) {
        loadHistory(currentUser.id);
      }
    };

    init();

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      const nextUser = nextSession?.user ?? null;
      setUser(nextUser);
      setProfile(buildUserProfile(nextUser));
      if (nextUser) {
        loadHistory(nextUser.id);
        return;
      }
      setHistory([]);
      setShowHistory(false);
      setTaskId(null);
      setStatus(null);
    });

    return () => {
      subscription.unsubscribe();
    };
  }, [buildUserProfile, loadHistory]);

  useEffect(() => {
    const savedTaskId = localStorage.getItem('wiki_gen_task_id');
    if (!savedTaskId) return;
    setTaskId(savedTaskId);
    activePollTaskIdRef.current = savedTaskId;
    pollAttemptRef.current = 0;
    void pollStatus(savedTaskId);
  }, [pollStatus]);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      activePollTaskIdRef.current = null;
      pollInFlightRef.current = false;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || !user) return;

    const normalizedInputUrl = url.trim();
    setIsSubmitting(true);
    setStatus(null);
    try {
      const res = await api.createTask(normalizedInputUrl, user.id);
      setTaskId(res.task_id);
      localStorage.setItem('wiki_gen_task_id', res.task_id);
      localStorage.setItem('wiki_gen_repo_url', normalizedInputUrl);
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      activePollTaskIdRef.current = res.task_id;
      pollAttemptRef.current = 0;
      void pollStatus(res.task_id);
    } catch (error) {
      console.error('Submission failed:', error);
      alert('Failed to start task. Please check the URL or API health.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClear = () => {
    setTaskId(null);
    setStatus(null);
    setUrl('');
    localStorage.removeItem('wiki_gen_task_id');
    localStorage.removeItem('wiki_gen_repo_url');
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    activePollTaskIdRef.current = null;
    pollInFlightRef.current = false;
    pollAttemptRef.current = 0;
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
    localStorage.removeItem('wiki_gen_task_id');
    localStorage.removeItem('wiki_gen_repo_url');
  };

  const handleSelectHistoryTask = (task: HistoryItem) => {
    if ((task.status !== 'completed' && task.status !== 'cached') || !task.result) {
      alert('This task did not complete successfully.');
      return;
    }

    setStatus({
      ...task,
      progress: 100,
      current_step: 'Loaded from history',
    });
    setTaskId(task.task_id);
    setShowHistory(false);
  };

  const getStatusColor = (s: string) => {
    switch (s) {
      case 'completed': return 'text-emerald-300 bg-emerald-400/10 border-emerald-300/25';
      case 'cached': return 'text-cyan-300 bg-cyan-400/10 border-cyan-300/25';
      case 'failed': return 'text-rose-300 bg-rose-400/10 border-rose-300/25';
      case 'processing': return 'text-fuchsia-300 bg-fuchsia-400/10 border-fuchsia-300/25';
      default: return 'text-amber-300 bg-amber-400/10 border-amber-300/25';
    }
  };

  const isCompleted = (status?.status === 'completed' || status?.status === 'cached') && status?.result?.r2_structure_url && status?.result?.r2_content_urls;
  const isProcessing = taskId !== null && status?.status === 'processing';

  if (!user) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden font-sans bg-[#05030d] text-white">
        <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_18%_16%,rgba(217,70,239,0.18),transparent_32%),radial-gradient(circle_at_84%_12%,rgba(124,58,237,0.22),transparent_30%),radial-gradient(circle_at_70%_86%,rgba(6,182,212,0.16),transparent_36%)]" />
          <div className="absolute inset-0 bg-[linear-gradient(120deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0)_22%,rgba(255,255,255,0.03)_46%,rgba(255,255,255,0)_65%,rgba(255,255,255,0.02)_100%)]" />
        </div>
        <div className="z-10 w-full max-w-3xl p-4 space-y-8">
          <div className="text-center space-y-4">
            <h1 className="text-4xl md:text-6xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-fuchsia-200 to-cyan-200">
              Project Wiki Generator
            </h1>
            <p className="text-lg text-zinc-300 max-w-xl mx-auto">
              Please login to start generating and tracking your project documentation.
            </p>
          </div>
          <div className="rounded-3xl border border-white/10 bg-[linear-gradient(145deg,rgba(18,12,36,0.9),rgba(9,9,20,0.88))] backdrop-blur-2xl p-4 md:p-6 shadow-[0_20px_80px_rgba(124,58,237,0.22)]">
            <Auth />
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden font-sans bg-[#05030d] text-white">
      {/* Background Elements */}
      <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_14%_18%,rgba(217,70,239,0.2),transparent_28%),radial-gradient(circle_at_85%_14%,rgba(99,102,241,0.24),transparent_34%),radial-gradient(circle_at_72%_82%,rgba(6,182,212,0.18),transparent_35%)]" />
        <div className="absolute inset-0 bg-[linear-gradient(130deg,rgba(255,255,255,0.02)_0%,rgba(255,255,255,0)_25%,rgba(255,255,255,0.04)_53%,rgba(255,255,255,0)_78%,rgba(255,255,255,0.02)_100%)]" />
      </div>

      <div className={cn(
        'z-10 w-full transition-all duration-700 ease-in-out',
        isCompleted ? 'max-w-[1600px] h-screen flex flex-col p-4 md:p-8' : 'max-w-4xl p-4 sm:p-16 space-y-10',
      )}>
        {/* Header */}
        <motion.div
          layout
          className={cn('text-center space-y-4 relative', isCompleted ? 'flex items-center justify-between space-y-0 mb-6' : '')}
        >
          {/* User Menu */}
          {!isCompleted && (
            <div className="absolute top-[-4rem] right-0 flex items-center gap-4">
              {profile && (
                <div className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-zinc-200 bg-white/[0.03] rounded-full border border-white/15 backdrop-blur-xl">
                  <div className="w-2 h-2 rounded-full bg-fuchsia-400" />
                  {profile.email || profile.phone}
                </div>
              )}
              <button
                onClick={() => setShowHistory(!showHistory)}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:text-white bg-white/[0.03] hover:bg-white/[0.08] rounded-full transition-all border border-white/15 backdrop-blur-xl"
                aria-label="Show task history"
              >
                <History className="w-3.5 h-3.5" />
                History
              </button>
              <button
                onClick={handleLogout}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-rose-300/90 hover:text-rose-200 bg-rose-500/10 hover:bg-rose-500/20 rounded-full transition-all border border-rose-300/20 backdrop-blur-xl"
                aria-label="Logout"
              >
                <LogOut className="w-3.5 h-3.5" />
                Logout
              </button>
            </div>
          )}

          {isCompleted ? (
            <>
              <div className="flex items-center gap-4">
                <h1 className="text-2xl font-bold tracking-tight text-white">Project Wiki</h1>
                <div className="hidden md:flex items-center gap-2 px-3 py-1 rounded-full bg-white/[0.03] border border-white/15 text-xs text-zinc-300 font-mono">
                  <div className="w-2 h-2 rounded-full bg-emerald-400" />
                  Generated
                </div>
              </div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setShowHistory(!showHistory)}
                  className="flex items-center gap-2 px-4 py-2 text-sm text-zinc-300 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                  aria-label="Toggle task history"
                >
                  <History className="w-4 h-4" />
                  History
                </button>
                <button
                  onClick={handleClear}
                  className="flex items-center gap-2 px-4 py-2 text-sm text-zinc-300 hover:text-white hover:bg-white/10 rounded-lg transition-colors"
                  aria-label="Start new project"
                >
                  <RotateCcw className="w-4 h-4" />
                  New Project
                </button>
                <button
                  onClick={handleLogout}
                  className="flex items-center gap-2 px-4 py-2 text-sm text-rose-300/90 hover:text-rose-200 hover:bg-rose-500/10 rounded-lg transition-colors"
                  aria-label="Logout"
                >
                  <LogOut className="w-4 h-4" />
                  Logout
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/[0.03] border border-white/15 text-xs text-zinc-300 font-mono mb-4">
                <div className={cn('w-2 h-2 rounded-full', health ? 'bg-green-500 animate-pulse' : 'bg-red-500')} />
                API SYSTEM: {health ? 'ONLINE' : 'OFFLINE'}
              </div>
              <h1 className="text-4xl md:text-6xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-fuchsia-200 to-cyan-200">
                Project Wiki Generator
              </h1>
              <p className="text-lg text-zinc-300 max-w-xl mx-auto">
                Transform your GitHub repository into comprehensive documentation instantly using AI.
              </p>
            </>
          )}
        </motion.div>

        <AnimatePresence mode="wait">
          {showHistory ? (
            <motion.div
              key="history-section"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-6"
            >
              <div className="flex items-center justify-between">
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <History className="w-5 h-5 text-fuchsia-300" />
                  Task History
                </h2>
                <button
                  onClick={() => setShowHistory(false)}
                  className="text-sm text-zinc-400 hover:text-white"
                >
                  Close History
                </button>
              </div>

              <div className="grid gap-4 max-h-[60vh] overflow-y-auto pr-2 custom-scrollbar">
                {history.length === 0 ? (
                  <div className="text-center py-12 bg-white/[0.03] rounded-2xl border border-dashed border-white/15 backdrop-blur-xl">
                    <Clock className="w-12 h-12 text-zinc-500 mx-auto mb-4 opacity-40" />
                    <p className="text-zinc-400">No history yet. Start by generating a wiki!</p>
                  </div>
                ) : (
                  history.map((item) => (
                    <div
                      key={item.id}
                      onClick={() => (item.status === 'completed' || item.status === 'cached') && handleSelectHistoryTask(item)}
                      className={cn(
                        'group p-4 bg-white/[0.03] border border-white/15 rounded-xl transition-all backdrop-blur-xl',
                        item.status === 'completed' || item.status === 'cached'
                          ? 'hover:bg-white/[0.08] cursor-pointer hover:border-fuchsia-400/40'
                          : 'opacity-80',
                      )}
                    >
                      <div className="flex items-start justify-between">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <Github className="w-4 h-4 text-zinc-400" />
                            <span className="font-mono text-sm text-white/90 truncate max-w-[200px] md:max-w-md">
                              {item.repo_url}
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-xs text-zinc-400">
                            <span>{new Date(item.created_at).toLocaleDateString()}</span>
                            <span>•</span>
                            <span className={cn('capitalize', getStatusColor(item.status).split(' ')[0])}>
                              {item.status}
                            </span>
                          </div>
                        </div>
                        {(item.status === 'completed' || item.status === 'cached') && (
                          <ChevronRight className="w-5 h-5 text-zinc-400 group-hover:text-white transition-colors" />
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </motion.div>
          ) : !isCompleted && (
            <motion.div
              key="input-section"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.5 }}
              className="space-y-12"
            >
              {/* Input Form */}
              <div className="relative group max-w-3xl mx-auto">
                <div className="absolute -inset-1 bg-gradient-to-r from-fuchsia-500/30 via-violet-500/25 to-cyan-500/30 rounded-[2rem] blur-2xl opacity-80 group-hover:opacity-100 transition-all duration-500" />
                {!taskId || status?.status === 'failed' ? (
                  <form onSubmit={handleSubmit}>
                    <div className="relative rounded-[2rem] border border-white/15 bg-[linear-gradient(155deg,rgba(18,12,36,0.95),rgba(9,8,20,0.92))] backdrop-blur-2xl p-5 md:p-7 shadow-[0_30px_90px_rgba(76,29,149,0.35)]">
                      <div className="inline-flex items-center gap-2 rounded-full border border-fuchsia-300/25 bg-fuchsia-400/10 px-3 py-1.5 text-xs tracking-wide text-fuchsia-100 mb-4">
                        <Sparkles className="w-3.5 h-3.5" />
                        GITHUB URL WORKFLOW
                      </div>
                      <div className="space-y-3">
                        <p className="text-zinc-200 text-base md:text-lg font-medium">输入你的仓库地址，立即生成结构化项目 Wiki</p>
                        <p className="text-zinc-400 text-sm">支持公开 GitHub 仓库。系统会自动索引并生成文档与可检索内容。</p>
                      </div>
                      <div className="mt-5 relative flex items-center gap-2 bg-black/35 border border-white/15 rounded-2xl p-2 transition-all focus-within:ring-2 focus-within:ring-fuchsia-400/40 focus-within:border-fuchsia-300/40">
                        <div className="ml-2 p-2 rounded-xl bg-white/5 border border-white/10">
                          <Link2 className="w-4 h-4 text-fuchsia-200" />
                        </div>
                        <input
                          type="url"
                          placeholder="https://github.com/username/repository"
                          className="flex-1 bg-transparent border-none outline-none text-white px-3 py-3 placeholder:text-zinc-500 font-mono text-sm"
                          value={url}
                          onChange={(e) => setUrl(e.target.value)}
                          disabled={isSubmitting || isProcessing}
                        />
                        <button
                          type="submit"
                          disabled={isSubmitting || isProcessing || !url.trim()}
                          className="px-5 py-3 rounded-xl font-medium text-white bg-gradient-to-r from-fuchsia-500 via-violet-500 to-indigo-500 hover:brightness-110 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 shadow-[0_8px_30px_rgba(168,85,247,0.45)]"
                          aria-label="Generate documentation"
                        >
                          {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <span className="flex items-center gap-2">Generate <ArrowRight className="w-4 h-4" /></span>}
                        </button>
                      </div>
                    </div>
                  </form>
                ) : (
                  <div className="relative rounded-[2rem] border border-white/15 bg-[linear-gradient(155deg,rgba(18,12,36,0.95),rgba(9,8,20,0.92))] backdrop-blur-2xl p-8 shadow-[0_30px_90px_rgba(76,29,149,0.35)]">
                    <div className="flex flex-col items-center gap-5 text-center">
                      <div className="relative">
                        <div className="absolute inset-0 rounded-full bg-fuchsia-500/40 blur-2xl" />
                        <div className="relative w-16 h-16 rounded-full border border-fuchsia-300/40 bg-fuchsia-500/10 flex items-center justify-center">
                          <Loader2 className="w-8 h-8 text-fuchsia-300 animate-spin" />
                        </div>
                      </div>
                      <div className="space-y-1">
                        <p className="text-zinc-100 text-lg font-semibold">Loading into Wiki Engine...</p>
                        <p className="text-zinc-400 text-sm">{status?.current_step || 'Preparing repository analysis pipeline...'}</p>
                      </div>
                      <div className="w-full max-w-xl space-y-2">
                        <div className="flex items-center justify-between text-xs text-zinc-400 font-mono">
                          <span className="inline-flex items-center gap-1"><ShieldCheck className="w-3 h-3" /> Processing</span>
                          <span>{Math.round(status?.progress ?? 0)}%</span>
                        </div>
                        <div className="h-2.5 rounded-full bg-black/45 border border-white/10 overflow-hidden">
                          {status ? (
                            <motion.div
                              className="h-full bg-gradient-to-r from-fuchsia-500 via-violet-400 to-cyan-400"
                              initial={{ width: 0 }}
                              animate={{ width: `${Math.max(4, status.progress)}%` }}
                              transition={{ duration: 0.5 }}
                            />
                          ) : (
                            <div className="h-full w-1/3 bg-gradient-to-r from-fuchsia-500 via-violet-400 to-cyan-400 animate-pulse" />
                          )}
                        </div>
                      </div>
                      <button
                        onClick={handleClear}
                        className="mt-2 text-sm text-rose-300 hover:text-rose-200 transition-colors flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-rose-500/10"
                        aria-label="Cancel task and start new"
                      >
                        <RotateCcw className="w-3 h-3" />
                        Cancel & Start New
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Status Card */}
              {status && (
                <motion.div
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  className="bg-[linear-gradient(150deg,rgba(21,14,42,0.72),rgba(10,10,24,0.72))] backdrop-blur-xl border border-white/15 rounded-3xl p-6 md:p-8 space-y-6 shadow-[0_20px_60px_rgba(59,130,246,0.15)]"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="p-3 bg-white/5 rounded-lg border border-white/10">
                        <Terminal className="w-6 h-6 text-fuchsia-300" />
                      </div>
                      <div>
                        <div className="text-sm text-zinc-400 font-mono uppercase tracking-wider">Task ID</div>
                        <div className="font-mono text-sm md:text-base text-white/80 truncate max-w-[200px] md:max-w-md">{status.task_id}</div>
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
                    <div className="h-2 w-full bg-black/45 rounded-full overflow-hidden border border-white/10">
                      <motion.div
                        className="h-full bg-gradient-to-r from-fuchsia-500 via-violet-500 to-cyan-400"
                        initial={{ width: 0 }}
                        animate={{ width: `${status.progress}%` }}
                        transition={{ duration: 0.5 }}
                      />
                    </div>
                  </div>

                  {status.status === 'failed' && (
                    <div className="p-4 rounded-xl bg-red-500/10 border border-red-500/20 text-red-200 text-sm">
                      Error: {status.error || 'Unknown error occurred'}
                    </div>
                  )}

                  {status.status === 'failed' && (
                    <div className="flex justify-center pt-4">
                      <button
                        onClick={handleClear}
                        className="text-zinc-300 hover:text-white text-sm transition-colors flex items-center gap-2"
                        aria-label="Start new task"
                      >
                        Start New Task
                      </button>
                    </div>
                  )}
                </motion.div>
              )}
            </motion.div>
          )}

          {isCompleted && !showHistory && (
            <motion.div
              key="wiki-viewer"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
              className="flex-1 min-h-0"
            >
              <WikiViewer
                userId={user.id}
                structureUrl={status.result!.r2_structure_url!}
                contentUrls={status.result!.r2_content_urls!}
                repoUrl={status.result?.repo_url || localStorage.getItem('wiki_gen_repo_url') || url}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </main>
  );
}
