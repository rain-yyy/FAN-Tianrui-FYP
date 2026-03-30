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
    'py': 'text-amber-700',
    'ts': 'text-sky-700',
    'tsx': 'text-sky-700',
    'js': 'text-amber-700',
    'jsx': 'text-amber-700',
    'md': 'text-teal-700',
    'json': 'text-emerald-700',
    'yaml': 'text-rose-700',
    'yml': 'text-rose-700',
  };
  return colors[extension.toLowerCase()] || 'text-stone-600';
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
        "bg-white border-stone-200 hover:bg-stone-50 hover:border-stone-300 shadow-sm",
        "text-stone-800 hover:text-stone-900"
      )}
    >
      <div className={getExtensionColor(source.extension)}>
        {getFileIcon(source.extension)}
      </div>
      <span className="font-mono truncate max-w-[150px]">{source.fileName}</span>
      {source.lineRange && (
        <span className="text-stone-500 font-mono text-[10px]">
          :{source.lineRange.start}
        </span>
      )}
    </motion.button>
  );
};

export const SourcesPanel = ({ sources, onSourceClick, compact = true }: SourcesPanelProps) => {
  const parsedSources = useMemo(
    () => sources
      .filter((s) => s && s.toLowerCase() !== 'unknown' && !s.endsWith(':unknown'))
      .map(parseSource),
    [sources]
  );
  
  if (sources.length === 0) {
    if (compact) return null;
    return (
      <div className="flex flex-col items-center justify-center py-8 text-center">
        <p className="text-sm text-stone-600">No sources</p>
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
