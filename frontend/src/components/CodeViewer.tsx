'use client';

import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  X, 
  FileCode, 
  Search, 
  ChevronRight, 
  Maximize2, 
  Minimize2,
  Copy,
  Check,
  FileText
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import ts from 'react-syntax-highlighter/dist/cjs/languages/prism/typescript';
import json from 'react-syntax-highlighter/dist/cjs/languages/prism/json';
import markdown from 'react-syntax-highlighter/dist/cjs/languages/prism/markdown';
import python from 'react-syntax-highlighter/dist/cjs/languages/prism/python';
import bash from 'react-syntax-highlighter/dist/cjs/languages/prism/bash';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/cjs/styles/prism';
import { cn } from '@/lib/utils';
import { api } from '@/lib/api';
import { Loader2 } from 'lucide-react';

// 由于没有安装 react-syntax-highlighter，我们回退使用 react-markdown + rehype-highlight
// 或者更简单，我们直接用 react-markdown 渲染代码块
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';

interface ParsedSource {
  raw: string;
  filePath: string;
  fileName: string;
  extension: string;
  lineRange?: { start: number; end: number };
  symbolName?: string;
  category?: string;
  content?: string; // 如果有预加载的内容
}

interface CodeViewerProps {
  isOpen: boolean;
  onClose: () => void;
  sources: ParsedSource[];
  initialSourceIndex?: number;
  repoUrl: string;
}

const getFileIcon = (extension: string) => {
  const codeExts = ['py', 'ts', 'tsx', 'js', 'jsx', 'java', 'cpp', 'c', 'go', 'rs', 'rb', 'php'];
  const docExts = ['md', 'txt', 'rst', 'json', 'yaml', 'yml', 'toml'];
  
  if (codeExts.includes(extension.toLowerCase())) {
    return <FileCode className="w-4 h-4" />;
  }
  return <FileText className="w-4 h-4" />;
};

const getExtensionColor = (extension: string) => {
  const colors: Record<string, string> = {
    'py': 'text-yellow-400',
    'ts': 'text-blue-400',
    'tsx': 'text-blue-400',
    'js': 'text-yellow-300',
    'jsx': 'text-yellow-300',
    'md': 'text-cyan-400',
    'json': 'text-green-400',
    'yaml': 'text-pink-400',
    'yml': 'text-pink-400',
  };
  return colors[extension.toLowerCase()] || 'text-zinc-400';
};

