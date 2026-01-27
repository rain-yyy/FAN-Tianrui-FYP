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
    // 如果已经初始化，直接设置加载完成
    if (mermaidInitialized && mermaidModule) {
      setIsLoading(false);
      return;
    }

    if (typeof window === 'undefined') return;

    const loadMermaid = async () => {
      try {
        // 动态导入 mermaid，确保只在客户端加载
        if (!mermaidModule) {
          mermaidModule = await import('mermaid');
        }
        
        const mermaid = mermaidModule.default;
        
        // 只在未初始化时初始化
        if (!mermaidInitialized) {
          mermaid.initialize({
            startOnLoad: false,
            theme: 'dark',
            securityLevel: 'loose',
            fontFamily: 'var(--font-sans)',
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

  // 修复 Mermaid 语法中的常见问题
  const fixMermaidSyntax = (mermaidCode: string): string => {
    let fixed = mermaidCode;
    
    // 修复节点标签中的 @ 符号问题
    // Mermaid 中，如果节点标签包含特殊字符（如 @），需要用引号包裹
    // 匹配类似 H[@follow/web] 或 B[follow@rss3.io] 的模式
    fixed = fixed.replace(/(\w+)\[([^\]]*@[^\]]*)\]/g, (match, nodeId, label) => {
      // 如果标签已经用引号包裹，先移除引号
      const cleanLabel = label.replace(/^["']|["']$/g, '');
      // 用双引号包裹整个标签，保留 @ 符号
      return `${nodeId}["${cleanLabel}"]`;
    });
    
    return fixed;
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
        
        // 清理之前的 SVG
        setSvg('');
        
        const mermaid = mermaidModule!.default;
        
        // 生成唯一的 ID
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
        
        // 尝试修复常见的语法问题
        let chartToRender = fixMermaidSyntax(chart);
        
        // 渲染图表
        const { svg: renderedSvg } = await mermaid.render(id, chartToRender);
        setSvg(renderedSvg);
        setIsLoading(false);
      } catch (error: any) {
        console.error('Mermaid render error:', error);
        console.error('Original chart content:', chart);
        
        // 如果修复后仍然失败，尝试更激进的修复
        try {
          const mermaid = mermaidModule!.default;
          const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
          
          // 更激进的修复：将所有节点标签中的 @ 替换为 (at)
          let aggressiveFix = chart.replace(/(\w+)\[([^\]]+)\]/g, (match, nodeId, label) => {
            if (label.includes('@')) {
              return `${nodeId}["${label.replace(/@/g, '(at)')}"]`;
            }
            return match;
          });
          
          const { svg: renderedSvg } = await mermaid.render(id, aggressiveFix);
          setSvg(renderedSvg);
          setIsLoading(false);
        } catch (retryError) {
          console.error('Mermaid retry render error:', retryError);
          setIsError(true);
          setIsLoading(false);
        }
      }
    };

    // 添加小延迟确保 DOM 已准备好
    const timer = setTimeout(() => {
      renderChart();
    }, 100);

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
