'use client';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';
import { ChatMessage } from '@/lib/api';

export interface DisplayMessage extends ChatMessage {
  id: string;
  timestamp: Date;
  sources?: string[];
  isError?: boolean;
}

interface MessageItemProps {
  message: DisplayMessage;
}

export const MessageItem = React.memo(({ message }: MessageItemProps) => {
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
});

MessageItem.displayName = 'MessageItem';