export default function CodeViewer({ isOpen, onClose, sources, initialSourceIndex = 0, repoUrl }: CodeViewerProps) {
  const [selectedIndex, setSelectedIndex] = useState(initialSourceIndex);
  const [searchQuery, setSearchQuery] = useState('');
  const [copied, setCopied] = useState(false);
  const [codeContent, setCodeContent] = useState<string>('');
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (isOpen) {
      setSelectedIndex(initialSourceIndex);
    }
  }, [isOpen, initialSourceIndex]);

  useEffect(() => {
    if (isOpen && sources[selectedIndex]) {
      const source = sources[selectedIndex];
      setIsLoading(true);
      setCodeContent(''); // Clear previous content

      api.getFileContent(repoUrl, source.filePath)
        .then(content => {
          setCodeContent(content);
        })
        .catch(err => {
          console.error(err);
          setCodeContent(`// Failed to load file content\n// Error: ${err.message}\n// Path: ${source.filePath}`);
        })
        .finally(() => {
          setIsLoading(false);
        });
    }
  }, [isOpen, selectedIndex, sources, repoUrl]);

  // 过滤文件列表
  const filteredSources = sources.filter(s => 
    s.filePath.toLowerCase().includes(searchQuery.toLowerCase()) ||
    s.fileName.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const selectedSource = sources[selectedIndex];
  
  // 构建 Markdown 代码块以便渲染
  const markdownContent = selectedSource 
    ? `\`\`\`${selectedSource.extension || 'text'}\n${codeContent}\n\`\`\``
    : '';

  const handleCopy = () => {
    if (selectedSource) {
      navigator.clipboard.writeText(selectedSource.filePath);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!isOpen) return null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 md:p-8"
      onClick={onClose}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="w-full max-w-6xl h-[85vh] bg-[#0d0d0d] border border-white/10 rounded-xl shadow-2xl flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top Header */}
        <div className="h-12 border-b border-white/10 flex items-center justify-between px-4 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
              <span className="text-purple-400">&lt;/&gt;</span>
              Code Inspector
            </div>
            <div className="h-4 w-[1px] bg-white/10 mx-2" />
            <div className="flex items-center gap-2 text-sm text-zinc-400">
              {selectedSource && (
                <>
                  <span className={getExtensionColor(selectedSource.extension)}>
                    {getFileIcon(selectedSource.extension)}
                  </span>
                  <span>{selectedSource.fileName}</span>
                </>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-white/10 rounded-lg text-zinc-400 hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 flex overflow-hidden">
          {/* Left Sidebar: Explorer */}
          <div className="w-64 border-r border-white/10 bg-[#0A0A0A] flex flex-col">
            <div className="p-3 border-b border-white/5">
              <div className="text-xs font-semibold text-zinc-500 mb-2 uppercase tracking-wider px-1">Explorer</div>
              <div className="relative">
                <Search className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-zinc-500" />
                <input
                  type="text"
                  placeholder="Search files..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-white/5 border border-white/5 rounded-md py-1.5 pl-8 pr-3 text-xs text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-purple-500/50 transition-colors"
                />
              </div>
            </div>
            <div className="flex-1 overflow-y-auto custom-scrollbar p-2 space-y-0.5">
              {filteredSources.map((source, idx) => {
                const globalIndex = sources.indexOf(source);
                const isSelected = globalIndex === selectedIndex;
                return (
                  <button
                    key={`${source.filePath}-${idx}`}
                    onClick={() => setSelectedIndex(globalIndex)}
                    className={cn(
                      "w-full flex items-center gap-2 px-2 py-1.5 rounded text-left transition-colors group",
                      isSelected 
                        ? "bg-purple-500/10 text-white" 
                        : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
                    )}
                  >
                    <span className={cn(
                      "shrink-0 transition-colors",
                      isSelected ? getExtensionColor(source.extension) : "text-zinc-600 group-hover:text-zinc-500"
                    )}>
                      {getFileIcon(source.extension)}
                    </span>
                    <span className="text-xs truncate font-mono">
                      {source.fileName}
                    </span>
                    {isSelected && (
                      <div className="ml-auto w-1.5 h-1.5 rounded-full bg-purple-500" />
                    )}
                  </button>
                );
              })}
            </div>
            {/* Status Bar */}
            <div className="p-2 border-t border-white/5 text-[10px] text-zinc-600 flex justify-between px-4">
              <span>{sources.length} files</span>
              <span>Ready</span>
            </div>
          </div>

          {/* Main Content: Code View */}
          <div className="flex-1 flex flex-col bg-[#0d0d0d] relative overflow-hidden">
            {/* Tab Bar */}
            {selectedSource && (
              <div className="h-9 border-b border-white/5 flex items-center bg-[#0A0A0A]">
                <div className="flex items-center gap-2 px-4 py-2 bg-[#0d0d0d] border-t-2 border-t-purple-500 border-r border-r-white/5 h-full">
                  <span className={cn("text-xs", getExtensionColor(selectedSource.extension))}>
                    {selectedSource.fileName}
                  </span>
                  <button 
                    onClick={onClose}
                    className="ml-2 hover:bg-white/10 rounded p-0.5"
                  >
                    <X className="w-3 h-3 text-zinc-500 hover:text-white" />
                  </button>
                </div>
              </div>
            )}

            {/* Code Area */}
            <div className="flex-1 overflow-auto custom-scrollbar p-0">
              {isLoading ? (
                <div className="flex flex-col items-center justify-center h-full text-zinc-500">
                  <Loader2 className="w-8 h-8 animate-spin mb-2 text-purple-500" />
                  <p className="text-sm">Loading content...</p>
                </div>
              ) : selectedSource ? (
                <div className="text-sm font-mono leading-relaxed">
                   {/* 这里使用 ReactMarkdown 渲染代码块，或者如果以后引入了 syntax-highlighter 可以在这里替换 */}
                   <div className="prose prose-invert max-w-none prose-pre:bg-transparent prose-pre:m-0 prose-pre:p-4 prose-code:bg-transparent prose-code:text-sm">
                     <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        rehypePlugins={[rehypeHighlight]}
                        components={{
                          pre: ({node, ...props}) => <pre {...props} className="!bg-transparent !m-0 !p-6" />,
                          code: ({node, className, children, ...props}) => {
                            const match = /language-(\w+)/.exec(className || '');
                            return (
                              <code className={cn(className, "font-mono text-sm")} {...props}>
                                {children}
                              </code>
                            );
                          }
                        }}
                      >
                        {markdownContent}
                      </ReactMarkdown>
                   </div>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-zinc-600">
                  <FileCode className="w-12 h-12 mb-4 opacity-20" />
                  <p className="text-sm">Select a file to view content</p>
                </div>
              )}
            </div>

            {/* Floating Action Buttons */}
            {selectedSource && (
              <div className="absolute top-12 right-6 flex items-center gap-2">
                 <button
                    onClick={handleCopy}
                    className="p-2 rounded-lg bg-white/5 hover:bg-white/10 border border-white/5 text-zinc-400 hover:text-white transition-all backdrop-blur-sm"
                    title="Copy Path"
                  >
                    {copied ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                  </button>
              </div>
            )}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
