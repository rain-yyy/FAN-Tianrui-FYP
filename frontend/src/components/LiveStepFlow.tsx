'use client';

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  Search, 
  GitBranch, 
  FileCode, 
  Map, 
  Loader2,
  CheckCircle2,
  Brain,
  Sparkles,
  Database,
  FileSearch,
  Zap,
  TextSearch,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { LiveToolStep } from '@/lib/api';
import { t } from '@/lib/i18n';

export interface LiveStep {
  id: string;
  type: 'planning' | 'tool' | 'retrieval' | 'evaluation' | 'synthesis' | 'hyde';
  status: 'pending' | 'running' | 'done' | 'error';
  title: string;
  description?: string;
  details?: string;
  elapsed_ms?: number;
  tool?: LiveToolStep;
}

interface LiveStepFlowProps {
  steps: LiveStep[];
  isAgent: boolean;
  currentPhase?: string;
  streamingAnswer?: string;
}

const stepIcons: Record<string, React.ReactNode> = {
  planning: <Brain className="w-4 h-4" />,
  retrieval: <Database className="w-4 h-4" />,
  hyde: <Sparkles className="w-4 h-4" />,
  evaluation: <Zap className="w-4 h-4" />,
  synthesis: <FileSearch className="w-4 h-4" />,
  rag_search: <Search className="w-4 h-4" />,
  code_graph: <GitBranch className="w-4 h-4" />,
  file_read: <FileCode className="w-4 h-4" />,
  repo_map: <Map className="w-4 h-4" />,
  grep_search: <TextSearch className="w-4 h-4" />,
};

const StepItem = ({ step, isLast }: { step: LiveStep; isLast: boolean }) => {
  const icon = step.tool 
    ? stepIcons[step.tool.tool] || <Sparkles className="w-4 h-4" />
    : stepIcons[step.type] || <Sparkles className="w-4 h-4" />;

  const statusColors = {
    pending: 'text-stone-500 bg-stone-100',
    running: 'text-sky-800 bg-sky-100 animate-pulse',
    done: 'text-emerald-800 bg-emerald-50',
    error: 'text-rose-800 bg-rose-50',
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 20 }}
      transition={{ duration: 0.3 }}
      className="relative"
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div className={cn(
          "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 transition-all",
          statusColors[step.status]
        )}>
          {step.status === 'running' ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : step.status === 'done' ? (
            <CheckCircle2 className="w-4 h-4" />
          ) : (
            icon
          )}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0 pb-4">
          <div className="flex items-center gap-2">
            <span className={cn(
              "text-sm font-medium",
              step.status === 'running' ? "text-sky-900" :
              step.status === 'done' ? "text-emerald-900" :
              step.status === 'error' ? "text-rose-900" :
              "text-stone-600"
            )}>
              {step.title}
            </span>
            {step.elapsed_ms && step.status === 'done' && (
              <span className="text-[10px] text-stone-500 font-mono">
                {step.elapsed_ms}ms
              </span>
            )}
          </div>
          
          {step.description && (
            <p className="text-xs text-stone-600 mt-0.5 truncate">
              {step.description}
            </p>
          )}
          
          {step.details && step.status === 'running' && (
            <motion.p 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="text-[11px] text-stone-500 mt-1 font-mono"
            >
              {step.details}
            </motion.p>
          )}
        </div>
      </div>

      {/* Connector line */}
      {!isLast && (
        <div className="absolute left-4 top-8 bottom-0 w-px bg-gradient-to-b from-stone-300 to-transparent" />
      )}
    </motion.div>
  );
};

export const LiveStepFlow = ({ steps, isAgent, currentPhase, streamingAnswer }: LiveStepFlowProps) => {
  if (steps.length === 0 && !currentPhase) {
    return null;
  }

  return (
    <div className="space-y-1">
      {/* Header */}
      <div className="flex items-center gap-2 mb-3">
        <div className={cn(
          "w-2 h-2 rounded-full animate-pulse",
          isAgent ? "bg-teal-600" : "bg-sky-600"
        )} />
        <span className={cn(
          "text-xs font-medium uppercase tracking-wider",
          isAgent ? "text-teal-800" : "text-sky-800"
        )}>
          {isAgent ? t('agentWorking') : t('processing')}
        </span>
      </div>

      {/* Steps */}
      <div className="pl-1">
        <AnimatePresence mode="popLayout">
          {steps.map((step, idx) => (
            <StepItem 
              key={step.id} 
              step={step} 
              isLast={idx === steps.length - 1 && !streamingAnswer} 
            />
          ))}
        </AnimatePresence>
      </div>

      {/* Streaming answer preview */}
      {streamingAnswer && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-4 p-3 rounded-lg bg-white border border-stone-200"
        >
          <div className="flex items-center gap-2 mb-2">
            <Sparkles className="w-3.5 h-3.5 text-sky-600" />
            <span className="text-xs text-stone-600">{t('generatingPreview')}</span>
          </div>
          <p className="text-sm text-stone-800 line-clamp-3">
            {streamingAnswer.slice(0, 200)}
            {streamingAnswer.length > 200 && '...'}
          </p>
        </motion.div>
      )}
    </div>
  );
};

export default LiveStepFlow;
