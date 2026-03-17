'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  ArrowUp, 
  Loader2, 
  Sparkles,
  X,
  Bot,
  History,
  MessageSquare,
  Plus,
  ChevronRight,
  Trash2
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { api, normalizeRepoUrl, ChatMessage, ChatResponse, AgentChatResponse, ChatMode, ChatHistoryItem, AgentTrajectoryStep } from '@/lib/api';
import { MessageItem, DisplayMessage } from './MessageItem';
import SourcesPanel, { parseSource } from './SourcesPanel';
import CodeViewer from './CodeViewer';

interface ChatInterfaceProps {
  userId: string;
  repoUrl: string;
  currentPageContext?: string;
  currentPageTitle?: string;
  initialChatId?: string;
  onChatLoaded?: () => void;
}

export default function ChatInterface({ 
  userId,
  repoUrl, 
  currentPageContext,
  currentPageTitle,
  initialChatId,
  onChatLoaded,
}: ChatInterfaceProps) {
  const [mode, setMode] = useState<'closed' | 'open'>('closed');
  const [chatMode, setChatMode] = useState<ChatMode>('rag');
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [currentRepoUrl, setCurrentRepoUrl] = useState(repoUrl);

  useEffect(() => {
    setCurrentRepoUrl(repoUrl);
  }, [repoUrl]);

  useEffect(() => {
    const savedMode = localStorage.getItem('chat_mode_preference');
    if (savedMode === 'agent' || savedMode === 'rag') {
      setChatMode(savedMode);
    }
  }, []);

  const handleSetChatMode = (newMode: ChatMode) => {
    setChatMode(newMode);
    localStorage.setItem('chat_mode_preference', newMode);
  };
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState<string>('');
  const [chatId, setChatId] = useState<string | undefined>(undefined);
  const [liveAgentLogs, setLiveAgentLogs] = useState<string[]>([]);
  const [chatHistory, setChatHistory] = useState<ChatHistoryItem[]>([]);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const [showHistorySidebar, setShowHistorySidebar] = useState(true);
  const [deletingChatId, setDeletingChatId] = useState<string | null>(null);

  // Code Viewer state removed (moved to MessageItem)

  // Resizable state
  const [sidebarWidth, setSidebarWidth] = useState(760);
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    const savedWidth = localStorage.getItem('chat_sidebar_width');
    const maxWidth = Math.floor(window.innerWidth * 0.9);
    const minWidth = Math.floor(window.innerWidth * 0.5);
    if (savedWidth) {
      setSidebarWidth(Math.min(Math.max(parseInt(savedWidth, 10), minWidth), maxWidth));
    }
  }, []);

  const startResizing = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  const stopResizing = useCallback(() => {
    setIsResizing(false);
    localStorage.setItem('chat_sidebar_width', sidebarWidth.toString());
  }, [sidebarWidth]);

  const resize = useCallback(
    (mouseMoveEvent: MouseEvent) => {
      if (isResizing) {
        const newWidth = window.innerWidth - mouseMoveEvent.clientX;
        const maxWidth = Math.floor(window.innerWidth * 0.9);
        const minWidth = Math.floor(window.innerWidth * 0.5);
        if (newWidth >= minWidth && newWidth <= maxWidth) {
          setSidebarWidth(newWidth);
        }
      }
    },
    [isResizing]
  );

  useEffect(() => {
    if (isResizing) {
      window.addEventListener("mousemove", resize);
      window.addEventListener("mouseup", stopResizing);
    }
    return () => {
      window.removeEventListener("mousemove", resize);
      window.removeEventListener("mouseup", stopResizing);
    };
  }, [isResizing, resize, stopResizing]);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fullViewInputRef = useRef<HTMLTextAreaElement>(null);

  const scrollToBottom = useCallback(() => {
    if (mode === 'open') {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [mode]);

  useEffect(() => {
    scrollToBottom();
  }, [messages, mode, scrollToBottom]);

  useEffect(() => {
    if (mode === 'open' && fullViewInputRef.current) {
      fullViewInputRef.current.focus();
    }
  }, [mode]);

  useEffect(() => {
    setChatId(undefined);
    setMessages([]);
    loadChatHistory();
  }, [repoUrl, userId]);

  useEffect(() => {
    if (!initialChatId) return;
    const loadAndOpen = async () => {
      try {
        const chatMessages = await api.getChatMessages(initialChatId);
        const displayMessages: DisplayMessage[] = chatMessages.map(msg => {
          const meta = msg.metadata || {};
          return {
            id: msg.id,
            role: msg.role,
            content: msg.content,
            timestamp: new Date(msg.created_at),
            mode: (meta.mode as ChatMode) || 'rag',
            sources: (meta.sources as string[]) || [],
            mermaid: (meta.mermaid as string) || null,
            trajectory: (meta.trajectory as unknown as AgentTrajectoryStep[]) || [],
          };
        });
        setMessages(displayMessages);
        setChatId(initialChatId);

        setMode('open');
      } catch (error) {
        console.error('Failed to load initial chat:', error);
      } finally {
        onChatLoaded?.();
      }
    };
    void loadAndOpen();
  }, [initialChatId]);

  const loadChatHistory = useCallback(async () => {
    if (!userId) return;
    setIsLoadingHistory(true);
    try {
      const history = await api.getChatHistory(userId);
      const normalizedRepo = normalizeRepoUrl(repoUrl);
      const repoHistory = history.filter(
        h => normalizeRepoUrl(h.repo_url) === normalizedRepo
      );
      setChatHistory(repoHistory);
    } catch (error) {
      console.error('Failed to load chat history:', error);
    } finally {
      setIsLoadingHistory(false);
    }
  }, [userId, repoUrl]);

  const handleLoadChat = async (historyItem: ChatHistoryItem) => {
    const effectiveChatId = historyItem.chat_id ?? historyItem.id;
    setIsChatLoading(true);
    setMessages([]);
    setCurrentRepoUrl(historyItem.repo_url);
    try {
      const chatMessages = await api.getChatMessages(effectiveChatId);
      const displayMessages: DisplayMessage[] = chatMessages.map(msg => {
        const meta = msg.metadata || {};
        return {
          id: msg.id,
          role: msg.role,
          content: msg.content,
          timestamp: new Date(msg.created_at),
          mode: (meta.mode as ChatMode) || 'rag',
          sources: (meta.sources as string[]) || [],
          mermaid: (meta.mermaid as string) || null,
          trajectory: (meta.trajectory as unknown as AgentTrajectoryStep[]) || [],
        };
      });
      setMessages(displayMessages);
      setChatId(effectiveChatId);
      
      if (mode === 'closed') {
        setMode('open');
      }
    } catch (error) {
      console.error('Failed to load chat messages:', error);
    } finally {
      setIsChatLoading(false);
    }
  };

  const handleNewChat = () => {
    setChatId(undefined);
    setMessages([]);
    if (fullViewInputRef.current) {
      fullViewInputRef.current.focus();
    }
  };

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

    if (mode === 'closed') {
      setMode('open');
    }

    setInputValue('');
    
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
    setLiveAgentLogs([]);
    setLoadingStatus(chatMode === 'agent' ? '分析问题意图...' : 'Thinking...');

    try {
      if (chatMode === 'agent') {
        // Agent mode (streaming)
        setLoadingStatus('Agent 正在规划探索路径...');

        const getToolFriendlyName = (toolName: string): string => {
          const toolNameMap: Record<string, string> = {
            'rag_search': '文档检索',
            'code_graph': '代码结构分析',
            'file_read': '文件内容读取',
            'repo_map': '仓库结构扫描',
          };
          return toolNameMap[toolName] || toolName;
        };

        const appendLiveLog = (line: string) => {
          setLiveAgentLogs(prev => [...prev.slice(-5), line]);
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
            const status = String(event.data.status || '');
            const friendlyStatus = status === 'analyzing' 
              ? '正在理解您的问题...' 
              : status === 'planned' 
                ? '已制定探索策略' 
                : '正在分析问题...';
            setLoadingStatus(friendlyStatus);
            appendLiveLog(friendlyStatus);
            continue;
          }

          if (event.type === 'tool_call') {
            const status = String(event.data.status || '');
            if (status === 'start') {
              const tools = Array.isArray(event.data.tools) ? event.data.tools : [];
              const friendlyNames = tools
                .map((item) => {
                  if (item && typeof item === 'object' && 'tool' in item) {
                    return getToolFriendlyName(String(item.tool));
                  }
                  return null;
                })
                .filter(Boolean);
              const line = friendlyNames.length > 0 
                ? `正在${friendlyNames.join('、')}...` 
                : '正在收集相关信息...';
              setLoadingStatus(line);
              appendLiveLog(line);
            } else if (status === 'done') {
              const line = '已获取相关代码上下文';
              setLoadingStatus(line);
              appendLiveLog(line);
            } else {
              const line = '正在深入分析代码...';
              setLoadingStatus(line);
              appendLiveLog(line);
            }
            continue;
          }

          if (event.type === 'evaluation') {
            const status = String(event.data.status || '');
            if (status === 'done') {
              const isSufficient = Boolean(event.data.is_sufficient);
              const line = isSufficient 
                ? '已收集到足够的信息' 
                : '正在补充更多相关代码...';
              setLoadingStatus(line);
              appendLiveLog(line);
            }
            continue;
          }

          if (event.type === 'synthesis') {
            const line = String(event.data.status || '') === 'done'
              ? '正在整理答案...'
              : '正在生成答案...';
            setLoadingStatus(line);
            appendLiveLog(line);
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
        loadChatHistory();

        const assistantMessage: DisplayMessage = {
          id: generateId(),
          role: 'assistant',
          content: finalResponse.answer,
          timestamp: new Date(),
          sources: finalResponse.sources,
          mode: 'agent',
          mermaid: finalResponse.mermaid,
          trajectory: finalResponse.trajectory,
        };
        setMessages(prev => [...prev, assistantMessage]);
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
        loadChatHistory();

        const assistantMessage: DisplayMessage = {
          id: generateId(),
          role: 'assistant',
          content: response.answer,
          timestamp: new Date(),
          sources: response.sources,
          mode: 'rag',
        };
        setMessages(prev => [...prev, assistantMessage]);
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

  const handleDeleteChat = async (historyItem: ChatHistoryItem, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!userId || !window.confirm('确定要删除这个对话吗？此操作无法撤销。')) return;

    const effectiveChatId = historyItem.chat_id ?? historyItem.id;
    setDeletingChatId(effectiveChatId);
    
    try {
      await api.deleteChatHistory(effectiveChatId, userId);
      setChatHistory(prev => prev.filter(item => (item.chat_id ?? item.id) !== effectiveChatId));
      if (chatId === effectiveChatId) {
        handleNewChat();
      }
    } catch (error) {
      console.error('Failed to delete chat:', error);
      alert('删除失败，请重试');
    } finally {
      setDeletingChatId(null);
    }
  };

  const repoName = repoUrl.split('/').slice(-2).join('/');

  return (
    <>
      <AnimatePresence>
        {mode === 'closed' && (
          <motion.div
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.8, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed bottom-8 right-8 z-40"
          >
            <button
              onClick={() => setMode('open')}
              className="inline-flex items-center gap-2.5 rounded-full border border-fuchsia-300/40 bg-black/80 px-5 py-3.5 text-base font-medium text-fuchsia-100 shadow-xl shadow-fuchsia-500/20 backdrop-blur-xl hover:bg-black hover:scale-105 hover:shadow-fuchsia-500/40 transition-all duration-300"
              aria-label="打开聊天侧边栏"
            >
              <MessageSquare className="w-5 h-5" />
              Chat
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {mode === 'open' && (
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.2 }}
            className="fixed top-0 right-0 bottom-0 z-50 bg-[#0A0A0A] border-l border-white/10 flex flex-col shadow-[-20px_0_60px_rgba(0,0,0,0.45)]"
            style={{ width: sidebarWidth }}
          >
            {/* Drag Handle */}
            <div
              className="absolute left-0 top-0 bottom-0 w-1 cursor-ew-resize hover:bg-blue-500/50 z-[60] transition-colors"
              onMouseDown={startResizing}
            />

            <header className="h-14 border-b border-white/10 flex items-center justify-between px-4 bg-white/[0.02]">
              <div className="flex items-center gap-4">
                <button
                  onClick={() => setShowHistorySidebar((prev) => !prev)}
                  className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:text-white transition-colors"
                  aria-label="切换历史侧栏"
                >
                  <History className="w-4 h-4" />
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
                <button 
                  onClick={() => setMode('closed')}
                  className="p-2 text-muted-foreground hover:text-white transition-colors"
                  aria-label="Close chat"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </header>

            <div className="flex-1 flex overflow-hidden">
              {/* 左侧历史记录侧边栏 */}
              <aside className={cn(
                "w-64 border-r border-white/10 bg-[#0A0A0A] flex-col transition-all duration-300",
                showHistorySidebar ? "hidden sm:flex" : "hidden"
              )}>
                <div className="p-3 border-b border-white/5 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-white/80 flex items-center gap-2">
                    <History className="w-4 h-4 text-purple-400" />
                    对话历史
                  </h3>
                  <button
                    onClick={handleNewChat}
                    className="p-1.5 rounded-lg bg-purple-500/20 text-purple-300 hover:bg-purple-500/30 active:scale-95 transition-all"
                    aria-label="新建对话"
                  >
                    <Plus className="w-4 h-4" />
                  </button>
                </div>
                <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-1">
                  {isLoadingHistory ? (
                    <div className="flex items-center justify-center py-8">
                      <Loader2 className="w-5 h-5 text-zinc-500 animate-spin" />
                    </div>
                  ) : chatHistory.length === 0 ? (
                    <div className="text-center py-8">
                      <MessageSquare className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
                      <p className="text-xs text-zinc-500">暂无对话记录</p>
                      <p className="text-xs text-zinc-600 mt-1">开始提问后将保存对话</p>
                    </div>
                  ) : (
                    chatHistory.map((item) => {
                      const effectiveChatId = item.chat_id ?? item.id;
                      return (
                      <button
                        key={item.id}
                        onClick={() => handleLoadChat(item)}
                        className={cn(
                          "w-full text-left p-2.5 rounded-lg transition-all group",
                          chatId === effectiveChatId
                            ? "bg-purple-500/20 border border-purple-500/30"
                            : "hover:bg-white/5 border border-transparent"
                        )}
                        aria-label={`加载对话 ${item.title || effectiveChatId.slice(0, 8)}`}
                      >
                        <div className="flex items-center gap-2">
                          <MessageSquare className={cn(
                            "w-4 h-4 shrink-0",
                            chatId === effectiveChatId ? "text-purple-400" : "text-zinc-500 group-hover:text-zinc-400"
                          )} />
                          <span className={cn(
                            "text-sm truncate flex-1",
                            chatId === effectiveChatId ? "text-purple-200" : "text-zinc-400 group-hover:text-zinc-300"
                          )}>
                            {item.title || `对话 ${effectiveChatId.slice(0, 8)}`}
                          </span>
                          
                          {/* Delete Button */}
                          <button
                            onClick={(e) => handleDeleteChat(item, e)}
                            className="p-1 rounded text-zinc-500 hover:text-red-400 hover:bg-white/10 opacity-0 group-hover:opacity-100 transition-all"
                            disabled={deletingChatId === effectiveChatId}
                          >
                            {deletingChatId === effectiveChatId ? (
                              <Loader2 className="w-3 h-3 animate-spin" />
                            ) : (
                              <Trash2 className="w-3 h-3" />
                            )}
                          </button>
                        </div>
                        <div className="mt-1 text-[10px] text-zinc-600 ml-6">
                          {new Date(item.created_at).toLocaleDateString('zh-CN', { 
                            month: 'short', 
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit'
                          })}
                        </div>
                      </button>
                    );
                    })
                  )}
                </div>
              </aside>

              {/* 中间聊天区域 */}
              <div className="flex-1 flex flex-col min-w-0 relative">
                <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-8 custom-scrollbar">
                  {isChatLoading && (
                    <div className="flex flex-col items-center justify-center h-full text-center">
                      <Loader2 className="w-8 h-8 text-purple-400 animate-spin mb-4" />
                      <p className="text-sm text-zinc-500">正在加载对话...</p>
                    </div>
                  )}
                  {messages.length === 0 && !isLoading && !isChatLoading && (
                    <div className="flex flex-col items-center justify-center h-full text-center">
                      <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-purple-500/20 to-blue-500/20 flex items-center justify-center mb-4">
                        {chatMode === 'agent' ? (
                          <Bot className="w-8 h-8 text-purple-400" />
                        ) : (
                          <Sparkles className="w-8 h-8 text-blue-400" />
                        )}
                      </div>
                      <h2 className="text-xl font-semibold text-white/90 mb-2">
                        {chatMode === 'agent' ? 'Agent 模式' : '快速问答'}
                      </h2>
                      <p className="text-sm text-zinc-500 max-w-md">
                        {chatMode === 'agent' 
                          ? '我会深入分析代码结构，追踪调用链，为您提供全面的代码理解'
                          : '快速搜索文档和代码，回答您的问题'}
                      </p>
                    </div>
                  )}
                  {messages.map((message) => (
                    <div key={message.id} className="max-w-3xl mx-auto">
                      <MessageItem message={message} repoUrl={currentRepoUrl} />
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
                  <div className="max-w-3xl mx-auto relative space-y-3">
                    <div className="relative bg-white/5 border border-white/10 rounded-xl overflow-hidden focus-within:ring-1 focus-within:ring-blue-500/50 transition-all">
                      <textarea
                        ref={fullViewInputRef}
                        value={inputValue}
                        onChange={handleInputChange}
                        onKeyDown={handleKeyDown}
                        placeholder={chatMode === 'agent' ? "Ask Agent to analyze code..." : "Ask a question..."}
                        className="w-full bg-transparent border-none outline-none text-sm text-white placeholder:text-muted-foreground resize-none px-4 py-3 max-h-[200px]"
                        rows={1}
                        disabled={isLoading}
                        aria-label="Follow-up question"
                      />
                      <div className="flex items-center justify-between px-2 pb-2">
                        {/* Mode Switcher inside Input Area */}
                        <div className="flex items-center gap-1">
                          <button
                            onClick={() => handleSetChatMode('rag')}
                            className={cn(
                              "flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all border",
                              chatMode === 'rag'
                                ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
                                : "text-zinc-500 border-transparent hover:text-zinc-300 hover:bg-white/5"
                            )}
                            title="快速回答模式"
                          >
                            <Sparkles className="w-3 h-3" />
                            <span>Chat</span>
                          </button>
                          <button
                            onClick={() => handleSetChatMode('agent')}
                            className={cn(
                              "flex items-center gap-1.5 px-2 py-1 rounded-md text-[10px] font-medium transition-all border",
                              chatMode === 'agent'
                                ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
                                : "text-zinc-500 border-transparent hover:text-zinc-300 hover:bg-white/5"
                            )}
                            title="Agent 深度模式"
                          >
                            <Bot className="w-3 h-3" />
                            <span>Agent</span>
                          </button>
                        </div>

                        <div className="flex items-center gap-2">
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
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
