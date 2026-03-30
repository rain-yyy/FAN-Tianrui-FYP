'use client';

import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { motion } from 'framer-motion';
import { 
  Sparkles, 
  Bot, 
  ChevronDown, 
  ChevronUp, 
  Search, 
  GitBranch, 
  FileCode, 
  Map, 
  CheckCircle2, 
  XCircle,
  TextSearch,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { ChatMessage, AgentTrajectoryStep, ChatMode } from '@/lib/api';
import Mermaid from './Mermaid';
import CodeViewer from './CodeViewer';
import SourcesPanel, { parseSource } from './SourcesPanel';

export interface DisplayMessage extends ChatMessage {
  id: string;
  timestamp: Date;
  sources?: string[];
  isError?: boolean;
  mode?: ChatMode;
  mermaid?: string | null;
  trajectory?: AgentTrajectoryStep[];
  isNew?: boolean;
}

const toolIcons: Record<string, React.ReactNode> = {
  'rag_search': <Search className="w-3.5 h-3.5" />,
  'code_graph': <GitBranch className="w-3.5 h-3.5" />,
  'file_read': <FileCode className="w-3.5 h-3.5" />,
  'repo_map': <Map className="w-3.5 h-3.5" />,
  'grep_search': <TextSearch className="w-3.5 h-3.5" />,
};

// Mermaid wrapper with error boundary
const MermaidWrapper = ({ chart, isStreaming }: { chart: string; isStreaming?: boolean }) => {
  const [hasError, setHasError] = React.useState(false);

  // Reset error state when chart changes
  React.useEffect(() => {
    setHasError(false);
  }, [chart]);

  if (hasError) {
    return (
      <div className="text-xs text-stone-600 p-2 bg-stone-100 rounded border border-stone-200">
        Failed to load diagram
      </div>
    );
  }

  // Wrap in error boundary
  try {
    return <Mermaid chart={chart} isStreaming={isStreaming} />;
  } catch {
    setHasError(true);
    return null;
  }
};

const getToolDescription = (tool: string, args?: Record<string, unknown>): string => {
  const descriptions: Record<string, (args?: Record<string, unknown>) => string> = {
    'rag_search': (a) => a?.query ? `Search docs: ${String(a.query).slice(0, 30)}...` : 'Search project docs',
    'code_graph': (a) => {
      const op = String(a?.operation || '');
      const symbol = String(a?.symbol_name || a?.file_path || '').split('/').pop();
      if (op === 'find_definition' && symbol) return `Find definition: ${symbol}`;
      if (op === 'find_callers' && symbol) return `Find callers: ${symbol}`;
      if (op === 'find_callees' && symbol) return `Find callees: ${symbol}`;
      if (op === 'get_all_symbols') return 'List all symbols';
      return 'Analyze code structure';
    },
    'file_read': (a) => {
      const path = String(a?.file_path || '');
      const fileName = path.split('/').pop() || path;
      return `Read file: ${fileName}`;
    },
    'repo_map': () => 'Scan repository layout',
    'grep_search': (a) => {
      const p = String(a?.pattern || '').slice(0, 40);
      return p ? `Grep: ${p}${p.length >= 40 ? '…' : ''}` : 'Lexical repo search';
    },
  };
  return descriptions[tool]?.(args) || `Run ${tool}`;
};

const StreamingMarkdown = ({ content, isNew }: { content: string, isNew?: boolean }) => {
  const [isStabilized, setIsStabilized] = useState(!isNew);

  React.useEffect(() => {
    if (!isNew) {
      setIsStabilized(true);
      return;
    }
    
    // For new messages, wait a short moment before enabling full markdown
    // This prevents expensive re-renders during rapid content updates
    const timer = setTimeout(() => {
      setIsStabilized(true);
    }, 100);
    
    return () => clearTimeout(timer);
  }, [isNew]);

  // If content is still streaming or very new, render with simpler processing
  if (!isStabilized && isNew) {
    return (
      <div className="whitespace-pre-wrap text-stone-800 text-[15px] leading-[1.7]">
        {content}
      </div>
    );
  }

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
    >
      {content}
    </ReactMarkdown>
  );
};

