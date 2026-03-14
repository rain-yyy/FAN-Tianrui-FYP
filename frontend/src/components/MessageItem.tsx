'use client';

import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Sparkles, Bot, ChevronDown, ChevronUp, Search, GitBranch, FileCode, Map, CheckCircle2, XCircle, Gauge } from 'lucide-react';
import { cn } from '@/lib/utils';
import { ChatMessage, AgentTrajectoryStep, ChatMode } from '@/lib/api';
import Mermaid from './Mermaid';

export interface DisplayMessage extends ChatMessage {
  id: string;
  timestamp: Date;
  sources?: string[];
  isError?: boolean;
  // Agent mode specific fields
  mode?: ChatMode;
  mermaid?: string | null;
  trajectory?: AgentTrajectoryStep[];
  confidence?: number;
  iterations?: number;
}

interface MessageItemProps {
  message: DisplayMessage;
}

const toolIcons: Record<string, React.ReactNode> = {
  'rag_search': <Search className="w-3.5 h-3.5" />,
  'code_graph': <GitBranch className="w-3.5 h-3.5" />,
  'file_read': <FileCode className="w-3.5 h-3.5" />,
  'repo_map': <Map className="w-3.5 h-3.5" />,
};

const TrajectoryDisplay = ({ trajectory }: { trajectory: AgentTrajectoryStep[] }) => {
  const [expanded, setExpanded] = useState(false);
  
  if (!trajectory || trajectory.length === 0) return null;
  
  return (
    <div className="mt-4 border border-white/10 rounded-xl overflow-hidden bg-white/[0.02]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between text-sm text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-purple-400" />
          <span>Agent 思考过程</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300">
            {trajectory.length} 步骤
          </span>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
      </button>
      
      {expanded && (
        <div className="px-4 pb-4 space-y-3">
          {trajectory.map((step, idx) => (
            <div 
              key={idx}
              className={cn(
                "flex items-start gap-3 p-3 rounded-lg border transition-colors",
                step.success 
                  ? "border-white/10 bg-white/[0.02]" 
                  : "border-red-500/20 bg-red-500/5"
              )}
            >
              <div className={cn(
                "w-8 h-8 rounded-lg flex items-center justify-center shrink-0",
                step.success ? "bg-purple-500/20 text-purple-300" : "bg-red-500/20 text-red-300"
              )}>
                {toolIcons[step.tool] || <Sparkles className="w-3.5 h-3.5" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs font-mono text-zinc-500">Step {step.step}</span>
                  <span className="text-sm font-medium text-white/90">{step.tool}</span>
                  {step.success ? (
                    <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
                  ) : (
                    <XCircle className="w-3.5 h-3.5 text-red-400" />
                  )}
                </div>
                <p className="text-sm text-zinc-400">{step.description}</p>
                {step.preview && (
                  <div className="mt-2 text-xs text-zinc-500 font-mono bg-black/30 rounded-lg p-2 max-h-20 overflow-y-auto">
                    {step.preview}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const ConfidenceIndicator = ({ confidence, iterations }: { confidence: number; iterations: number }) => {
  const getConfidenceColor = (c: number) => {
    if (c >= 0.8) return 'text-green-400 bg-green-500/20';
    if (c >= 0.6) return 'text-yellow-400 bg-yellow-500/20';
    return 'text-orange-400 bg-orange-500/20';
  };
  
  const getConfidenceLabel = (c: number) => {
    if (c >= 0.8) return '高置信度';
    if (c >= 0.6) return '中等置信度';
    return '低置信度';
  };
  
  return (
    <div className="flex items-center gap-3 mt-3 text-xs">
      <div className={cn("flex items-center gap-1.5 px-2 py-1 rounded-full", getConfidenceColor(confidence))}>
        <Gauge className="w-3 h-3" />
        <span>{getConfidenceLabel(confidence)} ({Math.round(confidence * 100)}%)</span>
      </div>
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-blue-500/20 text-blue-300">
        <Bot className="w-3 h-3" />
        <span>{iterations} 次迭代</span>
      </div>
    </div>
  );
};

export const MessageItem = React.memo(({ message }: MessageItemProps) => {
  const isUser = message.role === 'user';
  const isAgentMode = message.mode === 'agent';
  
  return (
    <div className={cn("flex gap-4 group", isUser ? "flex-row-reverse" : "flex-row")}>
      {/* Avatar */}
      <div className={cn(
        "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1",
        isUser 
          ? "bg-white/10" 
          : isAgentMode
            ? "bg-gradient-to-br from-purple-500/20 to-fuchsia-500/20"
            : "bg-gradient-to-br from-blue-500/20 to-purple-500/20"
      )}>
        {isUser ? (
          <div className="w-4 h-4 rounded-full bg-white/50" />
        ) : isAgentMode ? (
          <Bot className="w-4 h-4 text-purple-400" />
        ) : (
          <Sparkles className="w-4 h-4 text-blue-400" />
        )}
      </div>
      
      {/* Content */}
      <div className={cn("flex-1 min-w-0 space-y-1", isUser && "text-right")}>
        <div className="flex items-center gap-2 mb-1">
          <span className={cn(
            "text-sm font-medium", 
            isUser 
              ? "text-white/90 ml-auto" 
              : isAgentMode 
                ? "text-purple-400"
                : "text-blue-400"
          )}>
            {isUser ? "You" : isAgentMode ? "Agent" : "Assistant"}
          </span>
          {isAgentMode && !isUser && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 font-mono">
              AGENT
            </span>
          )}
          <span className="text-xs text-muted-foreground/50">
            {message.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
          </span>
        </div>

        <div className={cn(
          "prose prose-invert prose-sm max-w-none",
          "prose-p:text-gray-300 prose-p:leading-relaxed",
          "prose-pre:bg-[#151515] prose-pre:border prose-pre:border-white/10 prose-pre:rounded-xl",
          "prose-code:text-blue-200 prose-code:bg-white/5 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:font-normal",
          "prose-headings:text-white prose-headings:font-semibold",
          "prose-a:text-blue-400 hover:prose-a:text-blue-300 prose-a:no-underline",
          "prose-ul:my-2 prose-li:my-0.5",
          isUser && "text-white/90"
        )}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
          >
            {message.content}
          </ReactMarkdown>
        </div>
        
        {/* Mermaid Diagram for Agent responses */}
        {!isUser && message.mermaid && (
          <div className="mt-6">
            <div className="text-xs text-zinc-500 mb-2 font-mono uppercase tracking-widest">
              架构图
            </div>
            <Mermaid chart={message.mermaid} />
          </div>
        )}
        
        {/* Confidence & Iterations for Agent */}
        {!isUser && isAgentMode && message.confidence !== undefined && (
          <ConfidenceIndicator 
            confidence={message.confidence} 
            iterations={message.iterations || 0} 
          />
        )}
        
        {/* Trajectory Display for Agent */}
        {!isUser && message.trajectory && message.trajectory.length > 0 && (
          <TrajectoryDisplay trajectory={message.trajectory} />
        )}
      </div>
    </div>
  );
});

MessageItem.displayName = 'MessageItem';
