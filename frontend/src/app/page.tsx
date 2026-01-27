'use client';

import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { api, TaskStatusResponse } from '@/lib/api';
import WikiViewer from '@/components/WikiViewer';
import { 
  Github, 
  ArrowRight, 
  Loader2, 
  Terminal,
  RotateCcw
} from 'lucide-react';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export default function Home() {
  const [url, setUrl] = useState('');
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [health, setHealth] = useState<boolean | null>(null);
  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Check health on mount
  useEffect(() => {
    const check = async () => {
      const isOk = await api.checkHealth();
      setHealth(isOk);
    };
    check();
    
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
    if (!url.trim()) return;

    setIsSubmitting(true);
    setStatus(null);
    try {
      const res = await api.createTask(url);
      setTaskId(res.task_id);
      localStorage.setItem('wiki_gen_task_id', res.task_id);
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
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
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
          className={cn("text-center space-y-4", isCompleted ? "flex items-center justify-between space-y-0 mb-6" : "")}
        >
          {isCompleted ? (
             <>
               <div className="flex items-center gap-4">
                 <h1 className="text-2xl font-bold tracking-tight text-white">Project Wiki</h1>
                 <div className="hidden md:flex items-center gap-2 px-3 py-1 rounded-full bg-secondary border border-border text-xs text-muted-foreground font-mono">
                    <div className="w-2 h-2 rounded-full bg-green-500" />
                    Generated
                 </div>
               </div>
               <button 
                  onClick={handleClear}
                  className="flex items-center gap-2 px-4 py-2 text-sm text-muted-foreground hover:text-white hover:bg-white/5 rounded-lg transition-colors"
               >
                 <RotateCcw className="w-4 h-4" />
                 New Project
               </button>
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
          {!isCompleted && (
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

          {isCompleted && (
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
                />
             </motion.div>
          )}
        </AnimatePresence>
      </div>
    </main>
  );
}