const TrajectoryDisplay = ({ trajectory }: { trajectory: AgentTrajectoryStep[] }) => {
  const [expanded, setExpanded] = useState(false);
  
  if (!trajectory || trajectory.length === 0) return null;
  
  const successCount = trajectory.filter(s => s.success).length;
  
  return (
    <div className="mt-4 border border-stone-200 rounded-xl overflow-hidden bg-stone-50">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between text-sm text-stone-600 hover:text-stone-900 hover:bg-stone-100 transition-colors"
        aria-expanded={expanded}
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-teal-700" />
          <span>Exploration</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-teal-100 text-teal-900 border border-teal-200">
            {successCount} steps
          </span>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </button>
      
      {expanded && (
        <div className="px-4 pb-4 space-y-2">
          {trajectory.map((step, idx) => (
            <div 
              key={idx}
              className={cn(
                "flex items-center gap-3 p-3 rounded-lg transition-colors",
                step.success 
                  ? "bg-white" 
                  : "bg-rose-50"
              )}
            >
              <div className={cn(
                "w-7 h-7 rounded-lg flex items-center justify-center shrink-0",
                step.success ? "bg-teal-100 text-teal-900" : "bg-rose-100 text-rose-800"
              )}>
                {toolIcons[step.tool] || <Sparkles className="w-3.5 h-3.5" />}
              </div>
              <div className="flex-1 min-w-0 flex items-center justify-between">
                <span className="text-sm text-stone-700">
                  {getToolDescription(step.tool, step.arguments)}
                </span>
                {step.success ? (
                  <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};


export interface MessageItemProps {
  message: DisplayMessage;
  repoUrl: string;
}

export const MessageItem = React.memo(({ message, repoUrl }: MessageItemProps) => {
  const isUser = message.role === 'user';
  const isAgentMode = message.mode === 'agent';
  const [isCodeViewerOpen, setIsCodeViewerOpen] = useState(false);
  const [selectedSourceIndex, setSelectedSourceIndex] = useState(0);

  const handleSourceClick = (source: any, index: number) => {
    setSelectedSourceIndex(index);
    setIsCodeViewerOpen(true);
  };
  
  return (
    <motion.div 
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="mb-8"
    >
      {/* Code Viewer Modal */}
      <CodeViewer 
        isOpen={isCodeViewerOpen} 
        onClose={() => setIsCodeViewerOpen(false)} 
        sources={(message.sources || []).map(parseSource)}
        initialSourceIndex={selectedSourceIndex}
        repoUrl={repoUrl}
      />

      <div className={cn("flex gap-4 group", isUser ? "flex-row-reverse" : "flex-row")}>
      {!isUser && (
        <div className={cn(
          "w-8 h-8 rounded-xl flex items-center justify-center shrink-0 mt-1 shadow-sm",
          isAgentMode
            ? "bg-teal-50 ring-1 ring-teal-200"
            : "bg-sky-50 ring-1 ring-sky-200"
        )}>
          {isAgentMode ? (
            <Bot className="w-4 h-4 text-teal-700" />
          ) : (
            <Sparkles className="w-4 h-4 text-sky-700" />
          )}
        </div>
      )}
      
      <div className={cn(
        "flex-1 min-w-0 space-y-1.5", 
        isUser ? "flex flex-col items-end" : "text-left"
      )}>
        {!isUser && (
          <div className="flex items-center gap-2 mb-1.5 ml-1">
            <span className={cn(
              "text-[13px] font-medium tracking-wide", 
              isAgentMode ? "text-teal-700" : "text-sky-700"
            )}>
              {isAgentMode ? "Agent" : "Assistant"}
            </span>
            {isAgentMode && (
              <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-teal-100 text-teal-900 border border-teal-200 font-mono tracking-wider">
                AGENT
              </span>
            )}
            <span className="text-[11px] text-muted-foreground/40">
              {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        )}

        <div className={cn(
          isUser 
            ? "bg-sky-600 text-white px-5 py-3.5 rounded-3xl rounded-tr-md max-w-[85%] text-[15px] leading-relaxed shadow-sm"
            : "prose prose-stone prose-sm max-w-none prose-p:text-stone-700 prose-p:leading-[1.7] prose-p:text-[15px] prose-pre:bg-stone-100 prose-pre:border prose-pre:border-stone-200 prose-pre:rounded-xl prose-pre:shadow-sm prose-code:text-sky-800 prose-code:bg-sky-50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:font-medium prose-headings:text-stone-900 prose-headings:font-semibold prose-a:text-sky-700 hover:prose-a:text-sky-800 prose-a:no-underline prose-ul:my-2 prose-li:my-0.5"
        )}>
          {isUser ? (
            <div className="whitespace-pre-wrap">{message.content}</div>
          ) : (
            <StreamingMarkdown content={message.content} isNew={message.isNew} />
          )}
        </div>
        
        {!isUser && message.mermaid && (
          <div className="mt-6 p-4 rounded-2xl bg-stone-50 border border-stone-200">
            <div className="text-[11px] text-stone-500 mb-3 font-mono uppercase tracking-widest flex items-center gap-2">
              <Map className="w-3.5 h-3.5" />
              Architecture
            </div>
            <MermaidWrapper chart={message.mermaid} isStreaming={message.isNew} />
          </div>
        )}
        
        {!isUser && message.trajectory && message.trajectory.length > 0 && (
          <TrajectoryDisplay trajectory={message.trajectory} />
        )}

        {!isUser && message.sources && message.sources.length > 0 && (
          <div className="mt-5">
            <div className="text-[10px] text-stone-500 font-medium uppercase tracking-wider mb-2.5 flex items-center gap-2">
              <span>References</span>
              <div className="h-[1px] flex-1 bg-gradient-to-r from-stone-200 to-transparent"></div>
            </div>
            <SourcesPanel 
              sources={message.sources} 
              onSourceClick={handleSourceClick}
              compact={true}
            />
          </div>
        )}
      </div>
    </div>
    </motion.div>
  );
});

MessageItem.displayName = 'MessageItem';
