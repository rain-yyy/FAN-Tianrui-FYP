/**
 * Parsing for the `sources` strings the chat backend attaches to answers.
 *
 * Observed shapes (see docker/src/chat/service.py):
 *   code:/abs/…/data/repos/<repo>/src/x.py   rag_search (absolute, category prefix)
 *   text:/abs/…/data/repos/<repo>/README.md
 *   src/x.py:120-175                         file_read (repo-relative + lines)
 *   src/x.py                                 grep_search / code_graph
 *   web:https://…                            web_search
 *
 * File paths are normalised to repo-relative form, which is what
 * `POST /file/content` expects.
 */

interface LineRange {
  start: number;
  end: number;
}

export interface FileCitation {
  kind: "file";
  raw: string;
  /** Repo-relative path, e.g. `src/maya/core.py`. */
  path: string;
  fileName: string;
  extension: string;
  lines?: LineRange;
}

export interface WebCitation {
  kind: "web";
  raw: string;
  url: string;
}

type Citation = FileCitation | WebCitation;

const CATEGORY_PREFIX = /^(code|text|doc):/i;
const LINE_SUFFIX = /:(\d+)(?:-(\d+))?$/;
// Absolute checkout paths: …/data/repos/<repo>/ locally and in the container,
// or any …/repos/<repo>/ as a fallback.
const REPO_STORE_PREFIXES = [
  /^.*?\/data\/repos\/[^/]+\//,
  /^.*?\/repos\/[^/]+\//,
];

/** Turns an absolute checkout path or a relative path into `a/b/c.ext`. */
function normalizeRepoPath(rawPath: string): string {
  let path = rawPath.trim().replace(/\\/g, "/");
  if (path.startsWith("/") || /^[A-Za-z]:\//.test(path)) {
    for (const prefix of REPO_STORE_PREFIXES) {
      if (prefix.test(path)) {
        path = path.replace(prefix, "");
        break;
      }
    }
  }
  return path.replace(/^(\.\/)+/, "").replace(/^\/+/, "");
}

function parseCitation(raw: string): Citation | null {
  const source = raw.trim();
  if (!source || source.toLowerCase() === "unknown") return null;
  if (source.toLowerCase().endsWith(":unknown")) return null;

  if (source.toLowerCase().startsWith("web:")) {
    const url = source.slice(4).trim();
    return url ? { kind: "web", raw, url } : null;
  }

  let rest = source.replace(CATEGORY_PREFIX, "");
  let lines: LineRange | undefined;
  const lineMatch = rest.match(LINE_SUFFIX);
  if (lineMatch) {
    const start = Number.parseInt(lineMatch[1], 10);
    const end = lineMatch[2] ? Number.parseInt(lineMatch[2], 10) : start;
    lines = { start, end: Math.max(start, end) };
    rest = rest.slice(0, lineMatch.index);
  }

  const path = normalizeRepoPath(rest);
  if (!path) return null;
  const fileName = path.split("/").pop() ?? path;
  const dot = fileName.lastIndexOf(".");
  const extension = dot > 0 ? fileName.slice(dot + 1).toLowerCase() : "";
  return { kind: "file", raw, path, fileName, extension, lines };
}

function citationKey(citation: Citation): string {
  if (citation.kind === "web") return `web:${citation.url}`;
  const { lines } = citation;
  return lines ? `${citation.path}:${lines.start}-${lines.end}` : citation.path;
}

/** `src/x.py:12-40`, `src/x.py:12`, or `src/x.py`. */
export function formatFileCitation(citation: FileCitation): string {
  const { lines } = citation;
  if (!lines) return citation.path;
  return lines.start === lines.end
    ? `${citation.path}:${lines.start}`
    : `${citation.path}:${lines.start}-${lines.end}`;
}

interface ParsedCitations {
  /** Ordered, de-duplicated file citations. Indexes are stable for a message. */
  files: FileCitation[];
  links: WebCitation[];
}

/**
 * Parses a message's `sources` once. Every consumer (chips, Code Inspector)
 * must index into the same `files` array, so a click always opens the file
 * that was clicked.
 */
export function parseCitations(
  sources: readonly string[] | undefined,
): ParsedCitations {
  const files: FileCitation[] = [];
  const links: WebCitation[] = [];
  const seen = new Set<string>();
  for (const raw of sources ?? []) {
    const citation = parseCitation(raw);
    if (!citation) continue;
    const key = citationKey(citation);
    if (seen.has(key)) continue;
    seen.add(key);
    if (citation.kind === "file") files.push(citation);
    else links.push(citation);
  }
  return { files, links };
}
