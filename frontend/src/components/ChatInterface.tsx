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
import { api, normalizeRepoUrl, ChatMessage, ChatResponse, AgentChatResponse, ChatMode, ChatHistoryItem, AgentTrajectoryStep, LiveToolStep } from '@/lib/api';
import { MessageItem, DisplayMessage } from './MessageItem';
import SourcesPanel, { parseSource } from './SourcesPanel';
import CodeViewer from './CodeViewer';
import { LiveStepFlow, LiveStep } from './LiveStepFlow';
import { t } from '@/lib/i18n';
import { useAuth } from '@/providers/AuthProvider';

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
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState<string>('');
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
    setLiveSteps([]);
    setStreamingAnswer('');
    setLoadingStatus(chatMode === 'agent' ? t('analyzingStatus') : t('retrievingDocs'));

    // Helper to add a live step
    const addStep = (step: LiveStep) => {
      setLiveSteps(prev => {
        const existing = prev.find(s => s.id === step.id);
        if (existing) {
          return prev.map(s => s.id === step.id ? step : s);
        }
        return [...prev, step];
      });
    };

    // Helper to update step status
    const updateStep = (id: string, updates: Partial<LiveStep>) => {
      setLiveSteps(prev => prev.map(s => s.id === id ? { ...s, ...updates } : s));
    };

    try {
      if (chatMode === 'agent') {
        // Agent mode (streaming with detailed steps)
        let stepCounter = 0;

        addStep({
          id: 'planning',
          type: 'planning',
          status: 'running',
          title: t('analyzingIntent'),
          description: t('analyzingIntentDesc'),
        });

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
            const intent = String(event.data.intent || '');
            const entities = (event.data.entities as string[]) || [];
            
            if (status === 'planned' || status === 'direct_answer') {
              updateStep('planning', {
                status: 'done',
                title: t('analysisDone'),
                description: intent ? `${t('intentLabel')}: ${intent}` : undefined,
                details: entities.length > 0 ? `${t('keyEntities')}: ${entities.slice(0, 3).join(', ')}` : undefined,
              });
            } else if (status === 'analyzing') {
              updateStep('planning', {
                description: t('understandingQuestion'),
              });
            }
            continue;
          }

          if (event.type === 'tool_call') {
            const status = String(event.data.status || '');
            const iteration = Number(event.data.iteration || 1);
            const tools = (event.data.tools as Array<{ tool: string; description?: string; query?: string; pattern?: string; file_path?: string; symbol_name?: string; operation?: string }>)|| [];
            
            if (status === 'start') {
              // Add new steps for each tool
              for (const toolInfo of tools) {
                stepCounter++;
                const stepId = `tool_${iteration}_${stepCounter}`;
                addStep({
                  id: stepId,
                  type: 'tool',
                  status: 'running',
                  title: toolInfo.description || toolInfo.tool,
                  description: toolInfo.query || toolInfo.pattern || toolInfo.file_path || toolInfo.symbol_name || undefined,
                  tool: {
                    tool: toolInfo.tool,
                    description: toolInfo.description || '',
                    query: toolInfo.query,
                    pattern: toolInfo.pattern,
                    file_path: toolInfo.file_path,
                    symbol_name: toolInfo.symbol_name,
                    operation: toolInfo.operation,
                    status: 'running',
                  },
                });
              }
            } else if (status === 'done') {
              const elapsedMs = Number(event.data.elapsed_ms || 0);
              const results = (event.data.results as Array<{
                tool: string;
                success: boolean;
                duration_ms?: number;
                metrics?: Record<string, unknown>;
              }>) || [];
              
              // Update all running tool steps to done
              setLiveSteps(prev => prev.map(s => {
                if (s.type === 'tool' && s.status === 'running') {
                  const result = results.find(r => s.tool?.tool === r.tool);
                  const perToolMs = typeof result?.duration_ms === 'number' ? result.duration_ms : elapsedMs;
                  return {
                    ...s,
                    status: 'done' as const,
                    elapsed_ms: perToolMs,
                    tool: s.tool ? {
                      ...s.tool,
                      status: 'done' as const,
                      success: result?.success ?? true,
                      elapsed_ms: perToolMs,
                      metrics: result?.metrics,
                    } : undefined,
                  };
                }
                return s;
              }));
            }
            continue;
          }

          if (event.type === 'evaluation') {
            const status = String(event.data.status || '');
            const iteration = Number(event.data.iteration || 1);
            
            if (status === 'start') {
              addStep({
                id: `eval_${iteration}`,
                type: 'evaluation',
                status: 'running',
                title: t('evaluatingInfo'),
                description: t('evaluatingDesc'),
              });
            } else if (status === 'done') {
              const isSufficient = Boolean(event.data.is_sufficient);
              const confidence = String(event.data.confidence_level || '');
              const missingCount = Number(event.data.missing_count || 0);
              
              updateStep(`eval_${iteration}`, {
                status: 'done',
                title: isSufficient ? t('infoSufficient') : t('needMoreInfo'),
                description: confidence ? `${t('confidenceLabel')}: ${confidence}` : undefined,
                details: missingCount > 0 ? t('missingItems', { n: missingCount }) : undefined,
              });
            }
            continue;
          }

          if (event.type === 'synthesis') {
            const status = String(event.data.status || '');
            
            if (status === 'start') {
              addStep({
                id: 'synthesis',
                type: 'synthesis',
                status: 'running',
                title: t('synthesisingAnswer'),
                description: t('synthesisingDesc'),
              });
            } else if (status === 'done') {
              updateStep('synthesis', {
                status: 'done',
                title: t('answerDone'),
              });
            }
            continue;
          }

          if (event.type === 'answer_delta') {
            const delta = String(event.data.delta || '');
            setStreamingAnswer(prev => prev + delta);
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
          throw new Error(t('agentNoResult'));
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
          isNew: false, // Don't use typewriter effect since we already streamed
        };
        setMessages(prev => [...prev, assistantMessage]);
        
      } else {
        // RAG mode with streaming
        addStep({
          id: 'retrieval',
          type: 'retrieval',
          status: 'running',
          title: t('retrievalTitle'),
          description: t('retrievalDesc'),
        });

        let finalAnswer = '';
        let finalSources: string[] = [];
        let responseChatId = chatId;

        try {
          for await (const event of api.askQuestionStream({
            user_id: userId,
            question,
            repo_url: repoUrl,
            chat_id: chatId,
            conversation_history: buildConversationHistory(),
            current_page_context: currentPageContext,
          })) {
            if (event.type === 'retrieval_start') {
              updateStep('retrieval', {
                description: `${t('retrievalQuery')}: ${String(event.data.query || '').slice(0, 50)}...`,
              });
              continue;
            }

            if (event.type === 'hyde_generated') {
              addStep({
                id: 'hyde',
                type: 'hyde',
                status: 'done',
                title: t('hydeTitle'),
                description: t('hydeDesc'),
              });
              continue;
            }

            if (event.type === 'retrieval_done') {
              const docCount = Number(event.data.doc_count || 0);
              const sources = (event.data.sources as string[]) || [];
              updateStep('retrieval', {
                status: 'done',
                title: t('foundDocs', { n: docCount }),
                description: sources.length > 0 ? sources.slice(0, 2).join(', ') : undefined,
              });
              
              addStep({
                id: 'generation',
                type: 'synthesis',
                status: 'running',
                title: t('generatingAnswer'),
                description: t('generatingAnswerDesc'),
              });
              continue;
            }

            if (event.type === 'answer_delta') {
              const delta = String(event.data.delta || '');
              finalAnswer += delta;
              setStreamingAnswer(prev => prev + delta);
              continue;
            }

            if (event.type === 'answer_done') {
              finalAnswer = String(event.data.answer || finalAnswer);
              finalSources = (event.data.sources as string[]) || [];
              updateStep('generation', {
                status: 'done',
                title: t('answerDone'),
              });
              continue;
            }

            if (event.type === 'complete') {
              responseChatId = String(event.data.chat_id || chatId);
              finalSources = (event.data.sources as string[]) || finalSources;
              continue;
            }

            if (event.type === 'error') {
              throw new Error(String(event.data.error || 'RAG stream failed'));
            }
          }

          setChatId(responseChatId);
          loadChatHistory();

          const assistantMessage: DisplayMessage = {
            id: generateId(),
            role: 'assistant',
            content: finalAnswer,
            timestamp: new Date(),
            sources: finalSources,
            mode: 'rag',
            isNew: false, // Don't use typewriter since we streamed
          };
          setMessages(prev => [...prev, assistantMessage]);
          
        } catch (streamError) {
          // Fallback to non-streaming API if streaming fails
          console.warn('Streaming failed, falling back to blocking API:', streamError);
          
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
            isNew: true,
          };
          setMessages(prev => [...prev, assistantMessage]);
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
      setLiveSteps([]);
      setStreamingAnswer('');
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
    if (!userId || !window.confirm(t('deleteConfirmDialog'))) return;

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
      alert(t('deleteConfirm'));
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
              aria-label="Open chat sidebar"
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
                  aria-label="Toggle history sidebar"
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
              {/* Chat history sidebar */}
              <aside className={cn(
                "w-64 border-r border-white/10 bg-[#0A0A0A] flex-col transition-all duration-300",
                showHistorySidebar ? "hidden sm:flex" : "hidden"
              )}>
                <div className="p-3 border-b border-white/5 flex items-center justify-between">
                  <h3 className="text-sm font-medium text-white/80 flex items-center gap-2">
                    <History className="w-4 h-4 text-purple-400" />
                    {t('chatHistory')}
                  </h3>
                  <button
                    onClick={handleNewChat}
                    className="p-1.5 rounded-lg bg-purple-500/20 text-purple-300 hover:bg-purple-500/30 active:scale-95 transition-all"
                    aria-label="New chat"
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
                      <p className="text-xs text-zinc-500">{t('noChatHistory')}</p>
                      <p className="text-xs text-zinc-600 mt-1">{t('startChatHint')}</p>
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
                        aria-label={`Load chat ${item.title || effectiveChatId.slice(0, 8)}`}
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
                            {item.title || `${t('chatDefault')} ${effectiveChatId.slice(0, 8)}`}
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
                          {new Date(item.created_at).toLocaleDateString('en-US', { 
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

              {/* Main chat area */}
              <div className="flex-1 flex flex-col min-w-0 relative bg-[#050505]">
                <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-8 custom-scrollbar scroll-smooth">
                  {isChatLoading && (
                    <div className="flex flex-col items-center justify-center h-full text-center">
                      <Loader2 className="w-8 h-8 text-purple-400 animate-spin mb-4" />
                      <p className="text-sm text-zinc-500">{t('loadingChat')}</p>
                    </div>
                  )}
                  {messages.length === 0 && !isLoading && !isChatLoading && (
                    <div className="flex flex-col items-center justify-center h-full text-center px-4">
                      <div className="w-16 h-16 rounded-3xl bg-gradient-to-br from-purple-500/10 to-blue-500/10 border border-white/5 flex items-center justify-center mb-6 shadow-xl">
                        {chatMode === 'agent' ? (
                          <Bot className="w-8 h-8 text-purple-400" />
                        ) : (
                          <Sparkles className="w-8 h-8 text-blue-400" />
                        )}
                      </div>
                      <h2 className="text-2xl font-semibold text-white/90 mb-3">
                        {chatMode === 'agent' ? t('agentDeepAnalysis') : t('quickQA')}
                      </h2>
                      <p className="text-[15px] text-zinc-400 max-w-md leading-relaxed">
                        {chatMode === 'agent' 
                          ? t('agentDesc')
                          : t('ragDesc')}
                      </p>
                    </div>
                  )}
                  <div className="space-y-2">
                    {messages.map((message) => (
                      <div key={message.id} className="max-w-3xl mx-auto px-2 md:px-0">
                        <MessageItem message={message} repoUrl={currentRepoUrl} />
                      </div>
                    ))}
                  </div>
                  
                  {isLoading && (
                    <div className="max-w-3xl mx-auto px-2 md:px-0 mt-4">
                      <div className="ml-2 md:ml-12 p-4 rounded-xl bg-white/[0.02] border border-white/5">
                        <LiveStepFlow 
                          steps={liveSteps}
                          isAgent={chatMode === 'agent'}
                          currentPhase={loadingStatus}
                          streamingAnswer={streamingAnswer}
                        />
                        {liveSteps.length === 0 && (
                          <div className={cn(
                            "flex items-center gap-3 text-sm",
                            chatMode === 'agent' ? "text-purple-400" : "text-blue-400"
                          )}>
                            {chatMode === 'agent' ? (
                              <Bot className="w-4 h-4 animate-pulse" />
                            ) : (
                              <Sparkles className="w-4 h-4 animate-pulse" />
                            )}
                            <span className="font-medium tracking-wide">{loadingStatus || 'Thinking...'}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} className="h-4" />
                </div>

                <div className="p-4 md:p-6 bg-gradient-to-t from-[#0A0A0A] via-[#0A0A0A] to-transparent shrink-0">
                  <div className="max-w-3xl mx-auto relative space-y-3">
                    <div className="relative bg-[#1A1A1A] border border-white/10 rounded-2xl overflow-hidden focus-within:ring-2 focus-within:ring-purple-500/30 focus-within:border-purple-500/50 transition-all shadow-lg">
                      <textarea
                        ref={fullViewInputRef}
                        value={inputValue}
                        onChange={handleInputChange}
                        onKeyDown={handleKeyDown}
                        placeholder={chatMode === 'agent' ? "Ask Agent to analyze code..." : "Ask a question..."}
                        className="w-full bg-transparent border-none outline-none text-[15px] text-white placeholder:text-zinc-500 resize-none px-4 py-3.5 max-h-[200px] leading-relaxed"
                        rows={1}
                        disabled={isLoading}
                        aria-label="Follow-up question"
                      />
                      <div className="flex items-center justify-between px-3 pb-3">
                        {/* Mode Switcher inside Input Area */}
                        <div className="flex items-center gap-1.5">
                          <button
                            onClick={() => handleSetChatMode('rag')}
                            className={cn(
                              "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all border",
                              chatMode === 'rag'
                                ? "bg-blue-500/20 text-blue-300 border-blue-500/30"
                                : "text-zinc-500 border-transparent hover:text-zinc-300 hover:bg-white/5"
                            )}
                            title="Quick answer mode"
                          >
                            <Sparkles className="w-3.5 h-3.5" />
                            <span>Chat</span>
                          </button>
                          <button
                            onClick={() => handleSetChatMode('agent')}
                            className={cn(
                              "flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all border",
                              chatMode === 'agent'
                                ? "bg-purple-500/20 text-purple-300 border-purple-500/30"
                                : "text-zinc-500 border-transparent hover:text-zinc-300 hover:bg-white/5"
                            )}
                            title="Agent deep analysis mode"
                          >
                            <Bot className="w-3.5 h-3.5" />
                            <span>Agent</span>
                          </button>
                        </div>

                        <div className="flex items-center gap-2">
                          <button
                            onClick={handleSendMessage}
                            disabled={!inputValue.trim() || isLoading}
                            className={cn(
                              "p-2 rounded-xl transition-all shadow-sm",
                              inputValue.trim() && !isLoading
                                ? chatMode === 'agent'
                                  ? "bg-purple-600 text-white hover:bg-purple-500 hover:shadow-purple-500/25"
                                  : "bg-blue-600 text-white hover:bg-blue-500 hover:shadow-blue-500/25"
                                : "bg-white/5 text-zinc-600 cursor-not-allowed"
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
