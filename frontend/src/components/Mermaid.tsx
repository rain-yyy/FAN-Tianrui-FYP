'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { AlertTriangle, RefreshCw, Code2, X, ZoomIn } from 'lucide-react';

interface MermaidProps {
  chart: string;
  isStreaming?: boolean;
}

// Ensure mermaid is initialized only once (client-side).
let mermaidInitialized = false;
let mermaidModule: typeof import('mermaid') | null = null;

// Basic Mermaid syntax checks before render.
const validateMermaidSyntax = (code: string): { valid: boolean; error?: string } => {
  if (!code || !code.trim()) {
    return { valid: false, error: 'Empty diagram code' };
  }

  const trimmed = code.trim();
  
  // Check for incomplete code blocks (streaming artifacts)
  if (trimmed.includes('```') && !trimmed.endsWith('```')) {
    return { valid: false, error: 'Incomplete code block' };
  }
  
  // Check for basic diagram type declaration
  const validStarters = [
    'graph', 'flowchart', 'sequenceDiagram', 'classDiagram', 
    'stateDiagram', 'erDiagram', 'gantt', 'pie', 'journey',
    'mindmap', 'timeline', 'gitGraph', 'quadrantChart', 'sankey',
    'xychart', 'block-beta'
  ];
  
  const firstLine = trimmed.split('\n')[0].toLowerCase().trim();
  const hasValidStarter = validStarters.some(s => firstLine.startsWith(s.toLowerCase()));
  
  if (!hasValidStarter) {
    return { valid: false, error: 'Missing diagram type declaration' };
  }
  
  // Check for balanced brackets
  const openBrackets = (code.match(/\[/g) || []).length;
  const closeBrackets = (code.match(/\]/g) || []).length;
  if (openBrackets !== closeBrackets) {
    return { valid: false, error: 'Unbalanced brackets' };
  }
  
  // Check for balanced parentheses
  const openParens = (code.match(/\(/g) || []).length;
  const closeParens = (code.match(/\)/g) || []).length;
  if (openParens !== closeParens) {
    return { valid: false, error: 'Unbalanced parentheses' };
  }
  
  return { valid: true };
};

// Clean mermaid code from markdown artifacts
const cleanMermaidCode = (code: string): string => {
  let cleaned = code.trim();
  
  // Remove markdown code fences
  if (cleaned.startsWith('```mermaid')) {
    cleaned = cleaned.slice(10);
  } else if (cleaned.startsWith('```')) {
    cleaned = cleaned.slice(3);
  }
  
  if (cleaned.endsWith('```')) {
    cleaned = cleaned.slice(0, -3);
  }
  
  return cleaned.trim();
};

