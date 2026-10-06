/**
 * Wiki table of contents with stable chapter codes.
 *
 * Top-level chapters get D1…Dn and their pages Dn.m. The code is the shared
 * name for a chapter across the TOC, margin notes, chips and the URL
 * (`?d=D3.2`).
 */

export interface TocNode {
  id: string;
  title: string;
  /** Repo-relative files the generator attached to this page. */
  files: string[];
  code: string;
  /** Top-level chapter this node belongs to (itself for a chapter). */
  chapterId: string;
  children: TocNode[];
}

export interface WikiToc {
  title: string;
  nodes: TocNode[];
  byId: Map<string, TocNode>;
  byCode: Map<string, TocNode>;
  /** Depth-first reading order. */
  order: TocNode[];
  /** Repo-relative path → every page that lists it, in reading order. */
  byFile: Map<string, TocNode[]>;
}

interface RawTocItem {
  id?: unknown;
  section_id?: unknown;
  filename?: unknown;
  title?: unknown;
  name?: unknown;
  files?: unknown;
  children?: unknown;
}

const asString = (value: unknown) =>
  typeof value === "string" && value.trim() ? value.trim() : null;

function rawItems(data: unknown): RawTocItem[] {
  if (Array.isArray(data)) return data as RawTocItem[];
  if (data && typeof data === "object") {
    const record = data as { toc?: unknown; pages?: unknown };
    if (Array.isArray(record.toc)) return record.toc as RawTocItem[];
    if (Array.isArray(record.pages)) return record.pages as RawTocItem[];
  }
  return [];
}

function buildNodes(
  items: RawTocItem[],
  prefix: string,
  top: { chapterId: string } | null,
): TocNode[] {
  const nodes: TocNode[] = [];
  for (const item of items) {
    const id =
      asString(item.id) ??
      asString(item.section_id) ??
      asString(item.filename)?.replace(/\.json$/, "");
    if (!id) continue;
    const position = nodes.length + 1;
    const code = top ? `${prefix}.${position}` : `D${position}`;
    const own = top ?? { chapterId: id };
    nodes.push({
      id,
      title: asString(item.title) ?? asString(item.name) ?? id,
      files: Array.isArray(item.files)
        ? item.files.filter((f): f is string => typeof f === "string")
        : [],
      code,
      chapterId: own.chapterId,
      children: Array.isArray(item.children)
        ? buildNodes(item.children as RawTocItem[], code, own)
        : [],
    });
  }
  return nodes;
}

/** Parses `wiki_structure.json` (`{title, toc: [...]}` or a bare array). */
export function parseWikiToc(data: unknown): WikiToc {
  const nodes = buildNodes(rawItems(data), "", null);
  const byId = new Map<string, TocNode>();
  const byCode = new Map<string, TocNode>();
  const order: TocNode[] = [];
  const byFile = new Map<string, TocNode[]>();
  const walk = (list: TocNode[]) => {
    for (const node of list) {
      byId.set(node.id, node);
      byCode.set(node.code, node);
      order.push(node);
      for (const file of node.files) {
        const pages = byFile.get(file);
        if (pages) pages.push(node);
        else byFile.set(file, [node]);
      }
      walk(node.children);
    }
  };
  walk(nodes);
  const title =
    data && typeof data === "object" && !Array.isArray(data)
      ? asString((data as { title?: unknown }).title)
      : null;
  return { title: title ?? "", nodes, byId, byCode, order, byFile };
}
