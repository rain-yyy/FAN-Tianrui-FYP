/**
 * Shared per-tool wording, used by the live step list (LiveStepFlow) and the
 * persisted trace and notes (MessageItem). Reads the generic `arguments`
 * object each chat tool call carries (see docker/src/chat/tools/schemas.py
 * for the canonical argument names per tool).
 */

type Args = Record<string, unknown> | undefined;

const fileName = (path: unknown) =>
  String(path ?? "")
    .split("/")
    .pop() ?? "";
const clip = (value: unknown, max: number) => {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max)}…` : text;
};

const DESCRIPTIONS: Record<string, (args: Args) => string> = {
  rag_search: (a) =>
    a?.query ? `Search docs: ${clip(a.query, 40)}` : "Search project docs",
  code_graph: (a) => {
    const op = String(a?.operation ?? "");
    const symbol = fileName(a?.symbol_name ?? a?.file_path);
    if (op === "find_definition" && symbol) return `Find definition: ${symbol}`;
    if (op === "find_callers" && symbol) return `Find callers: ${symbol}`;
    if (op === "find_callees" && symbol) return `Find callees: ${symbol}`;
    if (op === "get_all_symbols") return "List all symbols";
    return "Analyze code structure";
  },
  file_read: (a) => `Read file: ${fileName(a?.file_path) || "unknown"}`,
  repo_map: () => "Scan repository layout",
  grep_search: (a) =>
    a?.pattern ? `Grep: ${clip(a.pattern, 40)}` : "Lexical repo search",
  web_search: (a) =>
    a?.query ? `Web search: ${clip(a.query, 40)}` : "Search the web",
};

export const getToolDescription = (tool: string, args?: Args): string =>
  DESCRIPTIONS[tool]?.(args) ?? `Run ${tool}`;

const LABELS: Record<string, string> = {
  rag_search: "search",
  file_read: "read",
  code_graph: "graph",
  grep_search: "grep",
  repo_map: "map",
  web_search: "web",
};

/** Short mono label for a tool, used in notes and traces. */
export const toolLabel = (tool: string): string => LABELS[tool] ?? tool;
