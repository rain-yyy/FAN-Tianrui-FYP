'use client';

import React, { useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import { 
  FileText, 
  FileCode, 
} from 'lucide-react';
import { cn } from '@/lib/utils';

export interface ParsedSource {
  raw: string;
  filePath: string;
  fileName: string;
  extension: string;
  lineRange?: { start: number; end: number };
  symbolName?: string;
  category?: string;
}

interface SourcesPanelProps {
  sources: string[];
  onSourceClick?: (source: ParsedSource, index: number) => void;
  compact?: boolean;
}

export const normalizeFilePath = (rawPath: string): string => {
  let path = rawPath;
  
  const prefixPatterns = [
    /^\/[^/]+\/[^/]+\/Documents\/GitHub\/[^/]+\//,
    /^\/[^/]+\/[^/]+\/repos\/[^/]+\//,
    /^\/tmp\/[^/]+\//,
    /^\/var\/[^/]+\/[^/]+\//,
    /^[A-Za-z]:\\[^\\]+\\[^\\]+\\repos\\[^\\]+\\/,
    /^\/var\/folders\/[^/]+\/[^/]+\/T\/[^/]+\//, // macOS temp dir
    /\/T\/[^/]+\//, // Generic temp dir pattern containing /T/
  ];
  
  for (const pattern of prefixPatterns) {
    path = path.replace(pattern, '');
  }
  
  path = path.replace(/\\/g, '/');
  
  if (path.startsWith('./')) {
    path = path.slice(2);
  }
  if (path.startsWith('/')) {
    path = path.slice(1);
  }
  
  return path;
};

export const parseSource = (source: string): ParsedSource => {
  let filePath = source;
  let category: string | undefined;
  let lineRange: { start: number; end: number } | undefined;
  let symbolName: string | undefined;
  
  if (source.includes(':') && !source.startsWith('/')) {
    const colonIndex = source.indexOf(':');
    const prefix = source.substring(0, colonIndex);
    if (['code', 'text', 'doc'].includes(prefix.toLowerCase())) {
      category = prefix;
      filePath = source.substring(colonIndex + 1);
    }
  }
  
  const lineMatch = filePath.match(/:(\d+)(?:-(\d+))?$/);
  if (lineMatch) {
    lineRange = {
      start: parseInt(lineMatch[1], 10),
      end: lineMatch[2] ? parseInt(lineMatch[2], 10) : parseInt(lineMatch[1], 10)
    };
    filePath = filePath.replace(/:(\d+)(?:-(\d+))?$/, '');
  }
  
  const symbolMatch = filePath.match(/:([A-Za-z_][A-Za-z0-9_]*)$/);
  if (symbolMatch && !symbolMatch[1].match(/^\d+$/)) {
    symbolName = symbolMatch[1];
    filePath = filePath.replace(/:([A-Za-z_][A-Za-z0-9_]*)$/, '');
  }
  
  filePath = normalizeFilePath(filePath);
  
  const fileName = filePath.split('/').pop() || filePath;
  const extension = fileName.includes('.') ? fileName.split('.').pop() || '' : '';
  
  return {
    raw: source,
    filePath,
    fileName,
    extension,
    lineRange,
    symbolName,
    category
  };
};

const getFileIcon = (extension: string) => {
  const codeExts = ['py', 'ts', 'tsx', 'js', 'jsx', 'java', 'cpp', 'c', 'go', 'rs', 'rb', 'php'];
  const docExts = ['md', 'txt', 'rst', 'json', 'yaml', 'yml', 'toml'];
  
  if (codeExts.includes(extension.toLowerCase())) {
    return <FileCode className="w-3.5 h-3.5" />;
  }
  return <FileText className="w-3.5 h-3.5" />;
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

const SourceChip = ({ 
  source, 
  onClick 
}: { 
  source: ParsedSource; 
  onClick: () => void;
}) => {
  return (
    <motion.button
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 px-2.5 py-1.5 rounded-md border text-xs transition-all",
        "bg-white/5 border-white/10 hover:bg-white/10 hover:border-white/20",
        "text-zinc-300 hover:text-white"
      )}
    >
      <div className={getExtensionColor(source.extension)}>
        {getFileIcon(source.extension)}
      </div>
      <span className="font-mono truncate max-w-[150px]">{source.fileName}</span>
      {source.lineRange && (
        <span className="text-zinc-500 font-mono text-[10px]">
          :{source.lineRange.start}
        </span>
      )}
    </motion.button>
  );
};

export const SourcesPanel = ({ sources, onSourceClick, compact = true }: SourcesPanelProps) => {
  const parsedSources = useMemo(() => sources.map(parseSource), [sources]);
  
  if (sources.length === 0) {
    if (compact) return null;
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <p className="text-sm text-zinc-500">暂无引用来源</p>
      </div>
    );
  }
  
  return (
    <div className="flex flex-wrap gap-2">
      {parsedSources.map((source, index) => (
        <SourceChip 
          key={`${source.filePath}-${index}`}
          source={source}
          onClick={() => onSourceClick?.(source, index)}
        />
      ))}
    </div>
  );
};

export default SourcesPanel;
