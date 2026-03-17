'use client';

import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
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
  XCircle
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
}

const toolIcons: Record<string, React.ReactNode> = {
  'rag_search': <Search className="w-3.5 h-3.5" />,
  'code_graph': <GitBranch className="w-3.5 h-3.5" />,
  'file_read': <FileCode className="w-3.5 h-3.5" />,
  'repo_map': <Map className="w-3.5 h-3.5" />,
};

const getToolDescription = (tool: string, args?: Record<string, unknown>): string => {
  const descriptions: Record<string, (args?: Record<string, unknown>) => string> = {
    'rag_search': (a) => a?.query ? `搜索相关文档：${String(a.query).slice(0, 30)}...` : '搜索项目文档',
    'code_graph': (a) => {
      const op = String(a?.operation || '');
      const symbol = String(a?.symbol_name || a?.file_path || '').split('/').pop();
      if (op === 'find_definition' && symbol) return `定位定义：${symbol}`;
      if (op === 'find_callers' && symbol) return `查找调用者：${symbol}`;
      if (op === 'find_callees' && symbol) return `查找被调用：${symbol}`;
      if (op === 'get_all_symbols') return '获取所有符号列表';
      return '分析代码结构关系';
    },
    'file_read': (a) => {
      const path = String(a?.file_path || '');
      const fileName = path.split('/').pop() || path;
      return `读取文件：${fileName}`;
    },
    'repo_map': () => '扫描仓库结构',
  };
  return descriptions[tool]?.(args) || `执行 ${tool}`;
};

const TrajectoryDisplay = ({ trajectory }: { trajectory: AgentTrajectoryStep[] }) => {
  const [expanded, setExpanded] = useState(false);
  
  if (!trajectory || trajectory.length === 0) return null;
  
  const successCount = trajectory.filter(s => s.success).length;
  
  return (
    <div className="mt-4 border border-white/10 rounded-xl overflow-hidden bg-white/[0.02]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between text-sm text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
        aria-expanded={expanded}
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-purple-400" />
          <span>探索过程</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300">
            {successCount} 个步骤
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
                  ? "bg-white/[0.02]" 
                  : "bg-red-500/5"
              )}
            >
              <div className={cn(
                "w-7 h-7 rounded-lg flex items-center justify-center shrink-0",
                step.success ? "bg-purple-500/20 text-purple-300" : "bg-red-500/20 text-red-300"
              )}>
                {toolIcons[step.tool] || <Sparkles className="w-3.5 h-3.5" />}
              </div>
              <div className="flex-1 min-w-0 flex items-center justify-between">
                <span className="text-sm text-zinc-300">
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
    <>
      {/* Code Viewer Modal */}
      <CodeViewer 
        isOpen={isCodeViewerOpen} 
        onClose={() => setIsCodeViewerOpen(false)} 
        sources={(message.sources || []).map(parseSource)}
        initialSourceIndex={selectedSourceIndex}
        repoUrl={repoUrl}
      />

      <div className={cn("flex gap-4 group", isUser ? "flex-row-reverse" : "flex-row")}>
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
        
        {!isUser && message.mermaid && (
          <div className="mt-6">
            <div className="text-xs text-zinc-500 mb-2 font-mono uppercase tracking-widest">
              架构图
            </div>
            <Mermaid chart={message.mermaid} />
          </div>
        )}
        
        {!isUser && message.trajectory && message.trajectory.length > 0 && (
          <TrajectoryDisplay trajectory={message.trajectory} />
        )}

        {!isUser && message.sources && message.sources.length > 0 && (
          <div className="mt-4">
            <div className="text-[10px] text-zinc-500 font-medium uppercase tracking-wider mb-2 flex items-center gap-2">
              <span>参考文件</span>
              <div className="h-[1px] flex-1 bg-white/5"></div>
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
    </>
  );
});

MessageItem.displayName = 'MessageItem';
