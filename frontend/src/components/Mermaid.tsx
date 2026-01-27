'use client';

import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

interface MermaidProps {
  chart: string;
}

export default function Mermaid({ chart }: MermaidProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>('');
  const [isError, setIsError] = useState(false);

  useEffect(() => {
    mermaid.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'loose',
      fontFamily: 'var(--font-sans)',
    });
  }, []);

  useEffect(() => {
    if (!chart || !ref.current) return;

    const renderChart = async () => {
      try {
        setIsError(false);
        const id = `mermaid-${Math.random().toString(36).substr(2, 9)}`;
        const { svg } = await mermaid.render(id, chart);
        setSvg(svg);
      } catch (error) {
        console.error('Mermaid render error:', error);
        setIsError(true);
      }
    };

    renderChart();
  }, [chart]);

  if (isError) return <div className="p-4 border border-red-500/20 text-red-400 text-sm rounded bg-red-500/10">Failed to render diagram</div>;

  return (
    <div 
      className="mermaid-container flex justify-center p-6 bg-secondary/30 rounded-xl overflow-x-auto border border-white/5"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