export default function Mermaid({ chart, isStreaming = false }: MermaidProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [svg, setSvg] = useState<string>('');
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [showSource, setShowSource] = useState(false);
  const [retryCount, setRetryCount] = useState(0);
  const [isZoomed, setIsZoomed] = useState(false);
  const renderAttemptRef = useRef(0);

  // Close zoom on Escape key
  useEffect(() => {
    if (!isZoomed) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsZoomed(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isZoomed]);

  // Clean and validate chart code
  const { cleanedChart, isValid, validationError } = useMemo(() => {
    const cleaned = cleanMermaidCode(chart || '');
    const validation = validateMermaidSyntax(cleaned);
    return {
      cleanedChart: cleaned,
      isValid: validation.valid,
      validationError: validation.error,
    };
  }, [chart]);

  // Load and initialize mermaid once on the client.
  useEffect(() => {
    // Already initialized: mark ready.
    if (mermaidInitialized && mermaidModule) {
      setIsLoading(false);
      return;
    }

    if (typeof window === 'undefined') return;

    const loadMermaid = async () => {
      try {
        // Dynamic import so mermaid only loads in the browser.
        if (!mermaidModule) {
          mermaidModule = await import('mermaid');
        }
        
        const mermaid = mermaidModule.default;
        
        // One-time initialize.
        if (!mermaidInitialized) {
          const currentTheme = document.documentElement.getAttribute('data-theme') === 'light' ? 'default' : 'dark';
          mermaid.initialize({
            startOnLoad: false,
            suppressErrorRendering: true,
            theme: currentTheme,
            securityLevel: 'loose',
            fontFamily: 'var(--font-sans)',
            flowchart: {
              htmlLabels: true,
              curve: 'basis',
            },
          });
          mermaid.parseError = (err) => {
             console.error('Mermaid parse error:', err);
          };
          mermaidInitialized = true;
        }
        
        setIsLoading(false);
      } catch (error) {
        console.error('Mermaid initialization error:', error);
        setIsError(true);
        setErrorMessage('Failed to load diagram library');
        setIsLoading(false);
      }
    };

    loadMermaid();
  }, []);

  // Patch common Mermaid label issues from streamed output.
  const fixMermaidSyntax = (mermaidCode: string): string => {
    let fixed = mermaidCode;
    
    // Quote labels that contain @.
    fixed = fixed.replace(/(\w+)\[([^\]]*@[^\]]*)\]/g, (match, nodeId, label) => {
      const cleanLabel = label.replace(/^["']|["']$/g, '');
      return `${nodeId}["${cleanLabel}"]`;
    });
    
    // Escape HTML-like chars inside labels.
    fixed = fixed.replace(/(\w+)\[([^\]]*[<>&][^\]]*)\]/g, (match, nodeId, label) => {
      const cleanLabel = label
        .replace(/^["']|["']$/g, '')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return `${nodeId}["${cleanLabel}"]`;
    });
    
    return fixed;
  };

  // Render diagram when chart / init state changes.
  useEffect(() => {
    // Don't render if streaming or invalid
    if (isStreaming && !isValid) {
      setIsLoading(false);
      return;
    }

    if (!cleanedChart || !mermaidInitialized || !mermaidModule) {
      if (!cleanedChart) {
        setIsLoading(false);
      }
      return;
    }

    // Skip rendering if validation fails
    if (!isValid) {
      setIsLoading(false);
      setIsError(true);
      setErrorMessage(validationError || 'Invalid diagram syntax');
      return;
    }

    const currentAttempt = ++renderAttemptRef.current;

    const renderChart = async () => {
      try {
        setIsError(false);
        setErrorMessage('');
        setIsLoading(true);
        
        setSvg('');
        
        const mermaid = mermaidModule!.default;
        const id = `mermaid-${Math.random().toString(36).substring(2, 11)}`;
        const chartToRender = fixMermaidSyntax(cleanedChart);
        
        const { svg: renderedSvg } = await mermaid.render(id, chartToRender);
        
        // Only update if this is still the latest attempt
        if (currentAttempt === renderAttemptRef.current) {
          setSvg(renderedSvg);
          setIsLoading(false);
          setRetryCount(0);
        }
      } catch (error: unknown) {
        console.error('Mermaid render error:', error);
        console.debug('Chart content:', cleanedChart);
        
        // Try aggressive fix
        try {
          const mermaid = mermaidModule!.default;
          const id = `mermaid-retry-${Math.random().toString(36).substring(2, 11)}`;
          
          // More aggressive fixes
          let aggressiveFix = cleanedChart
            // Replace @ in labels
            .replace(/(\w+)\[([^\]]+)\]/g, (match, nodeId, label) => {
              if (label.includes('@') || label.includes('<') || label.includes('>')) {
                const cleanLabel = label
                  .replace(/@/g, '(at)')
                  .replace(/</g, '(lt)')
                  .replace(/>/g, '(gt)');
                return `${nodeId}["${cleanLabel}"]`;
              }
              return match;
            });
          
          const { svg: renderedSvg } = await mermaid.render(id, aggressiveFix);
          
          if (currentAttempt === renderAttemptRef.current) {
            setSvg(renderedSvg);
            setIsLoading(false);
          }
        } catch (retryError) {
          console.error('Mermaid retry render error:', retryError);
          if (currentAttempt === renderAttemptRef.current) {
            setIsError(true);
            setErrorMessage(
              retryError instanceof Error 
                ? retryError.message.slice(0, 100) 
                : 'Failed to render diagram'
            );
            setIsLoading(false);
          }
        }
      }
    };

    // Add delay to ensure DOM is ready and debounce rapid changes
    const timer = setTimeout(() => {
      renderChart();
    }, isStreaming ? 500 : 100);

    return () => clearTimeout(timer);
  }, [cleanedChart, isValid, validationError, isStreaming]);

  // Retry handler
  const handleRetry = () => {
    setRetryCount(prev => prev + 1);
    setIsError(false);
    setErrorMessage('');
    setSvg('');
    setIsLoading(true);
    
    // Force re-render by incrementing attempt counter
    renderAttemptRef.current++;
  };

  // Streaming state - show placeholder
  if (isStreaming && !isValid) {
    return (
      <div className="mermaid-container flex justify-center items-center p-6 bg-stone-50 rounded-xl border border-stone-200 min-h-[150px]">
        <div className="text-stone-600 text-sm flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-sky-500 animate-pulse" />
          Generating diagram...
        </div>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="mermaid-container flex justify-center items-center p-6 bg-stone-50 rounded-xl border border-stone-200 min-h-[200px]">
        <div className="text-stone-600 text-sm">Rendering diagram...</div>
      </div>
    );
  }

  if (isError) {
    // Show degraded view with source code and retry option
    return (
      <div className="mermaid-container p-4 bg-amber-50 rounded-xl border border-amber-200">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 text-amber-800 text-sm">
            <AlertTriangle className="w-4 h-4" />
            <span>Diagram render failed</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSource(!showSource)}
              className="flex items-center gap-1.5 px-2 py-1 text-xs text-stone-600 hover:text-stone-900 bg-white border border-stone-200 rounded-md transition-colors"
              aria-label="Toggle source code"
            >
              <Code2 className="w-3.5 h-3.5" />
              {showSource ? 'Hide source' : 'View source'}
            </button>
            <button
              onClick={handleRetry}
              className="flex items-center gap-1.5 px-2 py-1 text-xs text-sky-800 hover:text-sky-900 bg-sky-100 rounded-md transition-colors border border-sky-200"
              aria-label="Retry rendering"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              Retry
            </button>
          </div>
        </div>
        
        {showSource && (
          <div className="mt-2">
            <pre className="p-3 bg-white rounded-lg text-xs text-stone-700 border border-stone-200 overflow-x-auto max-h-[200px] overflow-y-auto">
              <code>{cleanedChart}</code>
            </pre>
            {errorMessage && (
              <p className="mt-2 text-xs text-amber-800">
                Error: {errorMessage}
              </p>
            )}
          </div>
        )}
      </div>
    );
  }

  if (!svg) {
    return null;
  }

  return (
    <>
      <div
        ref={ref}
        className="mermaid-container relative flex justify-center p-6 bg-stone-50 rounded-xl overflow-x-auto border border-stone-200 cursor-zoom-in group"
        onClick={() => setIsZoomed(true)}
        title="Click to enlarge"
        dangerouslySetInnerHTML={{ __html: svg }}
      />
      <div className="flex justify-end mt-1">
        <button
          onClick={() => setIsZoomed(true)}
          className="flex items-center gap-1 text-[11px] text-stone-400 hover:text-stone-600 transition-colors"
          aria-label="Enlarge diagram"
        >
          <ZoomIn className="w-3 h-3" />
          <span>Enlarge</span>
        </button>
      </div>
      {isZoomed && (
        <div
          className="fixed inset-0 z-[9999] bg-black/75 flex items-center justify-center p-6 backdrop-blur-sm"
          onClick={() => setIsZoomed(false)}
        >
          <div
            className="relative max-w-[92vw] max-h-[88vh] overflow-auto bg-white rounded-2xl p-10 shadow-2xl [&_svg]:!max-width-none [&_svg]:!width-auto [&_svg]:!height-auto"
            style={{ minWidth: '60vw' }}
            onClick={e => e.stopPropagation()}
          >
            {/* Reset SVG dimensions so it scales naturally in the modal */}
            <style>{`
              .mermaid-zoom-inner svg {
                width: 100% !important;
                height: auto !important;
                max-width: 100% !important;
              }
            `}</style>
            <div
              className="mermaid-zoom-inner"
              dangerouslySetInnerHTML={{ __html: svg }}
            />
          </div>
          <button
            className="absolute top-4 right-4 p-2 rounded-full bg-white/20 text-white hover:bg-white/30 transition-colors"
            onClick={() => setIsZoomed(false)}
            aria-label="Close enlarged diagram"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      )}
    </>
  );
}
