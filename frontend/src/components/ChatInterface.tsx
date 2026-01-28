'use client';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  ArrowUp, 
  Loader2, 
  FileText,
  Sparkles,
  Zap,
  ChevronRight,
  ArrowLeft,
  X,
  CheckCircle2
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { api, ChatMessage, ChatResponse } from '@/lib/api';

interface ChatInterfaceProps {
  repoUrl: string;
  currentPageContext?: string;
  currentPageTitle?: string;
}

interface DisplayMessage extends ChatMessage {
  id: string;
  timestamp: Date;
  sources?: string[];
  isError?: boolean;
}

export default function ChatInterface({ 
  repoUrl, 
  currentPageContext,
  currentPageTitle 
}: ChatInterfaceProps) {
  // mode: 'bar' (initial floating bar) | 'full' (full screen chat interface)
  const [mode, setMode] = useState<'bar' | 'full'>('bar');
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [inputValue, setInputValue] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scannedFiles, setScannedFiles] = useState<string[]>([]);
  
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
  }, [messages, scrollToBottom, mode]);

  // Focus input when switching modes
  useEffect(() => {
    if (mode === 'full' && fullViewInputRef.current) {
      fullViewInputRef.current.focus();
    } else if (mode === 'bar' && inputRef.current) {
      // Optional: focus bar input if needed
    }
  }, [mode]);

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

    // If in bar mode, switch to full mode immediately
    if (mode === 'bar') {
      setMode('full');
    }

    setError(null);
    setInputValue('');
    
    // Reset height
    if (inputRef.current) inputRef.current.style.height = 'auto';
    if (fullViewInputRef.current) fullViewInputRef.current.style.height = 'auto';

    const userMessage: DisplayMessage = {
      id: generateId(),
      role: 'user',
      content: question,
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMessage]);

    setIsLoading(true);
    // Clear previous scanned files for new query or append? 
    // Usually per-query sources are relevant. Let's reset for the "current thinking" context.
    setScannedFiles([]); 

    try {
      const conversationHistory = buildConversationHistory();
      
      const response: ChatResponse = await api.askQuestion({
        question,
        repo_url: repoUrl,
        conversation_history: conversationHistory,
        current_page_context: currentPageContext,
      });

      const assistantMessage: DisplayMessage = {
        id: generateId(),
        role: 'assistant',
        content: response.answer,
        timestamp: new Date(),
        sources: response.sources,
      };
      setMessages(prev => [...prev, assistantMessage]);
      
      if (response.sources) {
        setScannedFiles(response.sources);
      }

    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to send message';
      setError(errorMessage);
      
      const errorDisplayMessage: DisplayMessage = {
        id: generateId(),
        role: 'assistant',
        content: errorMessage,
        timestamp: new Date(),
        isError: true,
      };
      setMessages(prev => [...prev, errorDisplayMessage]);
    } finally {
      setIsLoading(false);
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

  const handleCloseFullView = () => {
    setMode('bar');
    // Optional: Clear messages if you want a fresh start every time, 
    // but keeping history is usually better UX.
    // setMessages([]); 
  };

  // Extract repo name for display
  const repoName = repoUrl.split('/').slice(-2).join('/');

  return (
    <>
      {/* 
        INITIAL FLOATING BAR 
        Visible when mode === 'bar'
      */}
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
              <div className="absolute inset-0 bg-gradient-to-r from-blue-500/20 to-purple-500/20 rounded-2xl blur-xl opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
              <div className="relative bg-[#0A0A0A]/80 backdrop-blur-xl border border-white/10 rounded-2xl shadow-2xl overflow-hidden flex flex-col transition-all focus-within:ring-1 focus-within:ring-blue-500/50">
                
                {/* Input Area */}
                <div className="flex items-end gap-2 p-3">
                  <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-blue-500/10 mb-1 shrink-0">
                    <Sparkles className="w-4 h-4 text-blue-400" />
                  </div>
                  <textarea
                    ref={inputRef}
                    value={inputValue}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    placeholder={`Ask Devin about ${repoName}...`}
                    className="flex-1 bg-transparent border-none outline-none text-sm text-white placeholder:text-muted-foreground resize-none py-2 max-h-[120px]"
                    rows={1}
                  />
                  <button
                    onClick={handleSendMessage}
                    disabled={!inputValue.trim()}
                    className={cn(
                      "p-2 rounded-lg transition-all mb-0.5",
                      inputValue.trim()
                        ? "bg-blue-600 text-white hover:bg-blue-500 shadow-lg shadow-blue-500/20"
                        : "bg-white/5 text-muted-foreground cursor-not-allowed"
                    )}
                  >
                    <ArrowUp className="w-4 h-4" />
                  </button>
                </div>

                {/* Footer Badges */}
                <div className="px-4 pb-2 flex items-center gap-3 text-[10px] text-muted-foreground/70">
                  <div className="flex items-center gap-1">
                    <Zap className="w-3 h-3 text-yellow-500/70" />
                    <span>Fast Model</span>
                  </div>
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

      {/* 
        FULL SCREEN CHAT INTERFACE 
        Visible when mode === 'full'
      */}
      <AnimatePresence>
        {mode === 'full' && (
          <motion.div
            initial={{ opacity: 0, scale: 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.98 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-50 bg-[#0A0A0A] flex flex-col"
          >
            {/* Header */}
            <header className="h-14 border-b border-white/10 flex items-center justify-between px-4 bg-white/[0.02]">
              <div className="flex items-center gap-4">
                <button 
                  onClick={handleCloseFullView}
                  className="flex items-center gap-2 text-sm text-muted-foreground hover:text-white transition-colors"
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
              <div className="flex items-center gap-2">
                 <button 
                  onClick={handleCloseFullView}
                  className="p-2 text-muted-foreground hover:text-white transition-colors"
                 >
                   <X className="w-5 h-5" />
                 </button>
              </div>
            </header>

            {/* Main Content Grid */}
            <div className="flex-1 flex overflow-hidden">
              
              {/* Left Column: Chat History */}
              <div className="flex-1 flex flex-col min-w-0 relative">
                <div className="flex-1 overflow-y-auto p-4 md:p-8 space-y-8 custom-scrollbar">
                  {messages.map((message) => (
                    <div key={message.id} className="max-w-3xl mx-auto">
                      <MessageItem message={message} />
                    </div>
                  ))}
                  
                  {isLoading && (
                    <div className="max-w-3xl mx-auto pl-12">
                      <div className="flex items-center gap-2 text-muted-foreground text-sm animate-pulse">
                        <Loader2 className="w-4 h-4 animate-spin" />
                        <span>Thinking...</span>
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} className="h-4" />
                </div>

                {/* Bottom Input Bar (in full view) */}
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
                      />
                      <div className="absolute bottom-2 right-2 flex items-center gap-2">
                        <button
                          onClick={handleSendMessage}
                          disabled={!inputValue.trim() || isLoading}
                          className={cn(
                            "p-1.5 rounded-lg transition-all",
                            inputValue.trim() && !isLoading
                              ? "bg-blue-600 text-white hover:bg-blue-500"
                              : "bg-white/5 text-muted-foreground cursor-not-allowed"
                          )}
                        >
                          <ArrowUp className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                    <div className="mt-2 text-center">
                      <p className="text-[10px] text-muted-foreground/50">
                        AI can make mistakes. Check important info.
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Right Column: Scanned Files / Context */}
              <div className="hidden lg:flex w-80 border-l border-white/10 bg-white/[0.02] flex-col">
                <div className="p-4 border-b border-white/5">
                  <h3 className="text-sm font-medium text-white/80 flex items-center gap-2">
                    <FileText className="w-4 h-4 text-blue-400" />
                    Relevant Sources
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
                      No sources scanned yet.
                    </div>
                  )}
                </div>
              </div>

            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

function MessageItem({ message }: { message: DisplayMessage }) {
  const isUser = message.role === 'user';
  
  return (
    <div className={cn("flex gap-4 group", isUser ? "flex-row-reverse" : "flex-row")}>
      {/* Avatar */}
      <div className={cn(
        "w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-1",
        isUser ? "bg-white/10" : "bg-gradient-to-br from-blue-500/20 to-purple-500/20"
      )}>
        {isUser ? (
          <div className="w-4 h-4 rounded-full bg-white/50" />
        ) : (
          <Sparkles className="w-4 h-4 text-blue-400" />
        )}
      </div>
      
      {/* Content */}
      <div className={cn("flex-1 min-w-0 space-y-1", isUser && "text-right")}>
        <div className="flex items-center gap-2 mb-1">
          <span className={cn("text-sm font-medium", isUser ? "text-white/90 ml-auto" : "text-blue-400")}>
            {isUser ? "You" : "Devin"}
          </span>
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
      </div>
    </div>
  );
}
