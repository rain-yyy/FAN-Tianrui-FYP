/**
 * Shared per-tool description builder, used by both the live in-progress
 * step UI (LiveStepFlow) and the persisted trajectory panel (MessageItem).
 * Reads from the generic `arguments` object each chat tool call now carries
 * (see docker/src/chat/tools/schemas.py for the canonical arg names per tool).
 */
export const getToolDescription = (tool: string, args?: Record<string, unknown>): string => {
  const descriptions: Record<string, (args?: Record<string, unknown>) => string> = {
    'rag_search': (a) => a?.query ? `Search docs: ${String(a.query).slice(0, 30)}...` : 'Search project docs',
    'code_graph': (a) => {
      const op = String(a?.operation || '');
      const symbol = String(a?.symbol_name || a?.file_path || '').split('/').pop();
      if (op === 'find_definition' && symbol) return `Find definition: ${symbol}`;
      if (op === 'find_callers' && symbol) return `Find callers: ${symbol}`;
      if (op === 'find_callees' && symbol) return `Find callees: ${symbol}`;
      if (op === 'get_all_symbols') return 'List all symbols';
      return 'Analyze code structure';
    },
    'file_read': (a) => {
      const path = String(a?.file_path || '');
      const fileName = path.split('/').pop() || path;
      return `Read file: ${fileName}`;
    },
    'repo_map': () => 'Scan repository layout',
    'grep_search': (a) => {
      const p = String(a?.pattern || '').slice(0, 40);
      return p ? `Grep: ${p}${p.length >= 40 ? '…' : ''}` : 'Lexical repo search';
    },
    'web_search': (a) => a?.query ? `Web search: ${String(a.query).slice(0, 30)}...` : 'Search the web',
  };
  return descriptions[tool]?.(args) || `Run ${tool}`;
};
