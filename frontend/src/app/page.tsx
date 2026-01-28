'use client';

import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { api, TaskStatusResponse } from '@/lib/api';
import WikiViewer from '@/components/WikiViewer';
import { supabase, supabaseApi } from '@/lib/supabase';
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
  ExternalLink,
  ChevronRight
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import { Session } from '@supabase/supabase-js';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export default function Home() {
  const [session, setSession] = useState<Session | null>(null);
  const [url, setUrl] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [health, setHealth] = useState<boolean | null>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [profile, setProfile] = useState<any>(null);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Check health and session on mount
  useEffect(() => {
    const init = async () => {
      const isOk = await api.checkHealth();
      setHealth(isOk);

      const { data: { session } } = await supabase.auth.getSession();
      setSession(session);

      if (session) {
        loadHistory(session.user.id);
        loadProfile(session.user.id);
      }
    };
    init();

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
      if (session) {
        loadHistory(session.user.id);
        loadProfile(session.user.id);
      } else {
        setProfile(null);
        setHistory([]);
      }
    });

    return () => subscription.unsubscribe();
  }, []);

  const loadProfile = async (userId: string) => {
    const { data, error } = await supabaseApi.getProfile(userId);
    if (!error && data) {
      setProfile(data);
    }
  };

  const loadHistory = async (userId: string) => {
    const { data, error } = await supabaseApi.getTaskHistory(userId);
    if (!error && data) {
      setHistory(data);
    }
  };

  useEffect(() => {
    // Restore task from localStorage if exists
    const savedTaskId = localStorage.getItem('wiki_gen_task_id');
    if (savedTaskId) {
      setTaskId(savedTaskId);
      pollStatus(savedTaskId);
    }
  }, []);

  // Polling logic
  const pollStatus = async (id: string) => {
    try {
      const res = await api.getTaskStatus(id);
      setStatus(res);

      if (res.status === 'completed' || res.status === 'failed') {
        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
        
        // Sync final result to Supabase
        if (session) {
          await supabaseApi.syncTaskEnd(id, res.status, res.result);
          if (res.status === 'completed' && res.result?.r2_structure_url && res.result?.r2_content_urls) {
            // Also sync to repositories table
            const repoUrl = localStorage.getItem('wiki_gen_repo_url') || '';
            if (repoUrl) {
              await supabaseApi.syncRepository(repoUrl, res.result.r2_structure_url, res.result.r2_content_urls);
            }
          }
          loadHistory(session.user.id);
        }
      } else {
        pollTimerRef.current = setTimeout(() => pollStatus(id), 2000);
      }
    } catch (error: any) {
      console.error('Polling failed:', error);
      pollTimerRef.current = setTimeout(() => pollStatus(id), 5000);
    }
  };

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim() || !session) return;

    setIsSubmitting(true);
    setStatus(null);
    try {
      const res = await api.createTask(url);
      setTaskId(res.task_id);
      localStorage.setItem('wiki_gen_task_id', res.task_id);
      localStorage.setItem('wiki_gen_repo_url', url);
      
      // Initial sync to Supabase
      await supabaseApi.syncTaskStart(session.user.id, res.task_id, url);
      
      pollStatus(res.task_id);
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
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
  };

  const handleSelectHistoryTask = (task: any) => {
    if (task.status === 'completed' && task.result) {
      setStatus({
        task_id: task.task_id,
        status: 'completed',
        progress: 100,
        current_step: 'Loaded from history',
        created_at: task.created_at,
        updated_at: task.created_at,
        result: task.result,
        error: null
      });
      setTaskId(task.task_id);
      setShowHistory(false);
    } else {
      alert('This task did not complete successfully.');
    }
  };

  const getStatusColor = (s: string) => {
    switch (s) {
      case 'completed': return 'text-green-500 bg-green-500/10 border-green-500/20';
      case 'failed': return 'text-red-500 bg-red-500/10 border-red-500/20';
      case 'processing': return 'text-blue-500 bg-blue-500/10 border-blue-500/20';
      default: return 'text-yellow-500 bg-yellow-500/10 border-yellow-500/20';
    }
  };

  const isCompleted = status?.status === 'completed' && status?.result?.r2_structure_url && status?.result?.r2_content_urls;

  if (!session) {
    return (
      <main className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden font-sans bg-[#020617]">
        <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none z-0">
          <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-blue-600/10 blur-[100px] rounded-full" />
          <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-cyan-600/10 blur-[100px] rounded-full" />
        </div>
        <div className="z-10 w-full max-w-3xl p-4 space-y-8">
          <div className="text-center space-y-4">
            <h1 className="text-4xl md:text-6xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-white/60">
              Project Wiki Generator
            </h1>
            <p className="text-lg text-muted-foreground max-w-xl mx-auto">
              Please login to start generating and tracking your project documentation.
            </p>
          </div>
          <Auth />
        </div>
      </main>
    );
  }
  return (
    <main className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden font-sans">
      {/* Background Elements */}
      <div className="absolute top-0 left-0 w-full h-full overflow-hidden pointer-events-none z-0">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-blue-600/10 blur-[100px] rounded-full" />
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-cyan-600/10 blur-[100px] rounded-full" />
      </div>

      <div className={cn(
        "z-10 w-full transition-all duration-700 ease-in-out",
        isCompleted ? "max-w-[1600px] h-screen flex flex-col p-4 md:p-8" : "max-w-3xl p-4 sm:p-24 space-y-12"
      )}>
        {/* Header */}
        <motion.div 
          layout
          className={cn("text-center space-y-4 relative", isCompleted ? "flex items-center justify-between space-y-0 mb-6" : "")}
        >
          {/* User Menu */}
          {!isCompleted && (
            <div className="absolute top-[-4rem] right-0 flex items-center gap-4">
              {profile && (
                <div className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-white/80 bg-white/5 rounded-full border border-white/10">
                  <div className="w-2 h-2 rounded-full bg-blue-500" />
                  {profile.email || profile.phone}
                </div>
              )}
              <button 
                onClick={() => setShowHistory(!showHistory)}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground hover:text-white bg-white/5 hover:bg-white/10 rounded-full transition-all border border-white/10"
              >
                <History className="w-3.5 h-3.5" />
                History
              </button>
              <button 
                onClick={handleLogout}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-red-400/80 hover:text-red-400 bg-red-400/5 hover:bg-red-400/10 rounded-full transition-all border border-red-400/10"
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
                 <div className="hidden md:flex items-center gap-2 px-3 py-1 rounded-full bg-secondary border border-border text-xs text-muted-foreground font-mono">
                    <div className="w-2 h-2 rounded-full bg-green-500" />
                    Generated
                 </div>
               </div>
               <div className="flex items-center gap-3">
                 <button 
                    onClick={() => setShowHistory(!showHistory)}
                    className="flex items-center gap-2 px-4 py-2 text-sm text-muted-foreground hover:text-white hover:bg-white/5 rounded-lg transition-colors"
                 >
                   <History className="w-4 h-4" />
                   History
                 </button>
                 <button 
                    onClick={handleClear}
                    className="flex items-center gap-2 px-4 py-2 text-sm text-muted-foreground hover:text-white hover:bg-white/5 rounded-lg transition-colors"
                 >
                   <RotateCcw className="w-4 h-4" />
                   New Project
                 </button>
                 <button 
                    onClick={handleLogout}
                    className="flex items-center gap-2 px-4 py-2 text-sm text-red-400/80 hover:text-red-400 hover:bg-red-400/5 rounded-lg transition-colors"
                 >
                   <LogOut className="w-4 h-4" />
                   Logout
                 </button>
               </div>
             </>
          ) : (
             <>
                <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary border border-border text-xs text-muted-foreground font-mono mb-4">
                  <div className={cn("w-2 h-2 rounded-full", health ? "bg-green-500 animate-pulse" : "bg-red-500")} />
                  API SYSTEM: {health ? 'ONLINE' : 'OFFLINE'}
                </div>
                <h1 className="text-4xl md:text-6xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-white/60">
                  Project Wiki Generator
                </h1>
                <p className="text-lg text-muted-foreground max-w-xl mx-auto">
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
                  <History className="w-5 h-5 text-blue-400" />
                  Task History
                </h2>
                <button 
                  onClick={() => setShowHistory(false)}
                  className="text-sm text-muted-foreground hover:text-white"
                >
                  Close History
                </button>
              </div>

              <div className="grid gap-4 max-h-[60vh] overflow-y-auto pr-2 custom-scrollbar">
                {history.length === 0 ? (
                  <div className="text-center py-12 bg-secondary/20 rounded-2xl border border-dashed border-white/10">
                    <Clock className="w-12 h-12 text-muted-foreground mx-auto mb-4 opacity-20" />
                    <p className="text-muted-foreground">No history yet. Start by generating a wiki!</p>
                  </div>
                ) : (
                  history.map((item) => (
                    <div 
                      key={item.id}
                      onClick={() => item.status === 'completed' && handleSelectHistoryTask(item)}
                      className={cn(
                        "group p-4 bg-secondary/40 border border-white/10 rounded-xl transition-all",
                        item.status === 'completed' ? "hover:bg-secondary/60 cursor-pointer hover:border-blue-500/30" : "opacity-80"
                      )}
                    >
                      <div className="flex items-start justify-between">
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <Github className="w-4 h-4 text-muted-foreground" />
                            <span className="font-mono text-sm text-white/90 truncate max-w-[200px] md:max-w-md">
                              {item.repo_url}
                            </span>
                          </div>
                          <div className="flex items-center gap-3 text-xs text-muted-foreground">
                            <span>{new Date(item.created_at).toLocaleDateString()}</span>
                            <span>•</span>
                            <span className={cn("capitalize", getStatusColor(item.status).split(' ')[0])}>
                              {item.status}
                            </span>
                          </div>
                        </div>
                        {item.status === 'completed' && (
                          <ChevronRight className="w-5 h-5 text-muted-foreground group-hover:text-white transition-colors" />
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
              <div className="relative group">
                 {!taskId || (status?.status === 'failed') ? (
                  <form onSubmit={handleSubmit}>
                    <div className="absolute inset-0 bg-gradient-to-r from-blue-500 to-cyan-500 rounded-xl blur opacity-20 group-hover:opacity-30 transition duration-500" />
                    <div className="relative flex items-center bg-secondary/50 backdrop-blur-xl border border-white/10 rounded-xl p-2 transition-all focus-within:ring-1 focus-within:ring-blue-500/50">
                      <Github className="w-6 h-6 ml-3 text-muted-foreground" />
                      <input
                        type="url"
                        placeholder="https://github.com/username/repository"
                        className="flex-1 bg-transparent border-none outline-none text-white px-4 py-3 placeholder:text-white/20 font-mono text-sm"
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        disabled={isSubmitting || (taskId !== null && status?.status === 'processing')}
                      />
                      <button
                        type="submit"
                        disabled={isSubmitting || (taskId !== null && status?.status === 'processing') || !url.trim()}
                        className="bg-white text-black px-6 py-3 rounded-lg font-medium hover:bg-gray-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                      >
                        {isSubmitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <span className="flex items-center gap-2">Generate <ArrowRight className="w-4 h-4" /></span>}
                      </button>
                    </div>
                  </form>
                 ) : (
                   <div className="text-center p-8 border border-white/10 rounded-xl bg-secondary/30 backdrop-blur">
                     <div className="flex flex-col items-center gap-4">
                        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
                        <p className="text-muted-foreground">Task in progress...</p>
                        <button 
                          onClick={handleClear}
                          className="mt-2 text-sm text-red-400 hover:text-red-300 transition-colors flex items-center gap-2 px-3 py-1.5 rounded-lg hover:bg-red-500/10"
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
                  className="bg-card/50 backdrop-blur-md border border-white/10 rounded-2xl p-6 md:p-8 space-y-6"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-4">
                      <div className="p-3 bg-secondary rounded-lg border border-white/5">
                        <Terminal className="w-6 h-6 text-blue-400" />
                      </div>
                      <div>
                        <div className="text-sm text-muted-foreground font-mono uppercase tracking-wider">Task ID</div>
                        <div className="font-mono text-sm md:text-base text-white/80 truncate max-w-[200px] md:max-w-md">{status.task_id}</div>
                      </div>
                    </div>
                    <div className={cn("px-3 py-1 rounded-full text-xs font-medium border uppercase tracking-wider", getStatusColor(status.status))}>
                      {status.status}
                    </div>
                  </div>

                  <div className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">{status.current_step}</span>
                      <span className="text-white font-mono">{Math.round(status.progress)}%</span>
                    </div>
                    <div className="h-2 w-full bg-secondary rounded-full overflow-hidden">
                      <motion.div 
                        className="h-full bg-gradient-to-r from-blue-500 to-cyan-500"
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

                  {(status.status === 'failed') && (
                    <div className="flex justify-center pt-4">
                      <button 
                        onClick={handleClear}
                        className="text-muted-foreground hover:text-white text-sm transition-colors flex items-center gap-2"
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
