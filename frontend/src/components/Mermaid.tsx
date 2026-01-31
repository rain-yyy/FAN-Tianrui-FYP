'use client';

import React, { useEffect, useRef, useState } from 'react';

interface MermaidProps {
  chart: string;
}

// 全局标志确保 mermaid 只初始化一次
let mermaidInitialized = false;
let mermaidModule: typeof import('mermaid') | null = null;

export default function Mermaid({ chart }: MermaidProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>('');
  const [isError, setIsError] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  // 动态加载并初始化 mermaid（只执行一次）
  useEffect(() => {
    if (mermaidInitialized && mermaidModule) {
      setIsLoading(false);
      return;
    }

    if (typeof window === 'undefined') return;

    const loadMermaid = async () => {
      try {
        if (!mermaidModule) {
          mermaidModule = await import('mermaid');
        }
        
        const mermaid = mermaidModule.default;
        
        if (!mermaidInitialized) {
          mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            securityLevel: 'loose',
            fontFamily: 'var(--font-sans)',
            suppressErrorRendering: true,
          });
          mermaidInitialized = true;
        }
        
        setIsLoading(false);
      } catch (error) {
        console.error('Mermaid initialization error:', error);
        setIsError(true);
        setIsLoading(false);
      }
    };

    loadMermaid();
  }, []);

  // 预处理 mermaid 代码
  const prepareChart = (code: string): string => {
    let prepared = code.trim();
    // 处理 JSON 中字面上的 \\n（未被解析的转义换行符）
    prepared = prepared.replace(/\\n/g, '\n');
    
    // 处理节点标签中的特殊字符：/ ( ) @ 等需要用双引号包裹
    // 匹配 NodeId[Label] 或 NodeId(Label) 等形式
    prepared = prepared.replace(/(\w+)(\[|\(|\{)([^\]\)\}]+)(\]|\)|\})/g, (match, nodeId, openBracket, label, closeBracket) => {
      // 如果标签已经用引号包裹，直接返回
      if (/^["'].*["']$/.test(label.trim())) {
        return match;
      }
      // 如果标签包含特殊字符，用双引号包裹
      if (/[\/\(\)@#&<>]/.test(label)) {
        return `${nodeId}${openBracket}"${label}"${closeBracket}`;
      }
      return match;
    });
    
    return prepared;
  };

  // 渲染图表
  useEffect(() => {
    if (!chart || !mermaidInitialized || !mermaidModule) {
      if (!chart) {
        setIsLoading(false);
      }
      return;
    }

    const renderChart = async () => {
      try {
        setIsError(false);
        setIsLoading(true);
        setSvg('');
        
        const mermaid = mermaidModule!.default;
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
        
        const preparedChart = prepareChart(chart);
        const { svg: renderedSvg } = await mermaid.render(id, preparedChart);
        setSvg(renderedSvg);
        setIsLoading(false);
      } catch (error) {
        console.error('Mermaid render error:', error);
        setIsError(true);
        setIsLoading(false);
      }
    };

    const timer = setTimeout(renderChart, 100);
    return () => clearTimeout(timer);
  }, [chart]);

  if (isLoading) {
    return (
      <div className="mermaid-container flex justify-center items-center p-6 bg-secondary/30 rounded-xl border border-white/5 min-h-[200px]">
        <div className="text-muted-foreground text-sm">正在加载图表...</div>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4 border border-red-500/20 text-red-400 text-sm rounded bg-red-500/10">
        <div className="font-semibold mb-2">图表渲染失败</div>
        <details className="text-xs mt-2">
          <summary className="cursor-pointer">查看错误详情</summary>
          <pre className="mt-2 p-2 bg-black/20 rounded overflow-auto max-h-32">
            {chart}
          </pre>
        </details>
      </div>
    );
  }

  if (!svg) {
    return null;
  }

  return (
    <div 
      ref={ref}
      className="mermaid-container flex justify-center p-6 bg-secondary/30 rounded-xl overflow-x-auto border border-white/5"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
