'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  ArrowUp, 
  Loader2, 
  FileText,
  Sparkles,
  Zap,
  X,
  ArrowLeft,
  CheckCircle2,
  Bot,
  Search,
  GitBranch,
  FileCode,
  Map
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { api, ChatMessage, ChatResponse, AgentChatResponse, ChatMode } from '@/lib/api';
import { MessageItem, DisplayMessage } from './MessageItem';

interface ChatInterfaceProps {
  userId: string;
  repoUrl: string;
  currentPageContext?: string;
  currentPageTitle?: string;
}

export default function ChatInterface({ 
  userId,
  repoUrl, 
  currentPageContext,
  currentPageTitle 
}: ChatInterfaceProps) {
  const [mode, setMode] = useState<'bar' | 'full'>('bar');
  const [chatMode, setChatMode] = useState<ChatMode>('rag');
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState<string>('');
  const [scannedFiles, setScannedFiles] = useState<string[]>([]);
  const [chatId, setChatId] = useState<string | undefined>(undefined);
  const [lastTrajectory, setLastTrajectory] = useState<DisplayMessage['trajectory']>([]);
  const [liveAgentLogs, setLiveAgentLogs] = useState<string[]>([]);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fullViewInputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    if (mode === 'full') {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [mode]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, mode, scrollToBottom]);

  useEffect(() => {
    if (mode === 'full' && fullViewInputRef.current) {
      fullViewInputRef.current.focus();
    }
  }, [mode]);

  useEffect(() => {
    setChatId(undefined);
    setMessages([]);
  }, [repoUrl, userId]);

  const generateId = () => `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

  const buildConversationHistory = (): ChatMessage[] => {
    return messages
      .filter(msg => !msg.isError)
      .map(msg => ({
        role: msg.role,
        content: msg.content,
      }));
  };

  const handleSendMessage = async () => {
    const question = inputValue.trim();
    if (!question || isLoading) return;

    if (mode === 'bar') {
      setMode('full');
    }

    setInputValue('');
    
    if (inputRef.current) inputRef.current.style.height = 'auto';
    if (fullViewInputRef.current) fullViewInputRef.current.style.height = 'auto';

    const userMessage: DisplayMessage = {
      id: generateId(),
      role: 'user',
      content: question,
      timestamp: new Date(),
      mode: chatMode,
    };
    setMessages(prev => [...prev, userMessage]);

    setIsLoading(true);
    setScannedFiles([]);
    setLiveAgentLogs([]);
    setLoadingStatus(chatMode === 'agent' ? '分析问题意图...' : 'Thinking...');

    try {
      if (chatMode === 'agent') {
        // Agent mode (streaming)
        setLoadingStatus('Agent 正在规划探索路径...');

        const appendLiveLog = (line: string) => {
          setLiveAgentLogs(prev => [...prev.slice(-7), line]);
        };

        let finalResponse: AgentChatResponse | null = null;
        for await (const event of api.askAgentQuestionStream({
          user_id: userId,
          question,
          repo_url: repoUrl,
          chat_id: chatId,
          conversation_history: buildConversationHistory(),
          current_page_context: currentPageContext,
        })) {
          if (event.type === 'planning') {
            const status = String(event.data.status || '正在分析问题...');
            setLoadingStatus(status);
            appendLiveLog(`🧭 ${status}`);
            continue;
          }

          if (event.type === 'tool_call') {
            const status = String(event.data.status || '');
            if (status === 'start') {
              const tools = Array.isArray(event.data.tools) ? event.data.tools : [];
              const names = tools
                .map((item) => (item && typeof item === 'object' && 'tool' in item ? String(item.tool) : null))
                .filter(Boolean)
                .join(' + ');
              const line = names ? `并行执行工具: ${names}` : '开始执行工具...';
              setLoadingStatus(line);
              appendLiveLog(`🔧 ${line}`);
            } else if (status === 'done') {
              const elapsed = Number(event.data.elapsed_ms || 0);
              const line = `工具执行完成 (${elapsed}ms)`;
              setLoadingStatus(line);
              appendLiveLog(`✅ ${line}`);
            } else {
              const line = '执行工具中...';
              setLoadingStatus(line);
              appendLiveLog(`🔍 ${line}`);
            }
            continue;
          }

          if (event.type === 'evaluation') {
            const status = String(event.data.status || '');
            if (status === 'done') {
              const confidence = Number(event.data.confidence ?? 0);
              const percentage = Number.isFinite(confidence) ? Math.round(confidence * 100) : 0;
              const line = `评估上下文充分性（置信度 ${percentage}%）`;
              setLoadingStatus(line);
              appendLiveLog(`📊 ${line}`);
            }
            continue;
          }

          if (event.type === 'synthesis') {
            const line = String(event.data.status || '') === 'done'
              ? '答案合成完成'
              : '正在生成最终答案...';
            setLoadingStatus(line);
            appendLiveLog(`🧠 ${line}`);
            continue;
          }

          if (event.type === 'error') {
            const errorDetail = String(event.data.error || event.data.detail || 'Agent stream failed');
            throw new Error(errorDetail);
          }

          if (event.type === 'complete') {
            finalResponse = event.data as unknown as AgentChatResponse;
          }
        }

        if (!finalResponse) {
          throw new Error('Agent 未返回最终结果');
        }

        setChatId(finalResponse.chat_id);

        const assistantMessage: DisplayMessage = {
          id: generateId(),
          role: 'assistant',
          content: finalResponse.answer,
          timestamp: new Date(),
          sources: finalResponse.sources,
          mode: 'agent',
          mermaid: finalResponse.mermaid,
          trajectory: finalResponse.trajectory,
          confidence: finalResponse.confidence,
          iterations: finalResponse.iterations,
        };
        setMessages(prev => [...prev, assistantMessage]);
        setLastTrajectory(finalResponse.trajectory);
        
        if (finalResponse.sources) {
          setScannedFiles(finalResponse.sources);
        }
      } else {
        // RAG mode
        const response: ChatResponse = await api.askQuestion({
          user_id: userId,
          question,
          repo_url: repoUrl,
          chat_id: chatId,
          conversation_history: buildConversationHistory(),
          current_page_context: currentPageContext,
        });
        setChatId(response.chat_id);

        const assistantMessage: DisplayMessage = {
          id: generateId(),
          role: 'assistant',
          content: response.answer,
          timestamp: new Date(),
          sources: response.sources,
          mode: 'rag',
        };
        setMessages(prev => [...prev, assistantMessage]);
        
        if (response.sources) {
          setScannedFiles(response.sources);
        }
      }

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to send message';
      
      const errorDisplayMessage: DisplayMessage = {
        id: generateId(),
        role: 'assistant',
        content: errorMessage,
        timestamp: new Date(),
        isError: true,
        mode: chatMode,
      };
      setMessages(prev => [...prev, errorDisplayMessage]);
    } finally {
      setIsLoading(false);
      setLoadingStatus('');
      setLiveAgentLogs([]);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputValue(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
  };

  const repoName = repoUrl.split('/').slice(-2).join('/');

  return (
    <>
      <AnimatePresence>
        {mode === 'bar' && (
          <motion.div
            initial={{ y: 100, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 100, opacity: 0 }}
            transition={{ type: 'spring', damping: 20, stiffness: 300 }}
            className="fixed bottom-8 left-1/2 -translate-x-1/2 z-40 w-full max-w-2xl px-4"
          >
            <div className="relative group">
              <div className={cn(
                "absolute inset-0 rounded-2xl blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-500",
                chatMode === 'agent' 
                  ? "bg-gradient-to-r from-purple-500/20 to-fuchsia-500/20" 
                  : "bg-gradient-to-r from-blue-500/20 to-purple-500/20"
              )} />
              <div className={cn(
                "relative bg-[#0A0A0A]/80 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col transition-all focus-within:ring-1",
                chatMode === 'agent' ? "focus-within:ring-purple-500/50" : "focus-within:ring-blue-500/50"
              )}>
                <div className="flex items-end gap-2 p-3">
                  <div className={cn(
                    "flex items-center justify-center w-8 h-8 rounded-lg mb-1 shrink-0",
                    chatMode === 'agent' ? "bg-purple-500/10" : "bg-blue-500/10"
                  )}>
                    {chatMode === 'agent' ? (
                      <Bot className="w-4 h-4 text-purple-400" />
                    ) : (
                      <Sparkles className="w-4 h-4 text-blue-400" />
                    )}
                  </div>
                  <textarea
                    ref={inputRef}
                    value={inputValue}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    placeholder={`Ask about ${repoName}...`}
                    className="flex-1 bg-transparent border-none outline-none text-sm text-white placeholder:text-muted-foreground resize-none py-2 max-h-[120px]"
                    rows={1}
                    aria-label="Chat input"
                  />
                  <button
                    onClick={handleSendMessage}
                    disabled={!inputValue.trim() || isLoading}
                    className={cn(
                      "p-2 rounded-lg transition-all mb-0.5",
                      inputValue.trim() && !isLoading
                        ? chatMode === 'agent'
                          ? "bg-purple-600 text-white hover:bg-purple-500 shadow-lg shadow-purple-500/20"
                          : "bg-blue-600 text-white hover:bg-blue-500 shadow-lg shadow-blue-500/20"
                        : "bg-white/5 text-muted-foreground cursor-not-allowed"
                    )}
                    aria-label="Send message"
                  >
                    <ArrowUp className="w-4 h-4" />
                  </button>
                </div>
                <div className="px-4 pb-2 flex items-center gap-3 text-[10px] text-muted-foreground/70">
                  {/* Mode Toggle */}
                  <button
                    onClick={() => setChatMode(chatMode === 'rag' ? 'agent' : 'rag')}
                    className={cn(
                      "flex items-center gap-1.5 px-2 py-1 rounded-full transition-all",
                      chatMode === 'agent' 
                        ? "bg-purple-500/20 text-purple-300 border border-purple-500/30" 
                        : "bg-blue-500/10 text-blue-400/80 border border-transparent hover:border-blue-500/30"
                    )}
                  >
                    {chatMode === 'agent' ? (
                      <>
                        <Bot className="w-3 h-3" />
                        <span>Agent 模式</span>
                      </>
                    ) : (
                      <>
                        <Zap className="w-3 h-3" />
                        <span>RAG 模式</span>
                      </>
                    )}
                  </button>
                  {currentPageTitle && (
                    <div className="flex items-center gap-1 max-w-[200px] truncate">
                      <FileText className="w-3 h-3 text-blue-400/70" />
                      <span className="truncate">Context: {currentPageTitle}</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {mode === 'full' && (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-50 bg-[#0A0A0A] flex flex-col"
          >
            <header className="h-14 border-b border-white/10 flex items-center justify-between px-4 bg-white/[0.02]">
              <div className="flex items-center gap-4">
                <button 
                  onClick={() => setMode('bar')}
                  className="flex items-center gap-2 text-sm text-muted-foreground hover:text-white transition-colors"
                  aria-label="Back to floating bar"
                >
                  <ArrowLeft className="w-4 h-4" />
                  <span className="font-mono">{repoName}</span>
                </button>
                {currentPageTitle && (
                  <>
                    <span className="text-white/20">/</span>
                    <span className="text-sm text-white/80 truncate max-w-[200px]">{currentPageTitle}</span>
                  </>
                )}
              </div>
              <div className="flex items-center gap-3">
                {/* Mode Toggle in Full View */}
                <button
                  onClick={() => setChatMode(chatMode === 'rag' ? 'agent' : 'rag')}
                  className={cn(
                    "flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium transition-all",
                    chatMode === 'agent' 
                      ? "bg-purple-500/20 text-purple-300 border border-purple-500/30" 
                      : "bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20"
                  )}
                >
                  {chatMode === 'agent' ? (
                    <>
                      <Bot className="w-3.5 h-3.5" />
                      <span>Agent 模式</span>
                    </>
                  ) : (
                    <>
                      <Zap className="w-3.5 h-3.5" />
                      <span>RAG 模式</span>
                    </>
                  )}
                </button>
                <button 
                  onClick={() => setMode('bar')}
                  className="p-2 text-muted-foreground hover:text-white transition-colors"
                  aria-label="Close chat"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </header>

            <div className="flex-1 flex overflow-hidden">
              <div className="flex-1 flex flex-col min-w-0 relative">
                <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-8 custom-scrollbar">
                  {messages.map((message) => (
                    <div key={message.id} className="max-w-3xl mx-auto">
                      <MessageItem message={message} />
                    </div>
                  ))}
                  
                  {isLoading && (
                    <div className="max-w-3xl mx-auto pl-12">
                      <div className={cn(
                        "flex items-center gap-2 text-sm",
                        chatMode === 'agent' ? "text-purple-300" : "text-muted-foreground"
                      )}>
                        {chatMode === 'agent' ? (
                          <Bot className="w-4 h-4 animate-pulse" />
                        ) : (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        )}
                        <span>{loadingStatus || 'Thinking...'}</span>
                      </div>
                      {chatMode === 'agent' && liveAgentLogs.length > 0 && (
                        <div className="mt-3 space-y-1">
                          {liveAgentLogs.map((log, index) => (
                            <div key={`${log}-${index}`} className="text-xs text-zinc-400 font-mono">
                              {log}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  <div ref={messagesEndRef} className="h-4" />
                </div>

                <div className="p-4 md:p-6 border-t border-white/5 bg-[#0A0A0A]">
                  <div className="max-w-3xl mx-auto relative">
                    <div className="relative bg-white/5 border border-white/10 rounded-xl overflow-hidden focus-within:ring-1 focus-within:ring-blue-500/50 transition-all">
                      <textarea
                        ref={fullViewInputRef}
                        value={inputValue}
                        onChange={handleInputChange}
                        onKeyDown={handleKeyDown}
                        placeholder="Ask a follow-up question..."
                        className="w-full bg-transparent border-none outline-none text-sm text-white placeholder:text-muted-foreground resize-none px-4 py-3 max-h-[200px]"
                        rows={1}
                        disabled={isLoading}
                        aria-label="Follow-up question"
                      />
                      <div className="absolute bottom-2 right-2 flex items-center gap-2">
                        <button
                          onClick={handleSendMessage}
                          disabled={!inputValue.trim() || isLoading}
                          className={cn(
                            "p-1.5 rounded-lg transition-all",
                            inputValue.trim() && !isLoading
                              ? chatMode === 'agent'
                                ? "bg-purple-600 text-white hover:bg-purple-500"
                                : "bg-blue-600 text-white hover:bg-blue-500"
                              : "bg-white/5 text-muted-foreground cursor-not-allowed"
                          )}
                          aria-label="Send follow-up"
                        >
                          <ArrowUp className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <aside className="hidden lg:flex w-80 border-l border-white/10 bg-white/[0.02] flex-col">
                {/* Sources Section */}
                <div className="p-4 border-b border-white/5">
                  <h3 className="text-sm font-medium text-white/80 flex items-center gap-2">
                    <FileText className="w-4 h-4 text-blue-400" />
                    引用来源
                  </h3>
                </div>
                <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                  {scannedFiles.length > 0 ? (
                    <div className="space-y-2">
                      {scannedFiles.map((file, idx) => (
                        <div 
                          key={idx}
                          className="group flex items-start gap-2 p-2 rounded-lg hover:bg-white/5 transition-colors cursor-default"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5 text-green-500/70 mt-0.5 shrink-0" />
                          <span className="text-xs text-muted-foreground group-hover:text-white/90 break-all transition-colors">
                            {file}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="text-center py-8 text-muted-foreground/40 text-xs">
                      暂无引用来源
                    </div>
                  )}
                </div>
                
                {/* Agent Trajectory Section (only show in Agent mode) */}
                {chatMode === 'agent' && lastTrajectory && lastTrajectory.length > 0 && (
                  <>
                    <div className="p-4 border-t border-white/5">
                      <h3 className="text-sm font-medium text-white/80 flex items-center gap-2">
                        <Bot className="w-4 h-4 text-purple-400" />
                        Agent 探索轨迹
                      </h3>
                    </div>
                    <div className="flex-1 overflow-y-auto p-4 custom-scrollbar max-h-64">
                      <div className="space-y-2">
                        {lastTrajectory.map((step, idx) => (
                          <div 
                            key={idx}
                            className={cn(
                              "flex items-start gap-2 p-2 rounded-lg transition-colors",
                              step.success ? "bg-white/[0.02]" : "bg-red-500/5"
                            )}
                          >
                            <div className={cn(
                              "w-5 h-5 rounded flex items-center justify-center shrink-0 mt-0.5",
                              step.success ? "bg-purple-500/20 text-purple-300" : "bg-red-500/20 text-red-300"
                            )}>
                              {step.tool === 'rag_search' && <Search className="w-3 h-3" />}
                              {step.tool === 'code_graph' && <GitBranch className="w-3 h-3" />}
                              {step.tool === 'file_read' && <FileCode className="w-3 h-3" />}
                              {step.tool === 'repo_map' && <Map className="w-3 h-3" />}
                              {!['rag_search', 'code_graph', 'file_read', 'repo_map'].includes(step.tool) && (
                                <Sparkles className="w-3 h-3" />
                              )}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="text-[10px] font-mono text-zinc-500">
                                Step {step.step}: {step.tool}
                              </div>
                              <div className="text-xs text-zinc-400 truncate">
                                {step.description}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </>
                )}
              </aside>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
